#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
STATE_FILE = Path('/var/lib/backup-recovery/state.json')
ACTION_STATE_FILE = Path('/var/lib/backup-recovery/action-state.json')
DOTFILES_GIT = HOME / '.dotfiles'
DOTFILES_CACHE = HOME / '.cache/backup-recovery/dotfiles-state.json'
UI_CONTRACT = '1.6.0'
SCHEMA_VERSION = 2
STATE_STALE_SECONDS = 1800

EXTERNAL_BUSY_UNITS = {
    'external-backup': 'restic-system-backup.service',
    'external-maintenance': 'restic-maintenance.service',
}

ACTION_UNITS = {
    'backup': 'backup-recovery-backup.service',
    'timeshift': 'backup-recovery-timeshift.service',
    'restic-check': 'backup-recovery-restic-check.service',
    'restore-test': 'backup-recovery-restore-test.service',
    'smart-short': 'backup-recovery-smart-short.service',
    'smart-long': 'backup-recovery-smart-long.service',
    'mount': 'backup-recovery-mount.service',
    'eject': 'backup-recovery-eject.service',
    'refresh-manifests': 'backup-recovery-refresh-manifests.service',
}


def emit(payload: dict) -> int:
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def run(cmd: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return subprocess.CompletedProcess(cmd, 124, '', str(exc))


UNIT_PROPERTIES = (
    'Id,LoadState,ActiveState,SubState,Result,ExecMainStatus,'
    'ActiveEnterTimestamp,InactiveEnterTimestamp'
)


def empty_unit_state(unit: str) -> dict:
    return {
        'unit': unit,
        'load_state': 'unknown',
        'active_state': 'unknown',
        'sub_state': 'unknown',
        'result': 'unknown',
        'exec_main_status': None,
    }


def unit_states(units: list[str]) -> dict[str, dict]:
    """Read many systemd unit states with one systemctl process."""
    unique = list(dict.fromkeys(units))
    result = {unit: empty_unit_state(unit) for unit in unique}
    if not unique:
        return result

    cp = run([
        'systemctl', 'show', *unique,
        '--property=' + UNIT_PROPERTIES,
        '--no-pager',
    ], timeout=6)
    if cp.returncode != 0:
        return result

    mapping = {
        'LoadState': 'load_state',
        'ActiveState': 'active_state',
        'SubState': 'sub_state',
        'Result': 'result',
        'ExecMainStatus': 'exec_main_status',
        'ActiveEnterTimestamp': 'active_enter_timestamp',
        'InactiveEnterTimestamp': 'inactive_enter_timestamp',
    }

    block: dict[str, str] = {}

    def flush() -> None:
        nonlocal block
        unit = block.get('Id', '')
        if not unit or unit not in result:
            block = {}
            return
        data = result[unit]
        for key, field in mapping.items():
            if key not in block:
                continue
            value = block[key]
            if key == 'ExecMainStatus':
                try:
                    data[field] = int(value)
                except ValueError:
                    data[field] = None
            else:
                data[field] = value
        block = {}

    for line in cp.stdout.splitlines():
        if not line.strip():
            flush()
            continue
        if '=' in line:
            key, value = line.split('=', 1)
            if key == 'Id' and block:
                flush()
            block[key] = value
    flush()
    return result


def unit_state(unit: str) -> dict:
    return unit_states([unit]).get(unit, empty_unit_state(unit))


def dotfiles_state() -> dict:
    if not DOTFILES_GIT.exists():
        return {'available': False, 'clean': False, 'changed_count': None, 'latest': None}
    cmd = ['git', f'--git-dir={DOTFILES_GIT}', f'--work-tree={HOME}']
    status = run(cmd + ['status', '--porcelain'], timeout=8)
    latest_cp = run(cmd + ['log', '-1', '--format=%H%x1f%h%x1f%ct%x1f%s'], timeout=8)
    latest = None
    if latest_cp.returncode == 0 and latest_cp.stdout.strip():
        parts = latest_cp.stdout.strip().split('\x1f', 3)
        if len(parts) == 4:
            try:
                timestamp = int(parts[2])
            except ValueError:
                timestamp = 0
            latest = {
                'id': parts[0],
                'short_id': parts[1],
                'timestamp': timestamp,
                'subject': parts[3],
            }
    changed_count = None
    if status.returncode == 0:
        changed_count = len([line for line in status.stdout.splitlines() if line.strip()])
    return {
        'available': status.returncode == 0,
        'clean': status.returncode == 0 and changed_count == 0,
        'changed_count': changed_count,
        'latest': latest,
    }



def read_dotfiles_cache() -> dict:
    try:
        data = json.loads(DOTFILES_CACHE.read_text())
        return data if isinstance(data, dict) else {
            'available': False, 'clean': False, 'changed_count': None, 'latest': None
        }
    except Exception:
        return {'available': False, 'clean': False, 'changed_count': None, 'latest': None}


def refresh_dotfiles_cache() -> dict:
    data = dotfiles_state()
    try:
        DOTFILES_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = DOTFILES_CACHE.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False) + '\n')
        tmp.replace(DOTFILES_CACHE)
    except Exception:
        pass
    return data

