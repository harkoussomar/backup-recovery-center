#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import pwd
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CONFIG_FILE = Path('/etc/backup-recovery/config.json')
STATE_DIR = Path('/var/lib/backup-recovery')
ACTIVITY_FILE = STATE_DIR / 'activity.json'
RESTIC_CHECK_MARKER = STATE_DIR / 'restic-check-ok.json'
RESTORE_TEST_MARKER = STATE_DIR / 'restore-test-ok.json'
COLLECTOR = Path('/usr/local/lib/backup-recovery/state_collector.py')


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


def write_json_atomic(path: Path, data, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def record(kind: str, title: str, detail: str, severity: str = 'info') -> None:
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
    write_json_atomic(ACTIVITY_FILE, data[-120:], 0o600)


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

    # Fail closed: an empty UUID is unverified identity, never a match.
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
    verify_expected_mount(config)
    if not Path(config['restic_repo']).is_dir():
        raise RuntimeError('Restic repository directory is missing on the mounted backup disk.')
    backup_service = str(config.get('backup_service') or 'restic-system-backup.service')
    cp = run(['systemctl', 'start', backup_service], timeout=7200)
    if cp.returncode != 0:
        raise RuntimeError((cp.stderr or cp.stdout).strip() or 'Restic backup service failed.')
    record('backup', 'Restic backup completed', 'Encrypted backup completed successfully.', 'success')


def action_timeshift(config: dict) -> None:
    verify_expected_mount(config)
    before_root = Path(config['mountpoint']) / 'timeshift' / 'snapshots'
    try:
        before = {p.name for p in before_root.iterdir() if p.is_dir()} if before_root.is_dir() else set()
    except OSError:
        before = set()
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
    verify_expected_mount(config)
    cmd = [
        'restic', '-r', config['restic_repo'],
        '--password-file', config['restic_password_file'],
        '--cache-dir', config['restic_cache_dir'],
        'check',
    ]
    cp = run(cmd, timeout=7200)
    detail = (cp.stderr or cp.stdout).strip()
    if cp.returncode != 0:
        write_json_atomic(RESTIC_CHECK_MARKER, {
            'ok': False,
            'time': int(time.time()),
            'detail': detail[-1000:],
        }, 0o600)
        raise RuntimeError(detail or 'Restic check failed.')
    write_json_atomic(RESTIC_CHECK_MARKER, {
        'ok': True,
        'time': int(time.time()),
        'detail': 'no errors were found',
    }, 0o600)
    record('check', 'Restic repository verified', 'Repository check completed with no errors.', 'success')


def action_restore_test(config: dict) -> None:
    verify_expected_mount(config)

    test_path = str(config.get('restore_test_path') or '/etc/hostname').strip()
    if not test_path.startswith('/') or test_path == '/':
        raise RuntimeError('restore_test_path must be an absolute file path.')
    live = Path(test_path)
    if not live.is_file():
        raise RuntimeError(f'Restore-test source file does not exist: {test_path}')

    with tempfile.TemporaryDirectory(prefix='backup-recovery-restore-test-') as tmp:
        cmd = [
            'restic', '-r', config['restic_repo'],
            '--password-file', config['restic_password_file'],
            '--cache-dir', config['restic_cache_dir'],
            'restore', 'latest', '--target', tmp, '--include', test_path,
        ]
        cp = run(cmd, timeout=900)
        restored = Path(tmp) / test_path.lstrip('/')
        detail = (cp.stderr or cp.stdout).strip()
        matched = (
            cp.returncode == 0
            and restored.is_file()
            and live.read_bytes() == restored.read_bytes()
        )
        if not matched:
            write_json_atomic(RESTORE_TEST_MARKER, {
                'ok': False,
                'time': int(time.time()),
                'detail': detail[-1000:] or f'Restored {test_path} did not match the live file.',
            }, 0o600)
            raise RuntimeError('Restic restore verification failed.')

        marker_detail = f'{test_path} restored and matched byte-for-byte'
        write_json_atomic(RESTORE_TEST_MARKER, {
            'ok': True,
            'time': int(time.time()),
            'detail': marker_detail,
        }, 0o600)
        record('restore-test', 'Restore test passed', marker_detail + '.', 'success')


def action_smart(config: dict, test: str) -> None:
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


def any_backup_work_running(config: dict) -> list[str]:
    backup_service = str(config.get('backup_service') or 'restic-system-backup.service')
    units = [
        backup_service,
        'backup-recovery-backup.service',
        'backup-recovery-timeshift.service',
        'backup-recovery-restic-check.service',
        'backup-recovery-restore-test.service',
        'backup-recovery-refresh-manifests.service',
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
    # Drive self-tests outlive the short systemd starter unit, so inspect the
    # physical disk before declaring it safe to unplug.
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
    busy = any_backup_work_running(config)
    if busy:
        raise RuntimeError('Cannot unmount while backup work is active: ' + ', '.join(busy))

    smart_running, _smart_detail = smart_self_test_in_progress(config)
    if smart_running:
        raise RuntimeError(
            'Cannot safely eject while the backup drive is running a SMART self-test. '
            'Wait for the test to finish or explicitly abort it first.'
        )

    mountpoint = config['mountpoint']
    if run(['mountpoint', '-q', mountpoint], timeout=5).returncode != 0:
        record('disk', 'Backup disk already unmounted', 'It is safe to disconnect the HDD.', 'info')
        return
    verify_expected_mount(config)
    sync_cp = run(['sync'], timeout=60)
    if sync_cp.returncode != 0:
        raise RuntimeError('Filesystem sync failed; the backup disk was not unmounted.')
    cp = run(['umount', mountpoint], timeout=60)
    if cp.returncode != 0:
        raise RuntimeError((cp.stderr or cp.stdout).strip() or 'Unable to unmount backup HDD.')
    if run(['mountpoint', '-q', mountpoint], timeout=5).returncode == 0:
        raise RuntimeError('Unmount command returned success but the backup filesystem is still mounted.')
    record('disk', 'Backup disk safely unmounted', 'Filesystem synced and unmounted. It is safe to disconnect the HDD.', 'success')


def write_text(path: Path, text: str, uid: int | None = None, gid: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(text)
    os.replace(tmp, path)
    if uid is not None and gid is not None:
        os.chown(path, uid, gid)


def action_refresh_manifests(config: dict) -> None:
    verify_expected_mount(config)
    user = str(config.get('user') or '').strip()
    if not user:
        raise RuntimeError('Configuration field "user" is required.')
    pw = pwd.getpwnam(user)
    arch_state = Path(config['mountpoint']) / 'arch-state'
    recovery = Path(config['mountpoint']) / 'recovery'
    arch_state.mkdir(parents=True, exist_ok=True)
    recovery.mkdir(parents=True, exist_ok=True)

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
        write_text(arch_state / name, cp.stdout, pw.pw_uid, pw.pw_gid)

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
        write_text(arch_state / 'user-services-enabled.txt', cp.stdout, pw.pw_uid, pw.pw_gid)

    shutil.copy2('/etc/fstab', arch_state / 'fstab.txt')
    os.chown(arch_state / 'fstab.txt', pw.pw_uid, pw.pw_gid)

    backup_service = str(config.get('backup_service') or 'restic-system-backup.service')
    backup_timer = str(config.get('backup_timer') or 'restic-system-backup.timer')
    maintenance_service = str(config.get('maintenance_service') or 'restic-maintenance.service')
    maintenance_timer = str(config.get('maintenance_timer') or 'restic-maintenance.timer')
    copies = [
        ('/etc/fstab', 'fstab-current.txt'),
        ('/etc/timeshift/timeshift.json', 'timeshift.json'),
        ('/etc/restic/excludes.txt', 'restic-excludes.txt'),
        (f'/etc/systemd/system/{backup_service}', backup_service),
        (f'/etc/systemd/system/{backup_timer}', backup_timer),
        (f'/etc/systemd/system/{maintenance_service}', maintenance_service),
        (f'/etc/systemd/system/{maintenance_timer}', maintenance_timer),
    ]
    for src, name in copies:
        source = Path(src)
        if source.exists():
            shutil.copy2(source, recovery / name)
    # Never copy /etc/restic/password.
    record('recovery', 'Recovery metadata refreshed', 'Package manifests and recovery configuration were updated.', 'success')


def main() -> int:
    if os.geteuid() != 0:
        print('This helper must run as root.', file=sys.stderr)
        return 1
    if len(sys.argv) < 2:
        print('Action required.', file=sys.stderr)
        return 2

    action = sys.argv[1]
    config = cfg()
    try:
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
        return 0
    except Exception as exc:
        record(action, f'{action} failed', str(exc), 'error')
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        collect_state()


if __name__ == '__main__':
    raise SystemExit(main())
