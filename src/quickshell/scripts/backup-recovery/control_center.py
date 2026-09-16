#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
STATE_FILE = Path('/var/lib/backup-recovery/state.json')
DOTFILES_GIT = HOME / '.dotfiles'
UI_CONTRACT = '2'

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


def unit_state(unit: str) -> dict:
    cp = run([
        'systemctl', 'show', unit,
        '--property=LoadState,ActiveState,SubState,Result,ExecMainStatus,ActiveEnterTimestamp,InactiveEnterTimestamp',
        '--no-pager',
    ], timeout=5)
    data = {
        'unit': unit,
        'load_state': 'unknown',
        'active_state': 'unknown',
        'sub_state': 'unknown',
        'result': 'unknown',
        'exec_main_status': None,
    }
    if cp.returncode != 0:
        return data
    for line in cp.stdout.splitlines():
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        mapping = {
            'LoadState': 'load_state',
            'ActiveState': 'active_state',
            'SubState': 'sub_state',
            'Result': 'result',
            'ExecMainStatus': 'exec_main_status',
            'ActiveEnterTimestamp': 'active_enter_timestamp',
            'InactiveEnterTimestamp': 'inactive_enter_timestamp',
        }
        if key not in mapping:
            continue
        if key == 'ExecMainStatus':
            try:
                data[mapping[key]] = int(value)
            except ValueError:
                data[mapping[key]] = None
        else:
            data[mapping[key]] = value
    return data


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


def fallback_state(message: str) -> dict:
    return {
        'schema_version': 2,
        'ui_contract': UI_CONTRACT,
        'generated_at': 0,
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
            'core_total': 6,
            'core_checks': [],
            'ready': 0,
            'total': 6,
            'checks': [],
            'resilience_checks': [],
            'restore_doc': '',
            'manifests_updated_at': 0,
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
    }


def load_state() -> dict:
    try:
        data = json.loads(STATE_FILE.read_text())
        if not isinstance(data, dict):
            raise ValueError('state root is not an object')
    except Exception as exc:
        data = fallback_state(f'State unavailable: {exc}')

    data.setdefault('history', {})['dotfiles'] = dotfiles_state()
    actions = {name: unit_state(unit) for name, unit in ACTION_UNITS.items()}
    data['actions'] = actions
    running = [
        name for name, item in actions.items()
        if item.get('active_state') in ('activating', 'active')
    ]
    data['current_action'] = running[0] if running else ''
    data['action_running'] = bool(running)
    return data


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
        if '--refresh' in args:
            ok, message = refresh_state()
            data = load_state()
            if not ok:
                data['refresh_error'] = message
            return emit(data)
        return emit(load_state())

    if cmd == 'refresh':
        ok, message = refresh_state()
        data = load_state()
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
