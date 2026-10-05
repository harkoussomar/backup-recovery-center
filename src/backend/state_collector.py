#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import json
import os
import pwd
import re
import shutil
import socket
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

CONFIG_FILE = Path('/etc/backup-recovery/config.json')
INSTALL_FILE = Path('/etc/backup-recovery/install.json')
STATE_DIR = Path('/var/lib/backup-recovery')
STATE_FILE = STATE_DIR / 'state.json'
EVIDENCE_FILE = STATE_DIR / 'evidence.json'
ACTIVITY_FILE = STATE_DIR / 'activity.json'
RESTIC_CHECK_MARKER = STATE_DIR / 'restic-check-ok.json'
RESTIC_DATA_CHECK_MARKER = STATE_DIR / 'restic-data-check.json'
RESTORE_TEST_MARKER = STATE_DIR / 'restore-test-ok.json'
BACKUP_PROOF_MARKER = STATE_DIR / 'backup-proof.json'
CREDENTIAL_EVIDENCE_MARKER = STATE_DIR / 'credential-recovery.json'
RECOVERY_MEDIA_EVIDENCE_MARKER = STATE_DIR / 'recovery-media.json'
SECOND_COPY_EVIDENCE_MARKER = STATE_DIR / 'second-copy.json'
SCHEMA_VERSION = 2
UI_CONTRACT = '1.6.0'
BACKEND_REVISION = '1.6.0-phase4-final'


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
    fd, tmp_name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


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

    # filesystem_accessible is the trusted application-level signal. Keep the
    # raw observation for diagnostics, but never inspect Restic/Timeshift/
    # recovery material through an unverified mount.
    disk['raw_filesystem_accessible'] = raw_accessible
    disk['filesystem_accessible'] = bool(raw_accessible and mounted_identity_verified)

    if mounted and not mounted_identity_verified:
        disk['status'] = 'wrong-filesystem-mounted'
    return disk


def secure_user_readable_state(path: Path, user: str) -> None:
    # state.json must be readable by the desktop user but not world-readable.
    account = pwd.getpwnam(user)
    os.chown(path, 0, account.pw_gid)
    os.chmod(path, 0o640)


def parse_restic_repository_id(repo: str, password_file: str, cache_dir: str) -> tuple[str, str]:
    cp = run([
        'restic', '-r', repo, '--password-file', password_file,
        '--cache-dir', cache_dir, 'cat', 'config',
    ], timeout=60)
    if cp.returncode != 0:
        return '', (cp.stderr or cp.stdout).strip()
    try:
        payload = json.loads(cp.stdout or '{}')
    except json.JSONDecodeError as exc:
        return '', f'Invalid Restic repository config JSON: {exc}'
    repo_id = str(payload.get('id') or '').strip()
    if not repo_id:
        return '', 'Restic repository did not expose a stable repository ID.'
    return repo_id, ''


