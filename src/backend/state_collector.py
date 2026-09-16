#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

CONFIG_FILE = Path('/etc/backup-recovery/config.json')
STATE_DIR = Path('/var/lib/backup-recovery')
STATE_FILE = STATE_DIR / 'state.json'
EVIDENCE_FILE = STATE_DIR / 'evidence.json'
ACTIVITY_FILE = STATE_DIR / 'activity.json'
RESTIC_CHECK_MARKER = STATE_DIR / 'restic-check-ok.json'
RESTORE_TEST_MARKER = STATE_DIR / 'restore-test-ok.json'
SCHEMA_VERSION = 2
UI_CONTRACT = '2'


def now_ts() -> int:
    return int(time.time())


def run(cmd: list[str], timeout: int = 30, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=env,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        return subprocess.CompletedProcess(cmd, 124, '', str(exc))


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write_json_atomic(path: Path, data: Any, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def parse_systemctl_show(unit: str) -> dict[str, Any]:
    props = [
        'LoadState', 'ActiveState', 'SubState', 'UnitFileState', 'Result',
        'ExecMainStatus', 'ActiveEnterTimestamp', 'InactiveEnterTimestamp',
        'NextElapseUSecRealtime', 'LastTriggerUSec',
    ]
    cp = run(['systemctl', 'show', unit, '--no-pager', '--property=' + ','.join(props)], timeout=8)
    data: dict[str, Any] = {'unit': unit, 'available': cp.returncode == 0}
    for line in cp.stdout.splitlines():
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        data[key] = value
    return data


def resolve_backup_device(uuid: str) -> dict[str, Any]:
    link = Path('/dev/disk/by-uuid') / uuid
    connected = link.exists()
    part = ''
    disk = ''
    model = ''
    size = ''
    tran = ''
    rm = False
    hotplug = False
    if connected:
        try:
            part = str(link.resolve())
        except OSError:
            connected = False
    if part:
        parent = run(['lsblk', '-ndo', 'PKNAME', part], timeout=5).stdout.strip()
        if parent:
            disk = '/dev/' + parent
            model = run(['lsblk', '-dno', 'MODEL', disk], timeout=5).stdout.strip()
            size = run(['lsblk', '-dno', 'SIZE', disk], timeout=5).stdout.strip()
            tran = run(['lsblk', '-dno', 'TRAN', disk], timeout=5).stdout.strip()
            rm = run(['lsblk', '-dno', 'RM', disk], timeout=5).stdout.strip() == '1'
            hotplug = run(['lsblk', '-dno', 'HOTPLUG', disk], timeout=5).stdout.strip() == '1'
    return {
        'uuid': uuid,
        'connected': connected,
        'partition': part,
        'disk': disk,
        'model': model,
        'size': size,
        'transport': tran,
        'removable': rm,
        'hotplug': hotplug,
    }


def mount_state(mountpoint: str, device: dict[str, Any]) -> dict[str, Any]:
    cp = run(['findmnt', '-rn', '-o', 'SOURCE,FSTYPE,OPTIONS,UUID', mountpoint], timeout=5)
    mounted = cp.returncode == 0 and bool(cp.stdout.strip())
    source = fstype = options = mounted_uuid = ''
    if mounted:
        parts = cp.stdout.strip().split(None, 3)
        if len(parts) > 0:
            source = parts[0]
        if len(parts) > 1:
            fstype = parts[1]
        if len(parts) > 2:
            options = parts[2]
        if len(parts) > 3:
            mounted_uuid = parts[3]
    total = used = free = 0
    percent = 0.0
    filesystem_accessible = False
    if mounted:
        try:
            usage = shutil.disk_usage(mountpoint)
            total, used, free = usage.total, usage.used, usage.free
            percent = (used / total * 100.0) if total else 0.0
            filesystem_accessible = True
        except OSError:
            filesystem_accessible = False
    if mounted:
        status = 'mounted' if filesystem_accessible else 'mounted-unavailable'
    elif device.get('connected'):
        status = 'connected-unmounted'
    else:
        status = 'offline'
    return {
        **device,
        'mounted': mounted,
        'status': status,
        'filesystem_accessible': filesystem_accessible,
        'mountpoint': mountpoint,
        'source': source,
        'fstype': fstype,
        'options': options,
        'mounted_uuid': mounted_uuid,
        'total_bytes': total,
        'used_bytes': used,
        'free_bytes': free,
        'used_percent': round(percent, 1),
    }



def enforce_backup_mount_identity(disk: dict[str, Any], expected_uuid: str) -> dict[str, Any]:
    expected = str(expected_uuid or '').strip()
    if not expected:
        raise RuntimeError('Backup filesystem UUID is not configured.')

    mounted = bool(disk.get('mounted'))
    mounted_uuid = str(disk.get('mounted_uuid') or '').strip()
    raw_accessible = bool(disk.get('filesystem_accessible'))

    mounted_identity_verified = bool(mounted and mounted_uuid and mounted_uuid == expected)
    disk['mounted_identity_verified'] = mounted_identity_verified
    disk['identity_verified'] = bool(disk.get('connected')) and (
        (not mounted) or mounted_identity_verified
    )

    # Downstream code may inspect repository/snapshots/recovery material only
    # when both filesystem access and mount identity are verified.
    disk['raw_filesystem_accessible'] = raw_accessible
    disk['filesystem_accessible'] = bool(raw_accessible and mounted_identity_verified)

    if mounted and not mounted_identity_verified:
        disk['status'] = 'wrong-filesystem-mounted'
    return disk


def secure_user_readable_state(path: Path, user: str) -> None:
    if not user:
        raise RuntimeError('Configuration field "user" is required.')
    account = pwd.getpwnam(user)
    os.chown(path, 0, account.pw_gid)
    os.chmod(path, 0o640)


def parse_restic_snapshots(repo: str, password_file: str, cache_dir: str) -> tuple[dict[str, Any] | None, str]:
    cp = run([
        'restic', '-r', repo, '--password-file', password_file,
        '--cache-dir', cache_dir, 'snapshots', '--json',
    ], timeout=60)
    if cp.returncode != 0:
        return None, (cp.stderr or cp.stdout).strip()
    try:
        rows = json.loads(cp.stdout or '[]')
    except json.JSONDecodeError as exc:
        return None, f'Invalid Restic JSON: {exc}'
    snapshots: list[dict[str, Any]] = []
    for row in rows:
        sid = str(row.get('short_id') or row.get('id') or '')
        if len(sid) > 8:
            sid = sid[:8]
        snapshots.append({
            'id': str(row.get('id') or ''),
            'short_id': sid,
            'time': str(row.get('time') or ''),
            'hostname': str(row.get('hostname') or ''),
            'tags': row.get('tags') or [],
            'paths': row.get('paths') or [],
        })
    snapshots.sort(key=lambda item: item.get('time', ''), reverse=True)
    return {
        'known': True,
        'snapshots': snapshots,
        'count': len(snapshots),
        'latest': snapshots[0] if snapshots else None,
    }, ''


def read_timeshift_config() -> tuple[dict[str, Any], dict[str, Any]]:
    cfg = read_json(Path('/etc/timeshift/timeshift.json'), {})
    mode = 'RSYNC'
    if cfg and str(cfg.get('btrfs_mode', 'false')).lower() == 'true':
        mode = 'BTRFS'
    schedule = {
        'daily': str(cfg.get('schedule_daily', 'false')).lower() == 'true',
        'daily_keep': int(cfg.get('count_daily', '0') or 0),
        'weekly': str(cfg.get('schedule_weekly', 'false')).lower() == 'true',
        'weekly_keep': int(cfg.get('count_weekly', '0') or 0),
    }
    return cfg, {'configured': bool(cfg), 'mode': mode, 'schedule': schedule}


def read_timeshift_snapshots(mountpoint: str) -> dict[str, Any]:
    root = Path(mountpoint) / 'timeshift' / 'snapshots'
    rows: list[dict[str, Any]] = []
    if root.is_dir():
        for path in sorted(root.iterdir(), reverse=True):
            if not path.is_dir():
                continue
            info = read_json(path / 'info.json', {})
            try:
                created_at = int(path.stat().st_mtime)
            except OSError:
                created_at = 0
            rows.append({
                'name': path.name,
                'comments': str(info.get('comments') or info.get('comment') or ''),
                'tags': str(info.get('tags') or ''),
                'created_at': created_at,
            })
    return {
        'known': True,
        'snapshots': rows,
        'count': len(rows),
        'latest': rows[0] if rows else None,
    }


def _status_label(status: str) -> str:
    low = status.lower().strip()
    if 'completed without error' in low:
        return 'Passed'
    if 'aborted by host' in low:
        return 'Aborted by user'
    if 'interrupted' in low:
        return 'Interrupted'
    if 'in progress' in low:
        return 'Running'
    if 'failure' in low or 'failed' in low:
        return 'Failed'
    return status.strip() or 'Unknown'


def parse_smart_text(text: str) -> dict[str, Any]:
    health = 'unknown'
    match = re.search(r'SMART overall-health self-assessment test result:\s*(\S+)', text)
    if match:
        health = match.group(1).lower()

    attrs: dict[str, int | str] = {}
    wanted = {
        'Reallocated_Sector_Ct': 'reallocated',
        'Current_Pending_Sector': 'pending',
        'Offline_Uncorrectable': 'uncorrectable',
        'UDMA_CRC_Error_Count': 'crc_errors',
        'Power_On_Hours': 'power_on_hours',
        'Load_Cycle_Count': 'load_cycles',
    }
    temperature: int | None = None
    for line in text.splitlines():
        parts = line.split()
        # ATA SMART attribute rows have the raw value beginning at column 10.
        if len(parts) >= 10 and parts[0].isdigit():
            name = parts[1]
            raw = parts[9]
            if name in wanted:
                try:
                    attrs[wanted[name]] = int(raw)
                except ValueError:
                    attrs[wanted[name]] = raw
            if name in ('Temperature_Celsius', 'Airflow_Temperature_Cel') and temperature is None:
                try:
                    temperature = int(raw)
                except ValueError:
                    pass
    if temperature is None:
        tm = re.search(r'Current Drive Temperature:\s*(\d+)\s*C', text)
        if tm:
            temperature = int(tm.group(1))

    error_match = re.search(r'ATA Error Count:\s*(\d+)', text)
    error_count = int(error_match.group(1)) if error_match else None

    short_minutes = None
    extended_minutes = None
    sm = re.search(r'Short self-test routine\s+recommended polling time:\s*\(\s*(\d+)\)\s*minutes?', text, re.I | re.S)
    if sm:
        short_minutes = int(sm.group(1))
    em = re.search(r'Extended self-test routine\s+recommended polling time:\s*\(\s*(\d+)\)\s*minutes?', text, re.I | re.S)
    if em:
        extended_minutes = int(em.group(1))

    tests: list[dict[str, Any]] = []
    test_re = re.compile(r'^\s*#\s*(\d+)\s+(.+?)\s{2,}(.+?)\s{2,}(\d+)%\s+(\d+)\s+(\S+)\s*$')
    for line in text.splitlines():
        m = test_re.match(line)
        if not m:
            continue
        remaining = int(m.group(4))
        status = m.group(3).strip()
        tests.append({
            'number': int(m.group(1)),
            'type': m.group(2).strip(),
            'status': status,
            'status_label': _status_label(status),
            'remaining_percent': remaining,
            'completed_percent': max(0, min(100, 100 - remaining)),
            'lifetime_hours': int(m.group(5)),
            'lba': m.group(6),
            'raw': line.strip(),
        })
        if len(tests) >= 8:
            break

    def safe_int(name: str) -> int:
        try:
            return int(attrs.get(name, 0) or 0)
        except (TypeError, ValueError):
            return 0

    active_bad = safe_int('reallocated') + safe_int('pending') + safe_int('uncorrectable')
    if health not in ('passed', 'unknown') or active_bad > 0:
        condition = 'critical'
    elif error_count and error_count > 0:
        condition = 'history-warning'
    else:
        condition = 'healthy'

    return {
        'known': True,
        'available': True,
        'health': health,
        'condition': condition,
        'temperature_c': temperature,
        'attributes': attrs,
        'historical_error_count': error_count,
        'self_tests': tests,
        'last_test': tests[0] if tests else None,
        'test_durations': {
            'short_minutes': short_minutes,
            'extended_minutes': extended_minutes,
        },
    }


def parse_smart(disk_path: str) -> tuple[dict[str, Any] | None, str]:
    if not disk_path:
        return None, 'Physical backup disk is unavailable.'
    cp = run(['smartctl', '-a', disk_path], timeout=20)
    text = (cp.stdout or '') + '\n' + (cp.stderr or '')
    if 'SMART support is: Available' not in text and 'SMART overall-health' not in text:
        return None, text.strip()[-700:] or 'SMART data unavailable.'
    return parse_smart_text(text), ''


def etc_history() -> dict[str, Any]:
    if not Path('/etc/.git').exists():
        return {'available': False, 'clean': False, 'latest': None, 'changed_count': None}
    status = run(['git', '-C', '/etc', 'status', '--porcelain'], timeout=12)
    latest_cp = run(['git', '-C', '/etc', 'log', '-1', '--format=%H%x1f%h%x1f%ct%x1f%s'], timeout=8)
    latest = None
    if latest_cp.returncode == 0 and latest_cp.stdout.strip():
        parts = latest_cp.stdout.strip().split('\x1f', 3)
        if len(parts) == 4:
            try:
                timestamp = int(parts[2])
            except ValueError:
                timestamp = 0
            latest = {'id': parts[0], 'short_id': parts[1], 'timestamp': timestamp, 'subject': parts[3]}
    changed = None
    if status.returncode == 0:
        changed = len([line for line in status.stdout.splitlines() if line.strip()])
    return {
        'available': status.returncode == 0,
        'clean': status.returncode == 0 and changed == 0,
        'changed_count': changed,
        'latest': latest,
    }


def file_mtime(path: str) -> int:
    try:
        return int(Path(path).stat().st_mtime)
    except OSError:
        return 0


def load_activity() -> list[dict[str, Any]]:
    data = read_json(ACTIVITY_FILE, [])
    if not isinstance(data, list):
        return []
    return sorted(data, key=lambda item: int(item.get('time', 0)), reverse=True)[:30]


def check_marker(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {'known': False, 'ok': None, 'time': 0, 'detail': ''}
    data = read_json(path, {})
    if 'ok' not in data:
        return {'known': False, 'ok': None, 'time': int(data.get('time', 0) or 0), 'detail': str(data.get('detail') or '')}
    return {
        'known': True,
        'ok': bool(data.get('ok')),
        'time': int(data.get('time', 0) or 0),
        'detail': str(data.get('detail') or ''),
    }


def iso_to_ts(value: str) -> int:
    if not value:
        return 0
    try:
        return int(datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp())
    except ValueError:
        return 0


def cached_domain(evidence: dict[str, Any], name: str) -> dict[str, Any] | None:
    item = evidence.get(name)
    if not isinstance(item, dict) or not item.get('known'):
        return None
    cached = dict(item)
    cached['availability'] = 'cached'
    cached['cached'] = True
    return cached


def save_domain_evidence(evidence: dict[str, Any], name: str, payload: dict[str, Any]) -> None:
    keep = dict(payload)
    keep['known'] = True
    keep['verified_at'] = now_ts()
    keep.pop('availability', None)
    keep.pop('cached', None)
    keep.pop('current_error', None)
    evidence[name] = keep


def core_check(check_id: str, label: str, ok: bool, state: str, detail: str, cached: bool = False) -> dict[str, Any]:
    return {
        'id': check_id,
        'label': label,
        'ok': ok,
        'state': state,
        'detail': detail,
        'cached': cached,
    }


def main() -> int:
    cfg = read_json(CONFIG_FILE, {})
    if not cfg:
        raise SystemExit('backup-recovery config missing')

    evidence = read_json(EVIDENCE_FILE, {})
    if not isinstance(evidence, dict):
        evidence = {}

    uuid = str(cfg.get('backup_uuid') or '').strip()
    if not uuid or uuid == 'CHANGE_ME':
        raise SystemExit('backup-recovery config field "backup_uuid" is required')
    mountpoint = str(cfg.get('mountpoint') or '').strip()
    if not mountpoint:
        raise SystemExit('backup-recovery config field "mountpoint" is required')
    repo = str(cfg.get('restic_repo', mountpoint + '/restic'))
    password_file = str(cfg.get('restic_password_file', '/etc/restic/password'))
    cache_dir = str(cfg.get('restic_cache_dir', '/var/cache/restic'))
    overdue_hours = int(cfg.get('overdue_hours', 48))

    device = resolve_backup_device(uuid)
    disk = mount_state(mountpoint, device)
    disk['expected_uuid'] = uuid
    disk = enforce_backup_mount_identity(disk, uuid)

    # Restic: live query when accessible, otherwise preserve explicit last-known evidence.
    restic_error = ''
    restic: dict[str, Any]
    repo_exists = disk['filesystem_accessible'] and Path(repo).is_dir()
    password_exists = Path(password_file).is_file()
    if repo_exists and password_exists:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        live_restic, restic_error = parse_restic_snapshots(repo, password_file, cache_dir)
        if live_restic is not None:
            restic = live_restic
            restic.update({
                'configured': True,
                'availability': 'live',
                'cached': False,
                'repository_accessible': True,
                'verified_at': now_ts(),
                'current_error': '',
            })
            save_domain_evidence(evidence, 'restic', restic)
        else:
            cached = cached_domain(evidence, 'restic')
            restic = cached or {'known': False, 'snapshots': [], 'count': None, 'latest': None}
            restic.update({
                'configured': True,
                'availability': 'cached' if cached else 'unavailable',
                'cached': bool(cached),
                'repository_accessible': True,
                'current_error': restic_error,
            })
    else:
        cached = cached_domain(evidence, 'restic')
        restic = cached or {'known': False, 'snapshots': [], 'count': None, 'latest': None}
        restic.update({
            'configured': password_exists,
            'availability': 'cached' if cached else 'unavailable',
            'cached': bool(cached),
            'repository_accessible': False,
            'current_error': '',
        })

    # Timeshift schedule is configuration state and remains known while the HDD is offline.
    timeshift_cfg, timeshift_base = read_timeshift_config()
    timeshift: dict[str, Any]
    if disk['filesystem_accessible']:
        live_ts = read_timeshift_snapshots(mountpoint)
        timeshift = {**timeshift_base, **live_ts, 'availability': 'live', 'cached': False, 'verified_at': now_ts()}
        save_domain_evidence(evidence, 'timeshift', {
            **live_ts,
            'mode': timeshift_base['mode'],
            'schedule': timeshift_base['schedule'],
            'configured': timeshift_base['configured'],
        })
    else:
        cached = cached_domain(evidence, 'timeshift')
        timeshift = cached or {'known': False, 'snapshots': [], 'count': None, 'latest': None}
        timeshift.update(timeshift_base)
        timeshift['availability'] = 'cached' if cached else 'unavailable'
        timeshift['cached'] = bool(cached)

    # SMART can be queried from the physical disk even when the filesystem is not mounted.
    smart_error = ''
    if device.get('connected'):
        live_smart, smart_error = parse_smart(str(device.get('disk') or ''))
        if live_smart is not None:
            smart = live_smart
            smart.update({'availability': 'live', 'cached': False, 'verified_at': now_ts(), 'current_error': ''})
            save_domain_evidence(evidence, 'smart', smart)
        else:
            cached = cached_domain(evidence, 'smart')
            smart = cached or {'known': False, 'available': False, 'attributes': {}, 'self_tests': [], 'last_test': None, 'test_durations': {}}
            smart.update({'availability': 'cached' if cached else 'unavailable', 'cached': bool(cached), 'current_error': smart_error})
    else:
        cached = cached_domain(evidence, 'smart')
        smart = cached or {'known': False, 'available': False, 'attributes': {}, 'self_tests': [], 'last_test': None, 'test_durations': {}}
        smart.update({'availability': 'cached' if cached else 'unavailable', 'cached': bool(cached), 'current_error': ''})
    smart['available'] = bool(smart.get('known'))

    backup_timer_name = str(cfg.get('backup_timer') or 'restic-system-backup.timer')
    maintenance_timer_name = str(cfg.get('maintenance_timer') or 'restic-maintenance.timer')
    backup_service_name = str(cfg.get('backup_service') or 'restic-system-backup.service')
    restic_timer = parse_systemctl_show(backup_timer_name)
    maintenance_timer = parse_systemctl_show(maintenance_timer_name)
    restic_service = parse_systemctl_show(backup_service_name)
    state_timer = parse_systemctl_show('backup-recovery-state.timer')

    latest_ts = 0
    if restic.get('latest'):
        latest_ts = iso_to_ts(str(restic['latest'].get('time') or ''))
    age_hours = ((now_ts() - latest_ts) / 3600.0) if latest_ts else None
    restic['latest_timestamp'] = latest_ts
    restic['age_hours'] = round(age_hours, 1) if age_hours is not None else None
    restic['timer'] = restic_timer
    restic['maintenance_timer'] = maintenance_timer
    restic['service'] = restic_service
    restic['check'] = check_marker(RESTIC_CHECK_MARKER)
    restic['restore_test'] = check_marker(RESTORE_TEST_MARKER)

    recovery_root = Path(mountpoint) / 'recovery'
    arch_state = Path(mountpoint) / 'arch-state'
    restore_doc = recovery_root / 'RESTORE.md'
    manifests = arch_state / 'packages-explicit.txt'

    recovery_evidence = evidence.get('recovery') if isinstance(evidence.get('recovery'), dict) else {}
    if disk['filesystem_accessible']:
        guide_known = restore_doc.is_file()
        manifests_known = manifests.is_file()
        manifests_updated_at = file_mtime(str(manifests)) if manifests_known else 0
        iso_files = [p.name for p in recovery_root.glob('*.iso')] if recovery_root.is_dir() else []
        recovery_evidence = {
            'known': True,
            'verified_at': now_ts(),
            'guide_known': guide_known,
            'manifests_known': manifests_known,
            'manifests_updated_at': manifests_updated_at,
            'iso_files': iso_files,
        }
        evidence['recovery'] = recovery_evidence
    else:
        guide_known = bool(recovery_evidence.get('guide_known'))
        manifests_known = bool(recovery_evidence.get('manifests_known'))
        manifests_updated_at = int(recovery_evidence.get('manifests_updated_at', 0) or 0)
        iso_files = list(recovery_evidence.get('iso_files') or [])

    # An ISO copy is useful recovery material, but it is not proof that a bootable USB exists.
    recovery_media_verified = bool(cfg.get('recovery_media_verified', False))
    second_copy_configured = bool(cfg.get('second_copy_configured', False))

    restic_known = bool(restic.get('known')) and restic.get('count') is not None
    restic_count = int(restic.get('count') or 0) if restic_known else 0
    timeshift_known = bool(timeshift.get('known')) and timeshift.get('count') is not None
    timeshift_count = int(timeshift.get('count') or 0) if timeshift_known else 0

    core_checks = [
        core_check(
            'restic', 'Encrypted Restic repository',
            restic_known and restic_count > 0,
            'ready' if restic_known and restic_count > 0 else ('missing' if restic_known else 'unknown'),
            (f'{restic_count} snapshot(s) verified' if restic_known else 'Snapshot inventory has not been verified yet.'),
            bool(restic.get('cached')),
        ),
        core_check(
            'restore-test', 'Actual restore test',
            restic['restore_test'].get('ok') is True,
            'ready' if restic['restore_test'].get('ok') is True else ('failed' if restic['restore_test'].get('known') else 'untested'),
            ('A file was restored and matched byte-for-byte.' if restic['restore_test'].get('ok') is True else 'Run Verify restore while the backup HDD is mounted.'),
        ),
        core_check(
            'timeshift', 'Timeshift restore points',
            timeshift_known and timeshift_count > 0,
            'ready' if timeshift_known and timeshift_count > 0 else ('missing' if timeshift_known else 'unknown'),
            (f'{timeshift_count} restore point(s) verified' if timeshift_known else 'Restore-point inventory has not been verified yet.'),
            bool(timeshift.get('cached')),
        ),
        core_check(
            'guide', 'Recovery documentation', guide_known,
            'ready' if guide_known else ('missing' if disk['filesystem_accessible'] else 'unknown'),
            'RESTORE.md is available.' if guide_known else 'Mount the backup HDD to verify RESTORE.md.',
            not disk['filesystem_accessible'] and guide_known,
        ),
        core_check(
            'manifests', 'Arch system manifests', manifests_known,
            'ready' if manifests_known else ('missing' if disk['filesystem_accessible'] else 'unknown'),
            'Package and system-state manifests are available.' if manifests_known else 'Mount the backup HDD to verify recovery manifests.',
            not disk['filesystem_accessible'] and manifests_known,
        ),
        core_check(
            'recovery-media', 'Bootable recovery media', recovery_media_verified,
            'ready' if recovery_media_verified else 'pending',
            'Bootable recovery media has been explicitly verified.' if recovery_media_verified else 'Not verified yet. A stored ISO alone is not counted as a bootable USB.',
        ),
    ]
    core_ready = sum(1 for item in core_checks if item['ok'])

    resilience_checks = [
        core_check(
            'second-copy', 'Second independent copy', second_copy_configured,
            'ready' if second_copy_configured else 'recommended',
            'An independent copy is configured.' if second_copy_configured else 'Recommended for protection against failure of the backup HDD itself.',
        )
    ]

    attention: list[dict[str, Any]] = []
    if disk.get('mounted') and not disk.get('mounted_identity_verified'):
        actual = str(disk.get('mounted_uuid') or '').strip() or 'unavailable'
        attention.append({
            'severity': 'critical',
            'tag': 'IDENTITY',
            'title': 'Unverified filesystem at backup mountpoint',
            'detail': (
                f'Expected backup UUID {uuid}, but the mounted filesystem UUID is {actual}. '
                'Backup/recovery reads and writes are blocked.'
            ),
        })
    if age_hours is not None and age_hours > overdue_hours:
        attention.append({
            'severity': 'warning', 'tag': 'DUE', 'title': 'Backup overdue',
            'detail': f'Last known Restic snapshot is {age_hours:.1f} hours old.',
        })
    if disk['connected'] and not disk['mounted']:
        attention.append({
            'severity': 'info', 'tag': 'INFO', 'title': 'Backup HDD connected but not mounted',
            'detail': 'Mount it when you want to create or verify backups.',
        })
    elif not disk['connected'] and age_hours is None:
        attention.append({
            'severity': 'info', 'tag': 'INFO', 'title': 'Backup HDD offline',
            'detail': 'Offline is normal, but no verified backup age is currently available.',
        })

    attrs = smart.get('attributes') or {}
    if smart.get('known'):
        def attr_int(key: str) -> int:
            try:
                return int(attrs.get(key, 0) or 0)
            except (TypeError, ValueError):
                return 0
        if any(attr_int(key) > 0 for key in ('reallocated', 'pending', 'uncorrectable')):
            attention.append({
                'severity': 'critical', 'tag': 'RISK', 'title': 'Active SMART sector problem',
                'detail': 'Reallocated, pending or uncorrectable sector counters are non-zero.',
            })
        elif int(smart.get('historical_error_count') or 0) > 0:
            attention.append({
                'severity': 'history', 'tag': 'HISTORY', 'title': 'Historical HDD read errors',
                'detail': f"ATA log contains {smart.get('historical_error_count')} historical error(s); current bad-sector counters are zero.",
            })
    if smart_error and device.get('connected'):
        attention.append({
            'severity': 'unverified', 'tag': 'UNVERIFIED', 'title': 'SMART status could not be refreshed',
            'detail': smart_error[:260],
        })
    if restic_error and disk['filesystem_accessible']:
        attention.append({
            'severity': 'critical', 'tag': 'ERROR', 'title': 'Restic repository query failed',
            'detail': restic_error[:260],
        })
    if disk['filesystem_accessible'] and not restic['check'].get('known'):
        attention.append({
            'severity': 'unverified', 'tag': 'UNVERIFIED', 'title': 'Repository integrity has not been checked here',
            'detail': 'Run Check repository when convenient.',
        })

    critical = any(item['severity'] == 'critical' for item in attention)
    protection_known = restic_known and restic_count > 0 and timeshift_known and timeshift_count > 0
    if critical:
        overall = {
            'status': 'critical', 'label': 'Needs attention',
            'summary': 'A verified backup or disk-health problem needs attention.',
        }
    elif age_hours is not None and age_hours > overdue_hours:
        overall = {
            'status': 'warning', 'label': 'Backup due',
            'summary': 'Protection exists, but the latest known backup is older than the configured target.',
        }
    elif protection_known and disk['mounted']:
        overall = {
            'status': 'protected', 'label': 'Protected',
            'summary': 'Protection is verified and the backup HDD is mounted.',
        }
    elif protection_known and disk['connected']:
        overall = {
            'status': 'protected-unmounted', 'label': 'Protected',
            'summary': 'Protection is current; the backup HDD is connected but not mounted.',
        }
    elif protection_known:
        overall = {
            'status': 'protected-offline', 'label': 'Protected',
            'summary': 'Protection is current; the backup HDD is offline by design.',
        }
    else:
        overall = {
            'status': 'unverified', 'label': 'Not verified',
            'summary': 'Some recovery evidence has not been verified yet.',
        }

    recovery = {
        'core_ready': core_ready,
        'core_total': len(core_checks),
        'core_checks': core_checks,
        # Compatibility aliases for older UI while upgrading transactionally.
        'ready': core_ready,
        'total': len(core_checks),
        'checks': core_checks,
        'resilience_checks': resilience_checks,
        'restore_doc': str(restore_doc),
        'restore_doc_known': guide_known,
        'manifests_path': str(arch_state),
        'manifests_known': manifests_known,
        'manifests_updated_at': manifests_updated_at,
        'iso_files': iso_files,
        'recovery_media_verified': recovery_media_verified,
    }

    state = {
        'schema_version': SCHEMA_VERSION,
        'ui_contract': UI_CONTRACT,
        'generated_at': now_ts(),
        'overall': overall,
        'disk': disk,
        'restic': restic,
        'timeshift': timeshift,
        'smart': smart,
        'history': {'etc': etc_history()},
        'recovery': recovery,
        'attention': attention,
        'activity': load_activity(),
        'state_timer': state_timer,
    }

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    write_json_atomic(EVIDENCE_FILE, evidence, 0o600)
    write_json_atomic(STATE_FILE, state, 0o640)
    secure_user_readable_state(STATE_FILE, str(cfg.get('user') or '').strip())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
