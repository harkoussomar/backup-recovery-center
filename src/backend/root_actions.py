#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CONFIG_FILE = Path('/etc/backup-recovery/config.json')
STATE_DIR = Path('/var/lib/backup-recovery')
ACTIVITY_FILE = STATE_DIR / 'activity.json'
ACTION_STATE_FILE = STATE_DIR / 'action-state.json'
ACTION_LOCK_FILE = Path('/run/backup-recovery/storage.lock')
ACTIVITY_LOCK_FILE = Path('/run/backup-recovery/activity.lock')
RESTIC_CHECK_MARKER = STATE_DIR / 'restic-check-ok.json'
RESTIC_DATA_CHECK_MARKER = STATE_DIR / 'restic-data-check.json'
RESTORE_TEST_MARKER = STATE_DIR / 'restore-test-ok.json'
BACKUP_PROOF_MARKER = STATE_DIR / 'backup-proof.json'
COLLECTOR = Path('/usr/local/lib/backup-recovery/state_collector.py')
BACKEND_REVISION = '1.6.0-phase4-final'


def run(cmd: list[str], timeout: int = 3600, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(cmd, 124, exc.stdout or '', exc.stderr or f'Timed out after {timeout}s')


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except Exception:
        return default


def write_json_atomic(path: Path, data, mode: int = 0o644) -> None:
    """Durably replace a JSON file without sharing a predictable temp path."""
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


def lock_file(path: Path, *, nonblocking: bool = False):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a+', encoding='utf-8')
    flags = fcntl.LOCK_EX | (fcntl.LOCK_NB if nonblocking else 0)
    try:
        fcntl.flock(handle.fileno(), flags)
    except Exception:
        handle.close()
        raise
    return handle


def record(kind: str, title: str, detail: str, severity: str = 'info') -> None:
    lock = lock_file(ACTIVITY_LOCK_FILE)
    try:
        data = read_json(ACTIVITY_FILE, [])
        if not isinstance(data, list):
            data = []
        data.append({
            'time': int(time.time()),
            'kind': kind,
            'title': title,
            'detail': detail,
            'severity': severity,
        })
        write_json_atomic(ACTIVITY_FILE, data[-120:], 0o640)
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


_ACTION_CONTEXT: dict[str, object] = {}


def _make_user_readable(path: Path, config: dict) -> None:
    user = str(config.get('user') or '').strip()
    if not user:
        return
    try:
        pw = pwd.getpwnam(user)
        os.chown(path, 0, pw.pw_gid)
        os.chmod(path, 0o640)
    except (KeyError, PermissionError, OSError):
        pass



ACTION_LABELS = {
    'backup': 'Backup',
    'timeshift': 'Timeshift restore point',
    'restic-check': 'Repository verification',
    'restore-test': 'Restore verification',
    'smart-short': 'SMART short test',
    'smart-long': 'SMART extended test',
    'mount': 'Backup HDD mount',
    'eject': 'Backup HDD safe eject',
    'refresh-manifests': 'Recovery manifest refresh',
}

DEFAULT_NOTIFY_START = {'backup', 'timeshift', 'restic-check', 'restore-test', 'smart-long'}
DEFAULT_NOTIFY_SUCCESS = {'backup', 'timeshift', 'restic-check', 'restore-test', 'refresh-manifests'}
DEFAULT_NOTIFY_FAILURE = {'*'}


def format_duration(seconds: int) -> str:
    value = max(0, int(seconds))
    if value >= 3600:
        return f'{value // 3600}h {(value % 3600) // 60:02d}m'
    if value >= 60:
        return f'{value // 60}m {value % 60:02d}s'
    return f'{value}s'


def notification_enabled_for(config: dict, event: str, action: str) -> bool:
    raw = config.get('notifications')
    policy = raw if isinstance(raw, dict) else {}
    if policy.get('enabled', True) is False:
        return False
    defaults = {
        'start': DEFAULT_NOTIFY_START,
        'success': DEFAULT_NOTIFY_SUCCESS,
        'failure': DEFAULT_NOTIFY_FAILURE,
    }
    configured = policy.get(event)
    if configured is None:
        allowed = defaults.get(event, set())
    elif isinstance(configured, list):
        allowed = {str(item) for item in configured}
    elif configured is True:
        allowed = {'*'}
    else:
        allowed = set()
    return '*' in allowed or action in allowed


def desktop_notify(
    config: dict,
    *,
    event: str,
    action: str,
    title: str,
    body: str,
    urgency: str = 'normal',
) -> bool:
    """Send through the user's freedesktop notification bus without affecting backup success."""
    if not notification_enabled_for(config, event, action):
        return False

    user = str(config.get('user') or '').strip()
    if not user:
        return False
    try:
        pw = pwd.getpwnam(user)
    except KeyError:
        return False

    notify_send = Path('/usr/bin/notify-send')
    runuser = Path('/usr/bin/runuser')
    user_bus = Path(f'/run/user/{pw.pw_uid}/bus')
    if not notify_send.is_file() or not runuser.is_file() or not user_bus.exists():
        return False

    clean_title = str(title).strip()[:160]
    clean_body = re.sub(r'\s+', ' ', str(body).strip())[:900]
    cmd = [
        str(runuser), '-u', user, '--',
        '/usr/bin/env',
        f'XDG_RUNTIME_DIR=/run/user/{pw.pw_uid}',
        f'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{pw.pw_uid}/bus',
        str(notify_send),
        '--app-name=Backup & Recovery',
        '--icon=drive-harddisk-symbolic',
        f'--urgency={urgency}',
        '--expire-time=7000',
        clean_title,
        clean_body,
    ]
    try:
        cp = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=4,
        )
        return cp.returncode == 0
    except Exception:
        return False