def fallback_state(message: str) -> dict:
    return {
        'schema_version': SCHEMA_VERSION,
        'ui_contract': UI_CONTRACT,
        'backend_revision': 'unavailable',
        'deployment': {},
        'generated_at': 0,
        'state_health': {
            'compatible': True,
            'fresh': False,
            'age_seconds': None,
            'refresh_ok': None,
            'error': message,
        },
        'overall': {
            'status': 'unverified',
            'label': 'Not verified',
            'summary': message,
        },
        'disk': {
            'connected': False,
            'mounted': False,
            'status': 'offline',
            'filesystem_accessible': False,
        },
        'restic': {
            'configured': False,
            'known': False,
            'availability': 'unavailable',
            'snapshots': [],
            'count': None,
            'latest': None,
            'check': {'known': False, 'ok': None, 'time': 0},
            'restore_test': {'known': False, 'ok': None, 'time': 0},
            'data_check': {'known': False, 'ok': None},
            'backup_proof': {'known': False, 'ok': None, 'current': False},
            'repository_id': '',
            'scope': {},
            'timer': {},
            'maintenance_timer': {},
        },
        'timeshift': {
            'configured': False,
            'known': False,
            'availability': 'unavailable',
            'snapshots': [],
            'count': None,
            'latest': None,
            'mode': 'RSYNC',
            'schedule': {'daily': False, 'daily_keep': 0, 'weekly': False, 'weekly_keep': 0},
        },
        'smart': {
            'known': False,
            'available': False,
            'availability': 'unavailable',
            'attributes': {},
            'self_tests': [],
            'last_test': None,
            'test_durations': {},
        },
        'recovery': {
            'core_ready': 0,
            'core_total': 7,
            'core_checks': [],
            'ready': 0,
            'total': 7,
            'checks': [],
            'resilience_checks': [],
            'restore_doc': '/mnt/backup/recovery/RESTORE.md',
            'manifests_updated_at': 0,
            'credential_evidence': {},
            'recovery_media_evidence': {},
            'second_copy_evidence': {},
        },
        'history': {
            'dotfiles': {'available': False, 'clean': False, 'changed_count': None, 'latest': None},
            'etc': {'available': False, 'clean': False, 'changed_count': None, 'latest': None},
        },
        'attention': [],
        'activity': [],
        'actions': {},
        'action_running': False,
        'current_action': '',
        'operation': {
            'running': False, 'action': '', 'state': 'idle', 'phase': '',
            'started_at': 0, 'updated_at': 0, 'finished_at': 0,
            'elapsed_seconds': 0, 'progress': {}, 'message': '',
        },
    }