def parse_restic_snapshots(
    repo: str,
    password_file: str,
    cache_dir: str,
    expected_hostname: str,
    required_tags: list[str],
    required_paths: list[str],
) -> tuple[dict[str, Any] | None, str]:
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

    required_tag_set = {str(value) for value in required_tags if str(value)}
    required_path_set = {str(value) for value in required_paths if str(value)}
    snapshots: list[dict[str, Any]] = []
    total_repository_snapshots = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        total_repository_snapshots += 1
        hostname = str(row.get('hostname') or '')
        tags = [str(value) for value in (row.get('tags') or [])]
        paths = [str(value) for value in (row.get('paths') or [])]
        if expected_hostname and hostname != expected_hostname:
            continue
        if required_tag_set and not required_tag_set.issubset(set(tags)):
            continue
        if required_path_set and not required_path_set.issubset(set(paths)):
            continue

        sid = str(row.get('short_id') or row.get('id') or '')
        if len(sid) > 8:
            sid = sid[:8]
        snapshots.append({
            'id': str(row.get('id') or ''),
            'short_id': sid,
            'time': str(row.get('time') or ''),
            'hostname': hostname,
            'tags': tags,
            'paths': paths,
        })
    snapshots.sort(key=lambda item: item.get('time', ''), reverse=True)
    return {
        'known': True,
        'scope_version': 1,
        'scope': {
            'hostname': expected_hostname,
            'required_tags': list(required_tags),
            'required_paths': list(required_paths),
        },
        'repository_snapshot_count': total_repository_snapshots,
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
        return 'Aborted by host/system'
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
    if health == 'unknown':
        condition = 'unverified'
    elif health != 'passed' or active_bad > 0:
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


def age_hours_from_ts(timestamp: int) -> float | None:
    if not timestamp:
        return None
    return round(max(0.0, (now_ts() - timestamp) / 3600.0), 1)


def freshness_state(age_hours: float | None, overdue_hours: int) -> str:
    if age_hours is None:
        return 'unknown'
    return 'stale' if age_hours > overdue_hours else 'fresh'


def enrich_marker_freshness(marker: dict[str, Any], overdue_hours: int) -> dict[str, Any]:
    result = dict(marker)
    age = age_hours_from_ts(int(result.get('time', 0) or 0))
    result['age_hours'] = age
    result['overdue_hours'] = overdue_hours
    result['freshness'] = freshness_state(age, overdue_hours) if result.get('known') else 'unknown'
    result['fresh'] = bool(result.get('known')) and result['freshness'] == 'fresh'
    return result



def marker_with_provenance(
    path: Path,
    overdue_hours: int,
    *,
    backup_uuid: str,
    repository_id: str = '',
    latest_snapshot_id: str = '',
) -> dict[str, Any]:
    marker = enrich_marker_freshness(check_marker(path), overdue_hours)
    if not marker.get('known'):
        marker.update({'identity_valid': None, 'snapshot_current': None, 'provenance_state': 'unknown'})
        return marker

    marker_uuid = str(read_json(path, {}).get('backup_uuid') or '')
    raw = read_json(path, {})
    marker_repo = str(raw.get('repository_id') or '')
    marker_snapshot = str(raw.get('snapshot_id') or '')

    identity_valid = bool(marker_uuid and marker_uuid == backup_uuid)
    if repository_id:
        identity_valid = identity_valid and bool(marker_repo and marker_repo == repository_id)

    snapshot_current: bool | None = None
    if latest_snapshot_id:
        snapshot_current = bool(marker_snapshot and marker_snapshot == latest_snapshot_id)

    marker.update(raw)
    marker['identity_valid'] = identity_valid
    marker['snapshot_current'] = snapshot_current
    if not identity_valid:
        marker['provenance_state'] = 'identity-mismatch'
        marker['fresh'] = False
        marker['freshness'] = 'invalid'
    elif snapshot_current is False:
        marker['provenance_state'] = 'superseded'
        marker['fresh'] = False
        marker['freshness'] = 'superseded'
    else:
        marker['provenance_state'] = 'current'
    return marker


def attestation_marker(
    path: Path,
    overdue_hours: int,
    *,
    backup_uuid: str = '',
    repository_id: str = '',
) -> dict[str, Any]:
    raw = read_json(path, {})
    if not isinstance(raw, dict) or raw.get('verified') is not True:
        return {
            'known': False,
            'verified': False,
            'verified_at': 0,
            'label': '',
            'identifier': '',
            'age_hours': None,
            'overdue_hours': overdue_hours,
            'freshness': 'unknown',
            'fresh': False,
            'identity_valid': None,
        }
    verified_at = int(raw.get('verified_at', 0) or 0)
    age = age_hours_from_ts(verified_at)
    freshness = freshness_state(age, overdue_hours)
    identity_valid = True
    if backup_uuid:
        identity_valid = str(raw.get('backup_uuid') or '') == backup_uuid
    if repository_id:
        identity_valid = identity_valid and str(raw.get('repository_id') or '') == repository_id
    if not identity_valid:
        freshness = 'invalid'
    return {
        **raw,
        'known': True,
        'verified': True,
        'verified_at': verified_at,
        'age_hours': age,
        'overdue_hours': overdue_hours,
        'freshness': freshness,
        'fresh': freshness == 'fresh' and identity_valid,
        'identity_valid': identity_valid,
    }


def backup_proof_state(
    *,
    backup_uuid: str,
    repository_id: str,
    latest_snapshot_id: str,
) -> dict[str, Any]:
    raw = read_json(BACKUP_PROOF_MARKER, {})
    if not isinstance(raw, dict) or 'ok' not in raw:
        return {'known': False, 'ok': None, 'current': False, 'detail': ''}
    identity_valid = (
        str(raw.get('backup_uuid') or '') == backup_uuid
        and (not repository_id or str(raw.get('repository_id') or '') == repository_id)
    )
    snapshot_current = bool(
        latest_snapshot_id
        and str(raw.get('snapshot_id') or '') == latest_snapshot_id
    )
    return {
        **raw,
        'known': True,
        'identity_valid': identity_valid,
        'snapshot_current': snapshot_current,
        'current': bool(raw.get('ok') is True and identity_valid and snapshot_current),
        'age_hours': age_hours_from_ts(int(raw.get('time', 0) or 0)),
    }


def restic_data_check_state(
    *,
    backup_uuid: str,
    repository_id: str,
) -> dict[str, Any]:
    raw = read_json(RESTIC_DATA_CHECK_MARKER, {})
    if not isinstance(raw, dict) or not raw:
        return {'known': False, 'ok': None, 'identity_valid': None}
    identity_valid = (
        str(raw.get('backup_uuid') or '') == backup_uuid
        and (not repository_id or str(raw.get('repository_id') or '') == repository_id)
    )
    return {
        **raw,
        'known': True,
        'identity_valid': identity_valid,
        'age_hours': age_hours_from_ts(int(raw.get('time', 0) or 0)),
    }


def derive_overall(
    *,
    critical: bool,
    restic_ready: bool,
    restic_stale: bool,
    timeshift_ready: bool,
    timeshift_stale: bool,
    disk: dict[str, Any],
) -> dict[str, Any]:
    if critical:
        return {
            'status': 'critical', 'label': 'Needs attention',
            'summary': 'A current backup, filesystem, restore-verification, or disk-health problem needs attention.',
        }
    if restic_stale or timeshift_stale:
        stale = []
        if restic_stale:
            stale.append('backup')
        if timeshift_stale:
            stale.append('restore point')
        return {
            'status': 'warning', 'label': 'Protection due',
            'summary': 'Protection exists, but the ' + ' and '.join(stale) + ' evidence is older than its configured target.',
        }
    protection_ready = restic_ready and timeshift_ready
    if protection_ready and disk.get('mounted_identity_verified') and disk.get('filesystem_accessible'):
        return {
            'status': 'protected', 'label': 'Protected',
            'summary': 'Protection is current and the verified backup filesystem is mounted.',
        }
    if protection_ready and disk.get('connected') and not disk.get('mounted'):
        return {
            'status': 'protected-unmounted', 'label': 'Protected',
            'summary': 'Protection is current; the backup HDD is connected but intentionally unmounted.',
        }
    if protection_ready and not disk.get('connected'):
        return {
            'status': 'protected-offline', 'label': 'Protected',
            'summary': 'Protection is current; the backup HDD is offline by design.',
        }
    return {
        'status': 'unverified', 'label': 'Not verified',
        'summary': 'Some protection evidence is missing, stale, or has not been verified yet.',
    }


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


def collect_state_once() -> int:
    cfg = read_json(CONFIG_FILE, {})
    if not cfg:
        raise SystemExit('backup-recovery config missing')

    evidence = read_json(EVIDENCE_FILE, {})
    if not isinstance(evidence, dict):
        evidence = {}

    uuid = str(cfg['backup_uuid'])
    mountpoint = str(cfg.get('mountpoint', '/mnt/backup'))
    repo = str(cfg.get('restic_repo', mountpoint + '/restic'))
    password_file = str(cfg.get('restic_password_file', '/etc/restic/password'))
    cache_dir = str(cfg.get('restic_cache_dir', '/var/cache/restic'))
    overdue_hours = int(cfg.get('overdue_hours', 48))
    timeshift_overdue_hours = int(cfg.get('timeshift_overdue_hours', 168))
    repository_check_overdue_hours = int(cfg.get('repository_check_overdue_hours', 720))
    restore_test_overdue_hours = int(cfg.get('restore_test_overdue_hours', 720))
    manifests_overdue_hours = int(cfg.get('manifests_overdue_hours', 168))
    expected_hostname = str(cfg.get('restic_expected_hostname') or socket.gethostname()).strip()
    required_tags = [str(value) for value in (cfg.get('restic_required_tags') or []) if str(value)]
    required_paths = [str(value) for value in (cfg.get('restic_required_paths') or []) if str(value)]
    credential_proof_overdue_hours = int(cfg.get('credential_proof_overdue_hours', 4320))
    recovery_media_proof_overdue_hours = int(cfg.get('recovery_media_proof_overdue_hours', 2160))
    second_copy_proof_overdue_hours = int(cfg.get('second_copy_proof_overdue_hours', 720))

    device = resolve_backup_device(uuid)
    disk = mount_state(mountpoint, device)
    disk['expected_uuid'] = uuid
    disk = enforce_backup_mount_identity(disk, uuid)

    # Restic: live query when accessible, otherwise preserve explicit last-known evidence.
    # Phase 3 scopes inventory to this machine/declared scope and binds evidence
    # to a stable repository ID.
    restic_error = ''
    restic: dict[str, Any]
    repo_exists = disk['filesystem_accessible'] and Path(repo).is_dir()
    password_exists = Path(password_file).is_file()
    if repo_exists and password_exists:
        Path(cache_dir).mkdir(parents=True, exist_ok=True)
        live_restic, restic_error = parse_restic_snapshots(
            repo, password_file, cache_dir,
            expected_hostname, required_tags, required_paths,
        )
        repo_id = ''
        if live_restic is not None:
            repo_id, repo_id_error = parse_restic_repository_id(repo, password_file, cache_dir)
            if not repo_id:
                restic_error = repo_id_error or 'Unable to verify Restic repository identity.'
                live_restic = None
        if live_restic is not None:
            restic = live_restic
            restic.update({
                'repository_id': repo_id,
                'configured': True,
                'availability': 'live',
                'current_state': 'ready' if int(live_restic.get('count') or 0) > 0 else 'empty',
                'cached': False,
                'repository_accessible': True,
                'verified_at': now_ts(),
                'current_error': '',
            })
            save_domain_evidence(evidence, 'restic', restic)
        else:
            cached = cached_domain(evidence, 'restic')
            # Phase-2 cache was repository-wide and had no explicit scope binding.
            # Do not promote legacy unscoped cache into machine-specific evidence.
            if cached and int(cached.get('scope_version', 0) or 0) != 1:
                cached = None
            restic = cached or {
                'known': False, 'snapshots': [], 'count': None, 'latest': None,
                'scope_version': 1,
                'scope': {
                    'hostname': expected_hostname,
                    'required_tags': required_tags,
                    'required_paths': required_paths,
                },
            }
            restic.update({
                'configured': True,
                'availability': 'cached' if cached else 'unavailable',
                'current_state': 'failed',
                'cached': bool(cached),
                'repository_accessible': True,
                'current_error': restic_error or 'Restic repository query failed.',
            })
    else:
        cached = cached_domain(evidence, 'restic')
        if cached and int(cached.get('scope_version', 0) or 0) != 1:
            cached = None
        restic = cached or {
            'known': False, 'snapshots': [], 'count': None, 'latest': None,
            'scope_version': 1,
            'scope': {
                'hostname': expected_hostname,
                'required_tags': required_tags,
                'required_paths': required_paths,
            },
        }
        if not password_exists:
            current_state = 'missing-credential'
            current_error = 'Restic password file is missing.'
        elif disk['filesystem_accessible'] and not repo_exists:
            current_state = 'missing-repository'
            current_error = 'Restic repository directory is missing on the verified backup filesystem.'
        elif disk.get('mounted') and not disk.get('mounted_identity_verified'):
            current_state = 'blocked-identity'
            current_error = 'Restic repository access is blocked until the mounted filesystem identity is verified.'
        else:
            current_state = 'cached' if cached else 'unavailable'
            current_error = ''
        restic.update({
            'configured': password_exists,
            'availability': 'cached' if cached else 'unavailable',
            'current_state': current_state,
            'cached': bool(cached),
            'repository_accessible': False,
            'current_error': current_error,
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

    restic_timer = parse_systemctl_show('restic-system-backup.timer')
    maintenance_timer = parse_systemctl_show('restic-maintenance.timer')
    restic_service = parse_systemctl_show('restic-system-backup.service')
    state_timer = parse_systemctl_show('backup-recovery-state.timer')

    latest_ts = 0
    if restic.get('latest'):
        latest_ts = iso_to_ts(str(restic['latest'].get('time') or ''))
    age_hours = age_hours_from_ts(latest_ts)
    restic['latest_timestamp'] = latest_ts
    restic['age_hours'] = age_hours
    restic['overdue_hours'] = overdue_hours
    restic['freshness'] = freshness_state(age_hours, overdue_hours)
    restic['fresh'] = restic['freshness'] == 'fresh'
    restic['timer'] = restic_timer
    restic['maintenance_timer'] = maintenance_timer
    restic['service'] = restic_service
    repository_id = str(restic.get('repository_id') or '')
    latest_snapshot_id = str((restic.get('latest') or {}).get('id') or '')
    restic['check'] = marker_with_provenance(
        RESTIC_CHECK_MARKER,
        repository_check_overdue_hours,
        backup_uuid=uuid,
        repository_id=repository_id,
        latest_snapshot_id=latest_snapshot_id,
    )
    restic['restore_test'] = marker_with_provenance(
        RESTORE_TEST_MARKER,
        restore_test_overdue_hours,
        backup_uuid=uuid,
        repository_id=repository_id,
        latest_snapshot_id=latest_snapshot_id,
    )
    restic['data_check'] = restic_data_check_state(
        backup_uuid=uuid,
        repository_id=repository_id,
    )
    restic['backup_proof'] = backup_proof_state(
        backup_uuid=uuid,
        repository_id=repository_id,
        latest_snapshot_id=latest_snapshot_id,
    )

    ts_latest_timestamp = int((timeshift.get('latest') or {}).get('created_at', 0) or 0)
    timeshift_age_hours = age_hours_from_ts(ts_latest_timestamp)
    timeshift['latest_timestamp'] = ts_latest_timestamp
    timeshift['age_hours'] = timeshift_age_hours
    timeshift['overdue_hours'] = timeshift_overdue_hours
    timeshift['freshness'] = freshness_state(timeshift_age_hours, timeshift_overdue_hours)
    timeshift['fresh'] = timeshift['freshness'] == 'fresh'

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

    manifests_age_hours = age_hours_from_ts(manifests_updated_at)
    manifests_freshness = freshness_state(manifests_age_hours, manifests_overdue_hours) if manifests_known else 'unknown'
    manifests_fresh = manifests_known and manifests_freshness == 'fresh'

    # Phase 3 no longer treats static booleans as durable proof. These are
    # explicit attestations with verification time + provenance metadata.
    credential_evidence = attestation_marker(
        CREDENTIAL_EVIDENCE_MARKER,
        credential_proof_overdue_hours,
        backup_uuid=uuid,
        repository_id=repository_id,
    )
    recovery_media_evidence = attestation_marker(
        RECOVERY_MEDIA_EVIDENCE_MARKER,
        recovery_media_proof_overdue_hours,
        backup_uuid=uuid,
    )
    second_copy_evidence = attestation_marker(
        SECOND_COPY_EVIDENCE_MARKER,
        second_copy_proof_overdue_hours,
        backup_uuid=uuid,
    )
    recovery_media_verified = recovery_media_evidence.get('fresh') is True
    second_copy_verified = second_copy_evidence.get('fresh') is True
    credential_recovery_verified = credential_evidence.get('fresh') is True

    restic_known = bool(restic.get('known')) and restic.get('count') is not None
    restic_count = int(restic.get('count') or 0) if restic_known else 0
    timeshift_known = bool(timeshift.get('known')) and timeshift.get('count') is not None
    timeshift_count = int(timeshift.get('count') or 0) if timeshift_known else 0

    restic_current_failure = restic.get('current_state') in ('failed', 'missing-credential', 'missing-repository', 'blocked-identity')
    restic_ready = restic_known and restic_count > 0 and not restic_current_failure and restic.get('fresh') is True
    timeshift_ready = timeshift_known and timeshift_count > 0 and timeshift.get('fresh') is True

    check_marker_state = restic['check']
    restore_marker_state = restic['restore_test']
    check_ready = check_marker_state.get('ok') is True and check_marker_state.get('fresh') is True
    restore_ready = restore_marker_state.get('ok') is True and restore_marker_state.get('fresh') is True

    core_checks = [
        core_check(
            'restic', 'Encrypted Restic repository',
            restic_ready,
            ('failed' if restic_current_failure else 'stale' if restic_known and restic_count > 0 and restic.get('freshness') == 'stale' else 'ready' if restic_ready else 'missing' if restic_known else 'unknown'),
            (
                restic.get('current_error')
                or (
                    f'{restic_count} matching snapshot(s) · host {expected_hostname} · {restic.get("freshness", "unknown")}'
                    if restic_known else
                    'Machine-scoped snapshot inventory has not been verified yet.'
                )
            ),
            bool(restic.get('cached')),
        ),
        core_check(
            'restore-test', 'Actual restore test',
            restore_ready,
            ('failed' if restore_marker_state.get('known') and restore_marker_state.get('ok') is False else 'stale' if restore_marker_state.get('ok') is True and not restore_marker_state.get('fresh') else 'ready' if restore_ready else 'untested'),
            ('A file was restored and matched byte-for-byte.' if restore_ready else 'Restore verification is stale.' if restore_marker_state.get('ok') is True else 'Run Verify restore while the backup HDD is mounted.'),
        ),
        core_check(
            'timeshift', 'Timeshift restore points',
            timeshift_ready,
            ('stale' if timeshift_known and timeshift_count > 0 and timeshift.get('freshness') == 'stale' else 'ready' if timeshift_ready else 'missing' if timeshift_known else 'unknown'),
            (f'{timeshift_count} restore point(s) verified · {timeshift.get("freshness", "unknown")}' if timeshift_known else 'Restore-point inventory has not been verified yet.'),
            bool(timeshift.get('cached')),
        ),
        core_check(
            'guide', 'Recovery documentation', guide_known,
            'ready' if guide_known else ('missing' if disk['filesystem_accessible'] else 'unknown'),
            'RESTORE.md is available.' if guide_known else 'Mount the backup HDD to verify RESTORE.md.',
            not disk['filesystem_accessible'] and guide_known,
        ),
        core_check(
            'manifests', 'Arch system manifests', manifests_fresh,
            ('stale' if manifests_known and not manifests_fresh else 'ready' if manifests_fresh else 'missing' if disk['filesystem_accessible'] else 'unknown'),
            ('Package and system-state manifests are current.' if manifests_fresh else f'Recovery manifests are {manifests_age_hours:.1f} hours old.' if manifests_known and manifests_age_hours is not None else 'Mount the backup HDD to verify recovery manifests.'),
            not disk['filesystem_accessible'] and manifests_known,
        ),
        core_check(
            'credential-recovery', 'Restic recovery credential',
            credential_recovery_verified,
            'ready' if credential_recovery_verified else 'stale' if credential_evidence.get('known') else 'pending',
            (
                f'External credential recovery verified · {credential_evidence.get("label") or "external recovery method"}.'
                if credential_recovery_verified else
                f'Credential recovery proof is {credential_evidence.get("age_hours")} hours old.'
                if credential_evidence.get('known') else
                'Not verified. The Restic password must survive loss of the primary system disk.'
            ),
        ),
        core_check(
            'recovery-media', 'Bootable recovery media', recovery_media_verified,
            'ready' if recovery_media_verified else 'stale' if recovery_media_evidence.get('known') else 'pending',
            (
                f'Bootable recovery media verified · {recovery_media_evidence.get("label") or "recovery media"}.'
                if recovery_media_verified else
                f'Recovery-media proof is {recovery_media_evidence.get("age_hours")} hours old.'
                if recovery_media_evidence.get('known') else
                'Not verified yet. A stored ISO alone is not counted as bootable recovery media.'
            ),
        ),
    ]
    core_ready = sum(1 for item in core_checks if item['ok'])

    data_check_state = restic.get('data_check') or {}
    data_check_ok = bool(
        data_check_state.get('known')
        and data_check_state.get('ok') is True
        and data_check_state.get('identity_valid') is not False
    )
    data_subset = data_check_state.get('subset')
    data_total = data_check_state.get('total_subsets')

    resilience_checks = [
        core_check(
            'second-copy', 'Second independent copy', second_copy_verified,
            'ready' if second_copy_verified else 'stale' if second_copy_evidence.get('known') else 'recommended',
            (
                f'Independent copy verified · {second_copy_evidence.get("label") or "second copy"}.'
                if second_copy_verified else
                f'Second-copy proof is {second_copy_evidence.get("age_hours")} hours old.'
                if second_copy_evidence.get('known') else
                'Recommended for protection against failure, theft, or loss of the primary backup HDD.'
            ),
        ),
        core_check(
            'repository-data', 'Repository data-pack verification', data_check_ok,
            'ready' if data_check_ok else 'failed' if data_check_state.get('known') and data_check_state.get('ok') is False else 'recommended',
            (
                f'Data subset {data_subset}/{data_total} verified; next subset {data_check_state.get("next_subset")}/{data_total}.'
                if data_check_ok and data_subset and data_total else
                str(data_check_state.get('detail') or 'Run Check repository to rotate through stored data packs.')
                if data_check_state.get('known') else
                'Run Check repository to verify structure and rotate through stored data packs.'
            ),
        ),
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

    if restic.get('current_state') == 'missing-credential':
        attention.append({
            'severity': 'critical', 'tag': 'CREDENTIAL', 'title': 'Restic password file is missing',
            'detail': 'The repository cannot be opened until the configured Restic password file is restored.',
        })
    elif restic.get('current_state') == 'missing-repository':
        attention.append({
            'severity': 'critical', 'tag': 'REPOSITORY', 'title': 'Restic repository is missing',
            'detail': 'The verified backup filesystem is mounted, but the configured Restic repository directory is missing.',
        })
    elif restic.get('current_state') == 'failed':
        attention.append({
            'severity': 'critical', 'tag': 'ERROR', 'title': 'Restic repository query failed',
            'detail': str(restic.get('current_error') or 'Restic could not verify the configured repository.')[:260],
        })

    if age_hours is not None and age_hours > overdue_hours:
        attention.append({
            'severity': 'warning', 'tag': 'DUE', 'title': 'Backup overdue',
            'detail': f'Last known Restic snapshot is {age_hours:.1f} hours old.',
        })
    if timeshift_age_hours is not None and timeshift_age_hours > timeshift_overdue_hours:
        attention.append({
            'severity': 'warning', 'tag': 'DUE', 'title': 'Restore point overdue',
            'detail': f'Latest Timeshift restore point is {timeshift_age_hours:.1f} hours old; target is {timeshift_overdue_hours} hours.',
        })
    if manifests_known and not manifests_fresh:
        attention.append({
            'severity': 'warning', 'tag': 'DUE', 'title': 'Recovery manifests are stale',
            'detail': f'Recovery manifests are {manifests_age_hours:.1f} hours old; refresh them when the backup disk is mounted.',
        })

    if check_marker_state.get('known') and check_marker_state.get('ok') is False:
        attention.append({
            'severity': 'critical', 'tag': 'VERIFY', 'title': 'Repository integrity check failed',
            'detail': str(check_marker_state.get('detail') or 'The most recent repository check failed.')[:260],
        })
    elif check_marker_state.get('provenance_state') == 'identity-mismatch':
        attention.append({
            'severity': 'unverified', 'tag': 'IDENTITY', 'title': 'Repository-check proof belongs to different backup identity',
            'detail': 'Run Check repository against the currently configured repository.',
        })
    elif check_marker_state.get('provenance_state') == 'superseded':
        attention.append({
            'severity': 'unverified', 'tag': 'VERIFY', 'title': 'Repository check predates the latest snapshot',
            'detail': 'A newer matching snapshot exists than the snapshot covered by the last repository verification.',
        })
    elif check_marker_state.get('ok') is True and not check_marker_state.get('fresh'):
        attention.append({
            'severity': 'unverified', 'tag': 'DUE', 'title': 'Repository check is stale',
            'detail': f'The last repository check is {check_marker_state.get("age_hours")} hours old.',
        })

    if restore_marker_state.get('known') and restore_marker_state.get('ok') is False:
        attention.append({
            'severity': 'critical', 'tag': 'RESTORE', 'title': 'Restore verification failed',
            'detail': str(restore_marker_state.get('detail') or 'The most recent real restore verification failed.')[:260],
        })
    elif restore_marker_state.get('provenance_state') == 'identity-mismatch':
        attention.append({
            'severity': 'unverified', 'tag': 'IDENTITY', 'title': 'Restore proof belongs to different backup identity',
            'detail': 'Run Verify restore against the currently configured backup repository.',
        })
    elif restore_marker_state.get('provenance_state') == 'superseded':
        attention.append({
            'severity': 'unverified', 'tag': 'VERIFY', 'title': 'Latest snapshot has not been restore-tested',
            'detail': 'A newer matching snapshot exists than the one covered by the last real restore verification.',
        })
    elif restore_marker_state.get('ok') is True and not restore_marker_state.get('fresh'):
        attention.append({
            'severity': 'unverified', 'tag': 'DUE', 'title': 'Restore verification is stale',
            'detail': f'The last real restore verification is {restore_marker_state.get("age_hours")} hours old.',
        })

    backup_proof = restic.get('backup_proof') or {}
    if restic_known and restic_count > 0 and not backup_proof.get('current'):
        attention.append({
            'severity': 'unverified',
            'tag': 'PROOF',
            'title': 'Latest snapshot lacks Backup Recovery action proof',
            'detail': (
                'The latest matching snapshot exists, but it was not yet linked to a successful '
                'post-backup verification marker from this action engine.'
            ),
        })

    if credential_evidence.get('known') and not credential_recovery_verified:
        attention.append({
            'severity': 'unverified', 'tag': 'DUE', 'title': 'Recovery credential proof is stale',
            'detail': 'Re-confirm that the Restic recovery credential still exists outside the primary system disk.',
        })
    elif not credential_evidence.get('known'):
        attention.append({
            'severity': 'unverified', 'tag': 'CREDENTIAL', 'title': 'Recovery credential survivability is unverified',
            'detail': 'The encrypted repository may be unusable after primary-disk loss unless the Restic credential is stored independently.',
        })

    if recovery_media_evidence.get('known') and not recovery_media_verified:
        attention.append({
            'severity': 'unverified', 'tag': 'DUE', 'title': 'Bootable recovery-media proof is stale',
            'detail': 'Re-verify that the recovery medium still boots and is available.',
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
        elif smart.get('condition') == 'unverified':
            attention.append({
                'severity': 'unverified', 'tag': 'UNVERIFIED', 'title': 'SMART health is not verified',
                'detail': 'SMART data was readable, but no definitive overall-health result was detected.',
            })
        elif int(smart.get('historical_error_count') or 0) > 0:
            attention.append({
                'severity': 'history', 'tag': 'HISTORY', 'title': 'Historical ATA errors',
                'detail': f"ATA log contains {smart.get('historical_error_count')} historical error(s); current bad-sector counters are zero.",
            })
    if smart_error and device.get('connected'):
        attention.append({
            'severity': 'unverified', 'tag': 'UNVERIFIED', 'title': 'SMART status could not be refreshed',
            'detail': smart_error[:260],
        })
    if disk['filesystem_accessible'] and not restic['check'].get('known'):
        attention.append({
            'severity': 'unverified', 'tag': 'UNVERIFIED', 'title': 'Repository integrity has not been checked here',
            'detail': 'Run Check repository when convenient.',
        })

    critical = any(item['severity'] == 'critical' for item in attention)
    overall = derive_overall(
        critical=critical,
        restic_ready=restic_ready,
        restic_stale=restic_known and restic_count > 0 and restic.get('freshness') == 'stale',
        timeshift_ready=timeshift_ready,
        timeshift_stale=timeshift_known and timeshift_count > 0 and timeshift.get('freshness') == 'stale',
        disk=disk,
    )

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
        'manifests_age_hours': manifests_age_hours,
        'manifests_overdue_hours': manifests_overdue_hours,
        'manifests_freshness': manifests_freshness,
        'manifests_fresh': manifests_fresh,
        'iso_files': iso_files,
        'credential_evidence': credential_evidence,
        'recovery_media_evidence': recovery_media_evidence,
        'second_copy_evidence': second_copy_evidence,
        'recovery_media_verified': recovery_media_verified,
        'credential_recovery_verified': credential_recovery_verified,
        'second_copy_verified': second_copy_verified,
    }

    install_meta = read_json(INSTALL_FILE, {})
    if not isinstance(install_meta, dict):
        install_meta = {}
    deployment = {
        'version': str(install_meta.get('version') or ''),
        'ui_contract': str(install_meta.get('ui_contract') or ''),
        'backend_revision': str(install_meta.get('backend_revision') or ''),
        'state_schema': install_meta.get('state_schema'),
        'installed_at': int(install_meta.get('installed_at') or 0),
        'source': str(install_meta.get('source') or ''),
        'phase': str(install_meta.get('phase') or ''),
        'release_stage': str(install_meta.get('release_stage') or ''),
        'local_closure': bool(install_meta.get('local_closure')),
        'source_repo_detected': bool((install_meta.get('source_repo') or {}).get('detected')) if isinstance(install_meta.get('source_repo'), dict) else False,
        'source_commit': str((install_meta.get('source_repo') or {}).get('commit') or '') if isinstance(install_meta.get('source_repo'), dict) else '',
        'source_dirty': bool((install_meta.get('source_repo') or {}).get('dirty')) if isinstance(install_meta.get('source_repo'), dict) else False,
        'source_verified': bool((install_meta.get('source_repo') or {}).get('verified')) if isinstance(install_meta.get('source_repo'), dict) else False,
        'ci_verified': bool((install_meta.get('ci') or {}).get('verified')) if isinstance(install_meta.get('ci'), dict) else False,
        'integrity_file_count': len(install_meta.get('files') or {}) if isinstance(install_meta.get('files'), dict) else 0,
    }

    state = {
        'schema_version': SCHEMA_VERSION,
        'ui_contract': UI_CONTRACT,
        'backend_revision': BACKEND_REVISION,
        'deployment': deployment,
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
    write_json_atomic(EVIDENCE_FILE, evidence, 0o640)
    write_json_atomic(STATE_FILE, state, 0o640)
    secure_user_readable_state(STATE_FILE, str(cfg.get('user') or '').strip())
    return 0


def main() -> int:
    lock_path = Path('/run/backup-recovery/state-collector.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+', encoding='utf-8') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            return collect_state_once()
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


if __name__ == '__main__':
    raise SystemExit(main())