def notify_action_start(config: dict, action: str) -> None:
    label = ACTION_LABELS.get(action, action.replace('-', ' ').title())
    desktop_notify(
        config,
        event='start',
        action=action,
        title=f'{label} started',
        body='Backup & Recovery is running this operation in the background.',
        urgency='low',
    )


def notify_action_success(config: dict, action: str, started_at: int, message: str) -> None:
    label = ACTION_LABELS.get(action, action.replace('-', ' ').title())
    elapsed = format_duration(int(time.time()) - int(started_at))
    detail = message or f'{label} completed successfully.'
    desktop_notify(
        config,
        event='success',
        action=action,
        title=f'{label} completed',
        body=f'{detail} · {elapsed}',
        urgency='normal',
    )


def notify_action_failure(config: dict, action: str, started_at: int, message: str) -> None:
    if str(message).startswith('Busy:'):
        return
    label = ACTION_LABELS.get(action, action.replace('-', ' ').title())
    elapsed = format_duration(int(time.time()) - int(started_at))
    desktop_notify(
        config,
        event='failure',
        action=action,
        title=f'{label} failed',
        body=f'{message} · after {elapsed}',
        urgency='critical',
    )

def publish_action_state(
    config: dict,
    action: str,
    state: str,
    phase: str,
    *,
    progress: dict | None = None,
    message: str = '',
    started_at: int | None = None,
    finished_at: int | None = None,
) -> None:
    now = int(time.time())
    if started_at is None:
        started_at = int(_ACTION_CONTEXT.get('started_at') or now)
    payload = {
        'schema_version': 1,
        'action': action,
        'state': state,
        'phase': phase,
        'started_at': started_at,
        'updated_at': now,
        'finished_at': finished_at,
        'message': message,
        'progress': progress or {},
    }
    write_json_atomic(ACTION_STATE_FILE, payload, 0o640)
    _make_user_readable(ACTION_STATE_FILE, config)


def set_action_phase(config: dict, phase: str, progress: dict | None = None) -> None:
    action = str(_ACTION_CONTEXT.get('action') or '')
    if not action:
        return
    publish_action_state(config, action, 'running', phase, progress=progress)


def current_action_state() -> dict:
    data = read_json(ACTION_STATE_FILE, {})
    return data if isinstance(data, dict) else {}


def restic_progress_from_text(text: str) -> dict:
    latest: dict = {}
    for raw in text.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        start = raw.find('{')
        if start < 0:
            continue
        try:
            item = json.loads(raw[start:])
        except Exception:
            continue
        if not isinstance(item, dict) or item.get('message_type') != 'status':
            continue
        latest = item
    if not latest:
        return {}
    result: dict[str, object] = {}
    try:
        value = float(latest.get('percent_done'))
        result['percent'] = max(0.0, min(100.0, value * 100.0))
    except (TypeError, ValueError):
        pass
    for source, target in (
        ('files_done', 'files_done'),
        ('total_files', 'files_total'),
        ('bytes_done', 'bytes_done'),
        ('total_bytes', 'bytes_total'),
        ('seconds_remaining', 'eta_seconds'),
    ):
        value = latest.get(source)
        if isinstance(value, (int, float)):
            result[target] = value
    return result


def read_restic_progress(unit: str, since_ts: int) -> dict:
    cp = run([
        'journalctl', '-u', unit, '--since', f'@{max(0, since_ts - 2)}',
        '--no-pager', '-o', 'cat', '-n', '160',
    ], timeout=8)
    if cp.returncode != 0:
        return {}
    return restic_progress_from_text(cp.stdout)