def validate_contract(data: dict) -> tuple[bool, str]:
    try:
        schema = int(data.get('schema_version'))
    except (TypeError, ValueError):
        schema = -1
    contract = str(data.get('ui_contract') or '')
    if schema != SCHEMA_VERSION:
        return False, f'Incompatible state schema: expected {SCHEMA_VERSION}, got {schema}.'
    if contract != UI_CONTRACT:
        return False, f'Incompatible UI contract: expected {UI_CONTRACT}, got {contract or "missing"}.'
    return True, ''


def decorate_state_health(data: dict, *, refresh_ok: bool | None = None, refresh_error: str = '') -> dict:
    generated = int(data.get('generated_at', 0) or 0)
    age = max(0, int(time.time()) - generated) if generated else None
    fresh = age is not None and age <= STATE_STALE_SECONDS
    compatible, contract_error = validate_contract(data)
    error = refresh_error or contract_error
    data['state_health'] = {
        'compatible': compatible,
        'fresh': fresh,
        'age_seconds': age,
        'refresh_ok': refresh_ok,
        'error': error,
        'stale_after_seconds': STATE_STALE_SECONDS,
    }
    if not compatible:
        data['overall'] = {
            'status': 'critical',
            'label': 'Incompatible',
            'summary': contract_error,
        }
    elif refresh_ok is False:
        data['overall'] = {
            'status': 'unverified',
            'label': 'State stale',
            'summary': 'The latest privileged state refresh failed; displayed evidence is last-known only.',
        }
    elif not fresh:
        data['overall'] = {
            'status': 'unverified',
            'label': 'State stale',
            'summary': 'Backup state has not been refreshed recently enough to claim current protection.',
        }
    return data


