#!/usr/bin/env python3
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
assert (ROOT / "VERSION").read_text().strip() == "0.1.0-alpha.8"

cfg=json.loads((ROOT/"config/config.example.json").read_text())
assert cfg["user"] == "YOUR_USER"
assert cfg["backup_uuid"] == "CHANGE_ME"
assert cfg["restic_password_file"] == "/etc/restic/password"
assert cfg["restic_data_check_subsets"] >= 1
assert isinstance(cfg["restic_required_paths"], list)
assert isinstance(cfg["restic_required_tags"], list)
assert cfg["timeshift_overdue_hours"] > 0
assert cfg["credential_proof_overdue_hours"] > 0

wrapper=(ROOT/"src/quickshell/modules/ii/backupRecovery/BackupRecovery.qml").read_text()
content=(ROOT/"src/quickshell/modules/ii/backupRecovery/BackupRecoveryContent.qml").read_text()
opbar=(ROOT/"src/quickshell/modules/ii/backupRecovery/GlobalOperationBar.qml").read_text()
helper=(ROOT/"src/quickshell/scripts/backup-recovery/control_center.py").read_text()
collector=(ROOT/"src/backend/state_collector.py").read_text()
actions=(ROOT/"src/backend/root_actions.py").read_text()

assert 'target: "backupRecoveryV160"' in wrapper
assert 'return "1.6.0"' in wrapper
assert "UI_CONTRACT = '1.6.0'" in helper
assert "UI_CONTRACT = '1.6.0'" in collector
assert "BACKEND_REVISION = '1.6.0-phase4-final'" in collector
assert 'expectedUiContract: "1.6.0"' in content
assert "GlobalOperationBar {" in content
assert "Accessible.role: Accessible.StatusBar" in opbar

assert "mounted_identity_verified" in collector
assert "backup_proof" in collector
assert "ACTION_LOCK_FILE" in actions
assert "fcntl.flock" in actions
assert "/run/backup-recovery/storage.lock" in actions
assert "restic_data_check_subsets" in actions
assert "repository_id" in actions
assert "desktop_notify" in actions
assert "notify-send" in actions

for name in (
    "backup-recovery-evidence",
    "backup-recovery-doctor",
    "backup-recovery-selftest",
    "backup-recovery-recovery-drill",
):
    assert (ROOT/"src/tools"/name).is_file(), name

install=(ROOT/"installer/install.sh").read_text()
uninstall=(ROOT/"installer/uninstall.sh").read_text()

for token in (
    'PACKAGE_VERSION="0.1.0-alpha.8"',
    'UI_PROTOCOL="1.6.0"',
    'BACKEND_REVISION="1.6.0-phase4-final"',
    "backupRecoveryV160",
    "backup-recovery-doctor",
    "backup-recovery-selftest",
    "backup-recovery-evidence",
    "backup-recovery-recovery-drill",
    "systemd-hardening.conf",
    "20-backup-recovery-hardening.conf",
    '"release_stage":"public-release"',
    '"files":files',
    "Installed deployment doctor + selftest passed",
    "Transactional rollback snapshot created",
):
    assert token in install, token

assert "backupRecoveryProtocol2" not in install
assert 'UI_PROTOCOL="2"' not in install

for token in (
    'PACKAGE_VERSION="0.1.0-alpha.8"',
    "backup-recovery-doctor",
    "backup-recovery-selftest",
    "backup-recovery-evidence",
    "backup-recovery-recovery-drill",
    "20-backup-recovery-hardening.conf",
    "--purge",
):
    assert token in uninstall, token

doctor=(ROOT/"src/tools/backup-recovery-doctor").read_text()
selftest=(ROOT/"src/tools/backup-recovery-selftest").read_text()
assert "'public-release'" in doctor
assert "'public-release'" in selftest

workflow=(ROOT/".github/workflows/verify.yml").read_text()
assert "bubblewrap" in workflow

sandbox=(ROOT/"tests/test_installer_sandbox.sh").read_text()
assert "--share-net" not in sandbox
assert "bubblewrap is required in CI" in sandbox

readme=(ROOT/"README.md").read_text()
assert "> **Status:** `v0.1.0-alpha.8`" in readme

print("✓ public release contract 0.1.0-alpha.8 / runtime 1.6.0 passed")