def run_systemd_job_with_progress(config: dict, unit: str, timeout: int) -> subprocess.CompletedProcess[str]:
    """Start a systemd job and keep the global operation record live while it runs."""
    started = int(_ACTION_CONTEXT.get('started_at') or time.time())
    try:
        proc = subprocess.Popen(
            ['systemctl', 'start', unit],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        return subprocess.CompletedProcess(['systemctl', 'start', unit], 127, '', str(exc))

    deadline = time.monotonic() + timeout
    last_progress: dict = {}
    while proc.poll() is None:
        progress = read_restic_progress(unit, started)
        if progress:
            last_progress = progress
            set_action_phase(config, 'Backing up protected files', progress)
        else:
            set_action_phase(config, 'Restic backup running', last_progress)
        if time.monotonic() >= deadline:
            proc.kill()
            out, err = proc.communicate()
            return subprocess.CompletedProcess(proc.args, 124, out or '', err or f'Timed out after {timeout}s')
        time.sleep(2)
    out, err = proc.communicate()
    rc = proc.returncode or 0
    if rc != 0:
        return subprocess.CompletedProcess(proc.args, rc, out or '', err or '')

    # Type=simple/notify units can report start success before their workload
    # exits. Keep the Backup Recovery wrapper alive until the external service
    # is no longer active, so the global lock and UI busy state remain true.
    while True:
        state = run(['systemctl', 'is-active', unit], timeout=5).stdout.strip()
        if state not in ('active', 'activating', 'reloading'):
            break
        progress = read_restic_progress(unit, started)
        if progress:
            last_progress = progress
            set_action_phase(config, 'Backing up protected files', progress)
        else:
            set_action_phase(config, 'Restic backup running', last_progress)
        if time.monotonic() >= deadline:
            return subprocess.CompletedProcess(proc.args, 124, out or '', f'{unit} did not finish within {timeout}s')
        time.sleep(2)

    show = run(['systemctl', 'show', unit, '--property=Result,ExecMainStatus', '--no-pager'], timeout=8)
    result = ''
    status = 0
    for line in show.stdout.splitlines():
        if line.startswith('Result='):
            result = line.split('=', 1)[1].strip()
        elif line.startswith('ExecMainStatus='):
            try:
                status = int(line.split('=', 1)[1].strip())
            except ValueError:
                status = 0
    if result and result != 'success':
        return subprocess.CompletedProcess(proc.args, status or 1, out or '', err or f'{unit} finished with result {result}')
    if status != 0:
        return subprocess.CompletedProcess(proc.args, status, out or '', err or f'{unit} exited with status {status}')
    return subprocess.CompletedProcess(proc.args, 0, out or '', err or '')



def restic_base(config: dict) -> list[str]:
    return [
        'restic', '-r', str(config['restic_repo']),
        '--password-file', str(config['restic_password_file']),
        '--cache-dir', str(config['restic_cache_dir']),
    ]


def restic_repository_id(config: dict) -> str:
    cp = run(restic_base(config) + ['cat', 'config'], timeout=60)
    if cp.returncode != 0:
        raise RuntimeError((cp.stderr or cp.stdout).strip() or 'Unable to read Restic repository identity.')
    try:
        payload = json.loads(cp.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'Invalid Restic repository config JSON: {exc}') from exc
    repo_id = str(payload.get('id') or '').strip()
    if not repo_id:
        raise RuntimeError('Restic repository did not expose a stable repository ID.')
    return repo_id


def _snapshot_ts(value: str) -> int:
    from datetime import datetime
    try:
        return int(datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp())
    except (TypeError, ValueError):
        return 0


def expected_restic_hostname(config: dict) -> str:
    return str(config.get('restic_expected_hostname') or socket.gethostname()).strip()


def snapshot_matches_scope(item: dict, config: dict) -> bool:
    expected_host = expected_restic_hostname(config)
    if expected_host and str(item.get('hostname') or '') != expected_host:
        return False

    required_tags = {str(v) for v in (config.get('restic_required_tags') or []) if str(v)}
    tags = {str(v) for v in (item.get('tags') or []) if str(v)}
    if required_tags and not required_tags.issubset(tags):
        return False

    required_paths = {str(v) for v in (config.get('restic_required_paths') or []) if str(v)}
    paths = {str(v) for v in (item.get('paths') or []) if str(v)}
    if required_paths and not required_paths.issubset(paths):
        return False
    return True


def restic_matching_snapshots(config: dict) -> list[dict]:
    cp = run(restic_base(config) + ['snapshots', '--json'], timeout=90)
    if cp.returncode != 0:
        raise RuntimeError((cp.stderr or cp.stdout).strip() or 'Unable to query Restic snapshots.')
    try:
        rows = json.loads(cp.stdout or '[]')
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'Invalid Restic snapshot JSON: {exc}') from exc
    result = [row for row in rows if isinstance(row, dict) and snapshot_matches_scope(row, config)]
    result.sort(key=lambda item: str(item.get('time') or ''), reverse=True)
    return result


def latest_matching_snapshot(config: dict) -> dict | None:
    rows = restic_matching_snapshots(config)
    return rows[0] if rows else None


def _read_text_if_safe(path: str) -> str:
    p = Path(path)
    try:
        resolved = p.resolve(strict=True)
    except OSError:
        return ''
    allowed = ('/usr/local/', '/opt/', '/usr/bin/', '/usr/lib/', '/home/')
    if not any(str(resolved).startswith(prefix) for prefix in allowed):
        return ''
    try:
        if not resolved.is_file() or resolved.stat().st_size > 2_000_000:
            return ''
        return resolved.read_text(errors='replace')
    except OSError:
        return ''


def external_backup_definition_text() -> str:
    cp = run(['systemctl', 'cat', 'restic-system-backup.service', '--no-pager'], timeout=10)
    text = cp.stdout or ''
    # Inspect direct absolute executable/script references as well. This is only
    # used to recognize an explicitly configured --skip-if-unchanged policy.
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith('ExecStart='):
            continue
        command = line.split('=', 1)[1].lstrip('-').strip()
        for token in re.findall(r'(?:"([^"]+)"|\'([^\']+)\'|(\S+))', command):
            value = next((part for part in token if part), '')
            if value.startswith('/'):
                text += '\n' + _read_text_if_safe(value)
    return text


def backup_allows_unchanged_snapshot(config: dict) -> bool:
    explicit = config.get('restic_allow_unchanged_backup')
    if isinstance(explicit, bool):
        return explicit
    return '--skip-if-unchanged' in external_backup_definition_text()


def write_marker(path: Path, config: dict, payload: dict) -> None:
    write_json_atomic(path, payload, 0o640)
    _make_user_readable(path, config)