def read_action_state() -> dict:
    try:
        data = json.loads(ACTION_STATE_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def busy_units() -> list[tuple[str, str]]:
    busy: list[tuple[str, str]] = []
    named = {**ACTION_UNITS, **EXTERNAL_BUSY_UNITS}
    states = unit_states(list(named.values()))
    for name, unit in named.items():
        item = states.get(unit, empty_unit_state(unit))
        if item.get('active_state') in ('activating', 'active'):
            busy.append((name, unit))
    return busy


def decorate_operation(data: dict, running_names: list[str], external_running: list[str]) -> None:
    now = int(time.time())
    running = running_names + external_running
    current = running[0] if running else ''
    raw = read_action_state()
    op = {
        'running': bool(running),
        'action': current,
        'state': 'running' if running else str(raw.get('state') or 'idle'),
        'phase': '',
        'started_at': 0,
        'updated_at': 0,
        'finished_at': 0,
        'elapsed_seconds': 0,
        'progress': {},
        'message': '',
    }
    raw_action = str(raw.get('action') or '')
    if raw and (not current or raw_action == current):
        op.update(raw)
        op['running'] = bool(running)
    elif current:
        op['action'] = current
        op['phase'] = 'Operation in progress'
    started = int(op.get('started_at') or 0)
    finished = int(op.get('finished_at') or 0)
    if started:
        end = now if op.get('running') else (finished or int(op.get('updated_at') or now))
        op['elapsed_seconds'] = max(0, end - started)
    if not isinstance(op.get('progress'), dict):
        op['progress'] = {}
    data['operation'] = op
    data['current_action'] = current
    data['action_running'] = bool(running)


def load_state(*, instant: bool = False, refresh_dotfiles: bool = False) -> dict:
    try:
        data = json.loads(STATE_FILE.read_text())
        if not isinstance(data, dict):
            raise ValueError('state root is not an object')
    except Exception as exc:
        data = fallback_state(f'State unavailable: {exc}')

    compatible, message = validate_contract(data)
    if not compatible:
        # Do not let an incompatible backend silently drive the current frontend.
        bad = fallback_state(message)
        bad['schema_version'] = data.get('schema_version')
        bad['ui_contract'] = data.get('ui_contract')
        data = bad

    data.setdefault('history', {})['dotfiles'] = (
        refresh_dotfiles_cache() if refresh_dotfiles else read_dotfiles_cache()
    )

    if instant:
        # Zero-subprocess open path: paint the last verified state immediately.
        raw = read_action_state()
        running = str(raw.get('state') or '') == 'running'
        data['actions'] = {}
        data['external_actions'] = {}
        decorate_operation(data, [str(raw.get('action') or 'operation')] if running else [], [])
        data['snapshot_mode'] = 'instant-cache'
        return decorate_state_health(data)

    named = {**ACTION_UNITS, **EXTERNAL_BUSY_UNITS}
    states = unit_states(list(named.values()))
    actions = {
        name: states.get(unit, empty_unit_state(unit))
        for name, unit in ACTION_UNITS.items()
    }
    external_actions = {
        name: states.get(unit, empty_unit_state(unit))
        for name, unit in EXTERNAL_BUSY_UNITS.items()
    }
    data['actions'] = actions
    data['external_actions'] = external_actions
    running = [
        name for name, item in actions.items()
        if item.get('active_state') in ('activating', 'active')
    ]
    external_running = [
        name for name, item in external_actions.items()
        if item.get('active_state') in ('activating', 'active')
    ]
    decorate_operation(data, running, external_running)
    data['snapshot_mode'] = 'fast-live'
    return decorate_state_health(data)


def refresh_state() -> tuple[bool, str]:
    cp = run(['systemctl', 'start', 'backup-recovery-state.service'], timeout=45)
    if cp.returncode != 0:
        detail = (cp.stderr or cp.stdout).strip()
        return False, detail or 'Unable to refresh privileged state.'
    return True, ''


def action(name: str) -> int:
    unit = ACTION_UNITS.get(name)
    if not unit:
        return emit({
            'ok': False,
            'error': 'unknown-action',
            'message': f'Unknown action: {name}',
        })
    busy = busy_units()
    if busy:
        current, current_unit = busy[0]
        return emit({
            'ok': False,
            'error': 'busy',
            'message': f'{current.replace("-", " ").title()} is already in progress.',
            'action': name,
            'current_action': current,
            'unit': current_unit,
        })
    cp = run(['systemctl', 'start', '--no-block', unit], timeout=10)
    if cp.returncode != 0:
        return emit({
            'ok': False,
            'error': 'action-rejected',
            'message': (cp.stderr or cp.stdout).strip(),
            'action': name,
            'unit': unit,
        })
    return emit({'ok': True, 'accepted': True, 'action': name, 'unit': unit})


def main() -> int:
    args = sys.argv[1:]
    cmd = args[0] if args else 'snapshot'

    if cmd == 'snapshot':
        if '--instant' in args:
            return emit(load_state(instant=True))
        if '--refresh' in args:
            ok, message = refresh_state()
            data = load_state(refresh_dotfiles=True)
            data = decorate_state_health(data, refresh_ok=ok, refresh_error='' if ok else message)
            if not ok:
                data['refresh_error'] = message
            return emit(data)
        return emit(load_state())

    if cmd == 'refresh':
        ok, message = refresh_state()
        data = load_state(refresh_dotfiles=True)
        data = decorate_state_health(data, refresh_ok=ok, refresh_error='' if ok else message)
        data['refresh_ok'] = ok
        if not ok:
            data['refresh_error'] = message
        return emit(data)

    if cmd == 'action':
        if len(args) < 2:
            return emit({
                'ok': False,
                'error': 'missing-action',
                'message': 'Action name required.',
            })
        return action(args[1])

    if cmd == 'ping':
        return emit({
            'ok': True,
            'service': 'backup-recovery-control-center',
            'version': UI_CONTRACT,
            'time': int(time.time()),
        })

    return emit({
        'ok': False,
        'error': 'unknown-command',
        'message': f'Unknown command: {cmd}',
    })


if __name__ == '__main__':
    raise SystemExit(main())
