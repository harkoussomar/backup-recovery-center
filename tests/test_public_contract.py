#!/usr/bin/env python3
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]

cfg = json.loads((ROOT / "config/config.example.json").read_text())
assert cfg["user"] == "YOUR_USER"
assert cfg["backup_uuid"] == "CHANGE_ME"
assert cfg["restic_password_file"] == "/etc/restic/password"
assert cfg["restic_data_check_subsets"] >= 1
assert isinstance(cfg["restic_required_paths"], list)
assert isinstance(cfg["restic_required_tags"], list)
assert cfg["timeshift_overdue_hours"] > 0

rule = (ROOT / "polkit/49-backup-recovery.rules.in").read_text()
assert "__BRC_USER__" in rule
assert "org.freedesktop.systemd1.manage-units" in rule
assert 'verb === "start"' in rule
assert "run-command" not in rule.lower()

wrapper = (ROOT / "src/quickshell/modules/ii/backupRecovery/BackupRecovery.qml").read_text()
content = (ROOT / "src/quickshell/modules/ii/backupRecovery/BackupRecoveryContent.qml").read_text()
opbar = (ROOT / "src/quickshell/modules/ii/backupRecovery/GlobalOperationBar.qml").read_text()
helper = (ROOT / "src/quickshell/scripts/backup-recovery/control_center.py").read_text()
collector = (ROOT / "src/backend/state_collector.py").read_text()
actions = (ROOT / "src/backend/root_actions.py").read_text()

assert 'target: "backupRecoveryV160"' in wrapper
assert 'return "1.6.0"' in wrapper
assert "UI_CONTRACT = '1.6.0'" in helper
assert "UI_CONTRACT = '1.6.0'" in collector
assert "BACKEND_REVISION = '1.6.0-phase4-final'" in collector
assert 'expectedUiContract: "1.6.0"' in content
assert "GlobalOperationBar {" in content
assert "Accessible.role: Accessible.StatusBar" in opbar

assert "mounted_identity_verified" in collector
assert "timeshift_overdue_hours" in collector
assert "credential-recovery" in collector
assert "backup_proof" in collector

assert "ACTION_LOCK_FILE" in actions
assert "flock" in actions
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
    assert (ROOT / "src/tools" / name).is_file(), name

# The old alpha installer intentionally remains unchanged on this sync branch.
# Promotion of installer/release packaging is a separate reviewed step.
install = (ROOT / "installer/install.sh").read_text()
assert "Transactional rollback snapshot created" in install
assert "wrong filesystem mounted" in install

uninstall = (ROOT / "installer/uninstall.sh").read_text()
assert "--purge" in uninstall
assert "Restic/Timeshift repositories and recovery data were not touched" in uninstall

print("✓ runtime 1.6.0 source contract tests passed")