def run_bytes(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(cmd, 124, exc.stdout or b'', exc.stderr or f'Timed out after {timeout}s'.encode())


def ensure_secure_dir(path: Path, config: dict) -> None:
    """Create/verify a root-owned, desktop-group-readable non-symlink directory."""
    if path.exists() and path.is_symlink():
        raise RuntimeError(f'Refusing symlinked recovery directory: {path}')
    path.mkdir(parents=True, exist_ok=True)
    st = path.lstat()
    if not path.is_dir() or path.is_symlink():
        raise RuntimeError(f'Unsafe recovery directory: {path}')
    user = str(config.get('user') or '').strip()
    if not user:
        raise RuntimeError('Desktop user is not configured.')
    gid = pwd.getpwnam(user).pw_gid
    os.chown(path, 0, gid)
    os.chmod(path, 0o750)


def secure_copy_file(source: Path, dest: Path, config: dict) -> None:
    if not source.is_file():
        return
    ensure_secure_dir(dest.parent, config)
    user = str(config.get('user') or '').strip()
    gid = pwd.getpwnam(user).pw_gid
    fd, tmp_name = tempfile.mkstemp(prefix=f'.{dest.name}.', suffix='.tmp', dir=dest.parent)
    tmp = Path(tmp_name)
    try:
        with source.open('rb') as src, os.fdopen(fd, 'wb') as out:
            shutil.copyfileobj(src, out)
            out.flush()
            os.fsync(out.fileno())
        os.chown(tmp, 0, gid)
        os.chmod(tmp, 0o640)
        os.replace(tmp, dest)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass

def collect_state() -> None:
    try:
        run(['/usr/bin/python3', str(COLLECTOR)], timeout=90)
    except Exception:
        pass


def cfg() -> dict:
    data = read_json(CONFIG_FILE, {})
    if not data:
        raise RuntimeError('Backup Recovery configuration is missing.')
    return data


def require_mount(mountpoint: str) -> None:
    cp = run(['mountpoint', '-q', mountpoint], timeout=5)
    if cp.returncode != 0:
        raise RuntimeError(f'Backup disk is not mounted at {mountpoint}.')


def mounted_uuid(mountpoint: str) -> str:
    cp = run(['findmnt', '-rn', '-o', 'UUID', mountpoint], timeout=5)
    return cp.stdout.strip() if cp.returncode == 0 else ''


def verify_expected_mount(config: dict) -> None:
    mountpoint = str(config.get('mountpoint') or '').strip()
    expected = str(config.get('backup_uuid') or '').strip()
    if not mountpoint:
        raise RuntimeError('Backup mountpoint is not configured.')
    if not expected:
        raise RuntimeError('Backup filesystem UUID is not configured.')

    require_mount(mountpoint)
    actual = mounted_uuid(mountpoint).strip()

    # A missing UUID is not proof of identity. Recovery software must fail
    # closed rather than treating "unknown" as the expected filesystem.
    if not actual:
        raise RuntimeError(
            f'Filesystem mounted at {mountpoint}, but its UUID could not be verified. '
            'Backup/recovery actions were blocked.'
        )
    if actual != expected:
        raise RuntimeError(
            f'Wrong filesystem mounted at {mountpoint}: expected UUID {expected}, found {actual}.'
        )


def resolve_disk(uuid: str) -> str:
    link = Path('/dev/disk/by-uuid') / uuid
    if not link.exists():
        raise RuntimeError('Backup HDD is not connected.')
    part = str(link.resolve())
    parent = run(['lsblk', '-ndo', 'PKNAME', part], timeout=5).stdout.strip()
    if not parent:
        raise RuntimeError(f'Unable to resolve physical disk for {part}.')
    return '/dev/' + parent


def action_backup(config: dict) -> None:
    set_action_phase(config, 'Verifying backup disk')
    verify_expected_mount(config)
    if not Path(config['restic_repo']).is_dir():
        raise RuntimeError('Restic repository directory is missing on the mounted backup disk.')
    if not Path(config['restic_password_file']).is_file():
        raise RuntimeError('Restic password file is missing.')

    set_action_phase(config, 'Reading repository identity')
    repo_id_before = restic_repository_id(config)
    before = latest_matching_snapshot(config)
    before_id = str((before or {}).get('id') or '')

    set_action_phase(config, 'Starting Restic backup')
    cp = run_systemd_job_with_progress(config, 'restic-system-backup.service', timeout=7200)
    if cp.returncode != 0:
        raise RuntimeError((cp.stderr or cp.stdout).strip() or 'Restic backup service failed.')

    set_action_phase(config, 'Verifying resulting snapshot')
    repo_id_after = restic_repository_id(config)
    if repo_id_after != repo_id_before:
        raise RuntimeError('Restic repository identity changed while the backup was running.')

    after = latest_matching_snapshot(config)
    if not after:
        raise RuntimeError(
            f'Backup service succeeded, but no snapshot matched the expected host/scope '
            f'({expected_restic_hostname(config)}).'
        )

    after_id = str(after.get('id') or '')
    after_short = str(after.get('short_id') or after_id[:8])
    after_time = str(after.get('time') or '')
    result = 'new-snapshot'
    if before_id and after_id == before_id:
        if not backup_allows_unchanged_snapshot(config):
            write_marker(BACKUP_PROOF_MARKER, config, {
                'ok': False,
                'time': int(time.time()),
                'detail': 'Backup service exited successfully but no new matching snapshot was created.',
                'backup_uuid': str(config.get('backup_uuid') or ''),
                'repository_id': repo_id_after,
                'snapshot_id': after_id,
                'snapshot_time': after_time,
                'hostname': str(after.get('hostname') or ''),
                'backend_revision': BACKEND_REVISION,
            })
            raise RuntimeError(
                'Backup service succeeded, but no new expected Restic snapshot was verified.'
            )
        result = 'unchanged-skip'
    elif _snapshot_ts(after_time) and _snapshot_ts(after_time) < int(_ACTION_CONTEXT.get('started_at') or 0) - 5:
        raise RuntimeError('The newest matching Restic snapshot predates this backup action.')

    proof = {
        'ok': True,
        'time': int(time.time()),
        'detail': (
            f'New matching Restic snapshot {after_short} verified.'
            if result == 'new-snapshot'
            else f'No-change backup verified against existing snapshot {after_short}; service is configured to skip unchanged snapshots.'
        ),
        'result': result,
        'backup_uuid': str(config.get('backup_uuid') or ''),
        'repository_id': repo_id_after,
        'snapshot_id': after_id,
        'snapshot_time': after_time,
        'hostname': str(after.get('hostname') or ''),
        'tags': after.get('tags') or [],
        'paths': after.get('paths') or [],
        'backend_revision': BACKEND_REVISION,
    }
    write_marker(BACKUP_PROOF_MARKER, config, proof)
    _ACTION_CONTEXT['success_message'] = proof['detail']
    record(
        'backup',
        'Restic backup verified',
        f'{proof["detail"]} Repository identity and snapshot scope matched.',
        'success',
    )


def action_timeshift(config: dict) -> None:
    set_action_phase(config, 'Verifying backup disk')
    verify_expected_mount(config)
    before_root = Path(config['mountpoint']) / 'timeshift' / 'snapshots'
    try:
        before = {p.name for p in before_root.iterdir() if p.is_dir()} if before_root.is_dir() else set()
    except OSError:
        before = set()
    set_action_phase(config, 'Creating Timeshift restore point')
    cp = run([
        'timeshift', '--create',
        '--comments', 'Backup & Recovery Center restore point',
        '--tags', 'O',
    ], timeout=7200)
    if cp.returncode != 0:
        detail = (cp.stderr or cp.stdout).strip()
        raise RuntimeError(detail[-1200:] if detail else 'Timeshift snapshot failed.')
    try:
        after = {p.name for p in before_root.iterdir() if p.is_dir()} if before_root.is_dir() else set()
    except OSError:
        after = set()
    created = sorted(after - before)
    detail = f'Created {created[-1]}.' if created else 'Timeshift reported success; snapshot inventory will be refreshed.'
    record('timeshift', 'Restore point created', detail, 'success')


def action_restic_check(config: dict) -> None:
    set_action_phase(config, 'Verifying backup disk')
    verify_expected_mount(config)
    repo_id = restic_repository_id(config)
    latest = latest_matching_snapshot(config)
    snapshot_id = str((latest or {}).get('id') or '')

    total = max(1, int(config.get('restic_data_check_subsets', 4) or 4))
    previous = read_json(RESTIC_DATA_CHECK_MARKER, {})
    next_subset = int(previous.get('next_subset', 1) or 1)
    if (
        str(previous.get('repository_id') or '') != repo_id
        or str(previous.get('backup_uuid') or '') != str(config.get('backup_uuid') or '')
        or int(previous.get('total_subsets', total) or total) != total
    ):
        next_subset = 1
    next_subset = min(max(1, next_subset), total)

    help_cp = run(restic_base(config) + ['check', '--help'], timeout=30)
    subset_supported = '--read-data-subset' in (help_cp.stdout + help_cp.stderr)
    if subset_supported:
        set_action_phase(config, f'Checking structure + data subset {next_subset}/{total}')
        cp = run(
            restic_base(config) + ['check', f'--read-data-subset={next_subset}/{total}'],
            timeout=7200,
        )
        verification_mode = f'structural+data-subset-{next_subset}/{total}'
    else:
        set_action_phase(config, 'Checking Restic repository structure')
        cp = run(restic_base(config) + ['check'], timeout=7200)
        verification_mode = 'structural'

    detail = (cp.stderr or cp.stdout).strip()
    if cp.returncode != 0:
        marker = {
            'ok': False,
            'time': int(time.time()),
            'detail': detail[-1200:] or 'Restic repository verification failed.',
            'verification_mode': verification_mode,
            'backup_uuid': str(config.get('backup_uuid') or ''),
            'repository_id': repo_id,
            'snapshot_id': snapshot_id,
            'backend_revision': BACKEND_REVISION,
        }
        write_marker(RESTIC_CHECK_MARKER, config, marker)
        if subset_supported:
            write_marker(RESTIC_DATA_CHECK_MARKER, config, {
                'known': True,
                'ok': False,
                'time': marker['time'],
                'detail': marker['detail'],
                'subset': next_subset,
                'total_subsets': total,
                'next_subset': next_subset,
                'cycle_completed': False,
                'backup_uuid': marker['backup_uuid'],
                'repository_id': repo_id,
                'snapshot_id': snapshot_id,
                'backend_revision': BACKEND_REVISION,
            })
        raise RuntimeError(detail or 'Restic repository verification failed.')

    if subset_supported:
        data_marker = {
            'known': True,
            'ok': True,
            'time': int(time.time()),
            'detail': detail[-1200:] if detail else f'Repository data subset {next_subset}/{total} verified.',
            'subset': next_subset,
            'total_subsets': total,
            'next_subset': 1 if next_subset >= total else next_subset + 1,
            'cycle_completed': next_subset >= total,
            'backup_uuid': str(config.get('backup_uuid') or ''),
            'repository_id': repo_id,
            'snapshot_id': snapshot_id,
            'backend_revision': BACKEND_REVISION,
        }
        write_marker(RESTIC_DATA_CHECK_MARKER, config, data_marker)

    marker = {
        'ok': True,
        'time': int(time.time()),
        'detail': (
            f'Repository structure and data subset {next_subset}/{total} verified.'
            if subset_supported else
            'Repository structure verified; this Restic build does not expose data-subset verification.'
        ),
        'verification_mode': verification_mode,
        'backup_uuid': str(config.get('backup_uuid') or ''),
        'repository_id': repo_id,
        'snapshot_id': snapshot_id,
        'backend_revision': BACKEND_REVISION,
    }
    write_marker(RESTIC_CHECK_MARKER, config, marker)
    _ACTION_CONTEXT['success_message'] = marker['detail']
    record(
        'check',
        'Restic repository verification completed',
        marker['detail'],
        'success',
    )


def action_restore_test(config: dict) -> None:
    set_action_phase(config, 'Verifying backup disk')
    verify_expected_mount(config)
    repo_id = restic_repository_id(config)
    latest = latest_matching_snapshot(config)
    if not latest:
        raise RuntimeError('No Restic snapshot matches the configured machine/scope.')
    snapshot_id = str(latest.get('id') or '')
    snapshot_short = str(latest.get('short_id') or snapshot_id[:8])
    test_path = str(config.get('restore_test_path') or '/etc/hostname').strip()
    if not test_path.startswith('/') or test_path == '/':
        raise RuntimeError('restore_test_path must be one absolute file path.')

    with tempfile.TemporaryDirectory(prefix='backup-recovery-restore-test-') as tmp:
        cmd = restic_base(config) + [
            'restore', snapshot_id, '--target', tmp, '--include', test_path,
        ]
        set_action_phase(config, f'Restoring {test_path} from {snapshot_short}')
        cp = run(cmd, timeout=900)
        restored = Path(tmp) / test_path.lstrip('/')
        detail = (cp.stderr or cp.stdout).strip()
        if cp.returncode != 0 or not restored.is_file():
            write_marker(RESTORE_TEST_MARKER, config, {
                'ok': False,
                'time': int(time.time()),
                'detail': detail[-1000:] or f'{test_path} was not restored.',
                'backup_uuid': str(config.get('backup_uuid') or ''),
                'repository_id': repo_id,
                'snapshot_id': snapshot_id,
                'test_path': test_path,
                'backend_revision': BACKEND_REVISION,
            })
            raise RuntimeError('Restic restore verification failed.')

        # Compare the restored file with Restic's snapshot stream, not the current
        # live file. A legitimate live-file change after backup must not make a
        # valid restore look broken.
        set_action_phase(config, 'Validating restored content')
        dump_cp = run_bytes(restic_base(config) + ['dump', snapshot_id, test_path], timeout=300)
        restored_bytes = restored.read_bytes()
        if dump_cp.returncode != 0 or dump_cp.stdout != restored_bytes:
            dump_err = dump_cp.stderr.decode(errors='replace') if isinstance(dump_cp.stderr, bytes) else str(dump_cp.stderr or '')
            write_marker(RESTORE_TEST_MARKER, config, {
                'ok': False,
                'time': int(time.time()),
                'detail': dump_err[-1000:] or 'Restored bytes did not match the snapshot stream.',
                'backup_uuid': str(config.get('backup_uuid') or ''),
                'repository_id': repo_id,
                'snapshot_id': snapshot_id,
                'test_path': test_path,
                'backend_revision': BACKEND_REVISION,
            })
            raise RuntimeError('Restic restore verification content check failed.')

        digest = hashlib.sha256(restored_bytes).hexdigest()
        marker = {
            'ok': True,
            'time': int(time.time()),
            'detail': f'{test_path} restored from snapshot {snapshot_short} and matched the snapshot stream.',
            'backup_uuid': str(config.get('backup_uuid') or ''),
            'repository_id': repo_id,
            'snapshot_id': snapshot_id,
            'snapshot_time': str(latest.get('time') or ''),
            'test_path': test_path,
            'restored_sha256': digest,
            'backend_revision': BACKEND_REVISION,
        }
        write_marker(RESTORE_TEST_MARKER, config, marker)
        _ACTION_CONTEXT['success_message'] = marker['detail']
        record(
            'restore-test',
            'Restore test passed',
            f'{test_path} restored from {snapshot_short}; SHA-256 {digest[:12]}… verified.',
            'success',
        )


def action_smart(config: dict, test: str) -> None:
    set_action_phase(config, f'Starting SMART {test} self-test')
    disk = resolve_disk(config['backup_uuid'])
    cp = run(['smartctl', '-t', test, disk], timeout=30)
    text = ((cp.stdout or '') + '\n' + (cp.stderr or '')).strip()
    # smartctl uses a bitmask exit status; old logged errors can make the command
    # non-zero even when a new self-test was accepted successfully.
    accepted = any(token in text.lower() for token in (
        'please wait', 'testing has begun', 'self-test routine in progress',
        'test will complete', 'successful',
    ))
    if cp.returncode != 0 and not accepted:
        raise RuntimeError(text or f'SMART {test} test could not start.')
    label = 'Short' if test == 'short' else 'Extended'
    record('smart', f'{label} SMART test started', text[-600:] or 'Self-test accepted by drive.', 'info')


def action_mount(config: dict) -> None:
    set_action_phase(config, 'Mounting verified backup filesystem')
    mountpoint = config['mountpoint']
    expected = str(config['backup_uuid'])
    # Refuse to mount if the expected disk is not physically present.
    link = Path('/dev/disk/by-uuid') / expected
    if not link.exists():
        raise RuntimeError('Backup HDD is not connected.')
    if run(['mountpoint', '-q', mountpoint], timeout=5).returncode == 0:
        verify_expected_mount(config)
        record('disk', 'Backup disk already mounted', mountpoint, 'info')
        return
    cp = run(['mount', mountpoint], timeout=30)
    if cp.returncode != 0:
        raise RuntimeError((cp.stderr or cp.stdout).strip() or 'Unable to mount backup HDD.')
    try:
        verify_expected_mount(config)
    except Exception:
        run(['umount', mountpoint], timeout=30)
        raise
    record('disk', 'Backup disk mounted', f'{mountpoint} · UUID {expected}', 'success')


def any_backup_work_running() -> list[str]:
    units = [
        'restic-system-backup.service',
        'restic-maintenance.service',
        'backup-recovery-backup.service',
        'backup-recovery-timeshift.service',
        'backup-recovery-restic-check.service',
        'backup-recovery-restore-test.service',
        'backup-recovery-refresh-manifests.service',
        # These units finish shortly after asking the drive to start a test,
        # but include them to close the small start/eject race.
        'backup-recovery-smart-short.service',
        'backup-recovery-smart-long.service',
    ]
    busy: list[str] = []
    for unit in units:
        state = run(['systemctl', 'is-active', unit], timeout=5).stdout.strip()
        if state in ('active', 'activating'):
            busy.append(unit)
    return busy


def smart_self_test_in_progress(config: dict) -> tuple[bool, str]:
    # SMART tests run inside the drive and normally continue after the systemd
    # starter unit exits. Detect that drive-level state before telling the user
    # it is safe to disconnect the HDD.
    try:
        disk = resolve_disk(str(config.get('backup_uuid') or '').strip())
    except Exception:
        return False, ''

    cp = run(['smartctl', '-a', disk], timeout=20)
    text = ((cp.stdout or '') + '\n' + (cp.stderr or '')).strip()
    low = text.lower()
    running = (
        'self-test routine in progress' in low
        or ('self-test execution status:' in low and 'in progress' in low)
    )
    return running, text[-700:]


def action_eject(config: dict) -> None:
    set_action_phase(config, 'Checking active backup work')
    busy = any_backup_work_running()
    if busy:
        raise RuntimeError('Cannot unmount while backup work is active: ' + ', '.join(busy))

    smart_running, _smart_detail = smart_self_test_in_progress(config)
    if smart_running:
        raise RuntimeError(
            'Cannot safely eject while the backup drive is running a SMART self-test. '
            'Wait for the drive test to finish or explicitly abort it first.'
        )

    mountpoint = config['mountpoint']
    if run(['mountpoint', '-q', mountpoint], timeout=5).returncode != 0:
        record('disk', 'Backup disk already unmounted', 'It is safe to disconnect the HDD.', 'info')
        return
    verify_expected_mount(config)
    set_action_phase(config, 'Syncing filesystem buffers')
    sync_cp = run(['sync'], timeout=60)
    if sync_cp.returncode != 0:
        raise RuntimeError('Filesystem sync failed; the backup disk was not unmounted.')
    set_action_phase(config, 'Unmounting backup filesystem')
    cp = run(['umount', mountpoint], timeout=60)
    if cp.returncode != 0:
        raise RuntimeError((cp.stderr or cp.stdout).strip() or 'Unable to unmount backup HDD.')
    if run(['mountpoint', '-q', mountpoint], timeout=5).returncode == 0:
        raise RuntimeError('Unmount command returned success but the backup filesystem is still mounted.')
    record('disk', 'Backup disk safely unmounted', 'Filesystem synced and unmounted. It is safe to disconnect the HDD.', 'success')


def write_text(path: Path, text: str, config: dict) -> None:
    ensure_secure_dir(path.parent, config)
    user = str(config.get('user') or '').strip()
    gid = pwd.getpwnam(user).pw_gid
    fd, tmp_name = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chown(tmp, 0, gid)
        os.chmod(tmp, 0o640)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def action_refresh_manifests(config: dict) -> None:
    set_action_phase(config, 'Verifying backup disk')
    verify_expected_mount(config)
    set_action_phase(config, 'Refreshing recovery manifests')
    user = str(config.get('user') or '').strip()
    if not user:
        raise RuntimeError('Desktop user is not configured.')
    pw = pwd.getpwnam(user)
    arch_state = Path(config['mountpoint']) / 'arch-state'
    recovery = Path(config['mountpoint']) / 'recovery'
    ensure_secure_dir(arch_state, config)
    ensure_secure_dir(recovery, config)

    commands = {
        'packages-explicit.txt': ['pacman', '-Qqe'],
        'packages-official.txt': ['pacman', '-Qqen'],
        'packages-aur.txt': ['pacman', '-Qqem'],
        'all-packages.txt': ['pacman', '-Q'],
        'system-services-enabled.txt': ['systemctl', 'list-unit-files', '--state=enabled', '--no-legend'],
        'kernel.txt': ['uname', '-a'],
        'lsblk.txt': ['lsblk', '-f'],
    }
    for name, command in commands.items():
        cp = run(command, timeout=60)
        if cp.returncode != 0:
            raise RuntimeError(f'{name}: {(cp.stderr or cp.stdout).strip()}')
        write_text(arch_state / name, cp.stdout, config)

    env = dict(os.environ)
    env.update({
        'HOME': pw.pw_dir,
        'USER': user,
        'LOGNAME': user,
        'XDG_RUNTIME_DIR': f'/run/user/{pw.pw_uid}',
        'DBUS_SESSION_BUS_ADDRESS': f'unix:path=/run/user/{pw.pw_uid}/bus',
    })
    cp = run([
        'runuser', '-u', user, '--', 'systemctl', '--user',
        'list-unit-files', '--state=enabled', '--no-legend',
    ], timeout=30, env=env)
    if cp.returncode == 0:
        write_text(arch_state / 'user-services-enabled.txt', cp.stdout, config)

    secure_copy_file(Path('/etc/fstab'), arch_state / 'fstab.txt', config)

    copies = [
        ('/etc/fstab', 'fstab-current.txt'),
        ('/etc/timeshift/timeshift.json', 'timeshift.json'),
        ('/etc/restic/excludes.txt', 'restic-excludes.txt'),
        ('/etc/systemd/system/restic-system-backup.service', 'restic-system-backup.service'),
        ('/etc/systemd/system/restic-system-backup.timer', 'restic-system-backup.timer'),
        ('/etc/systemd/system/restic-maintenance.service', 'restic-maintenance.service'),
        ('/etc/systemd/system/restic-maintenance.timer', 'restic-maintenance.timer'),
    ]
    for src, name in copies:
        source = Path(src)
        if source.exists():
            secure_copy_file(source, recovery / name, config)
    # Never copy /etc/restic/password.
    record('recovery', 'Recovery metadata refreshed', 'Package manifests and recovery configuration were updated.', 'success')


def dispatch_action(action: str, config: dict) -> None:
    if action == 'backup':
        action_backup(config)
    elif action == 'timeshift':
        action_timeshift(config)
    elif action == 'restic-check':
        action_restic_check(config)
    elif action == 'restore-test' or action == 'bootstrap-verify':
        action_restore_test(config)
    elif action == 'smart-short':
        action_smart(config, 'short')
    elif action == 'smart-long':
        action_smart(config, 'long')
    elif action == 'mount':
        action_mount(config)
    elif action == 'eject':
        action_eject(config)
    elif action == 'refresh-manifests':
        action_refresh_manifests(config)
    else:
        raise RuntimeError(f'Unsupported action: {action}')


def main() -> int:
    if os.geteuid() != 0:
        print('This helper must run as root.', file=sys.stderr)
        return 1
    if len(sys.argv) < 2:
        print('Action required.', file=sys.stderr)
        return 2

    action = sys.argv[1]
    config = cfg()
    lock = None
    started_at = int(time.time())
    try:
        try:
            lock = lock_file(ACTION_LOCK_FILE, nonblocking=True)
        except BlockingIOError:
            running = current_action_state()
            label = str(running.get('action') or 'another backup/recovery action')
            raise RuntimeError(f'Busy: {label} is already in progress.')

        # The shared lock protects Backup Recovery actions from each other.
        # Independently scheduled Restic work must also block storage mutations.
        external_busy = []
        for unit in ('restic-system-backup.service', 'restic-maintenance.service'):
            state = run(['systemctl', 'is-active', unit], timeout=5).stdout.strip()
            if state in ('active', 'activating'):
                external_busy.append(unit)
        if external_busy:
            raise RuntimeError('Busy: external backup work is active: ' + ', '.join(external_busy))

        _ACTION_CONTEXT.clear()
        _ACTION_CONTEXT.update({'action': action, 'started_at': started_at})
        publish_action_state(config, action, 'running', 'Preparing action', started_at=started_at)
        notify_action_start(config, action)
        dispatch_action(action, config)
        finished = int(time.time())
        success_message = str(_ACTION_CONTEXT.get('success_message') or f'{action} completed successfully.')
        publish_action_state(
            config, action, 'success', 'Completed',
            message=success_message,
            started_at=started_at, finished_at=finished,
        )
        notify_action_success(config, action, started_at, success_message)
        return 0
    except Exception as exc:
        if lock is not None:
            try:
                publish_action_state(
                    config, action, 'failed', 'Failed', message=str(exc),
                    started_at=started_at, finished_at=int(time.time()),
                )
            except Exception:
                pass
        record(action, f'{action} failed', str(exc), 'error')
        notify_action_failure(config, action, started_at, str(exc))
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        _ACTION_CONTEXT.clear()
        if lock is not None:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            finally:
                lock.close()
        collect_state()


if __name__ == '__main__':
    raise SystemExit(main())
