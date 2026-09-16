#!/usr/bin/env python3
from pathlib import Path
import json
import re

ROOT = Path(__file__).resolve().parents[1]

assert (ROOT / "VERSION").read_text().strip() == "0.1.0-alpha.6"

cfg = json.loads((ROOT / "config/config.example.json").read_text())
assert cfg["user"] == "YOUR_USER"
assert cfg["backup_uuid"] == "CHANGE_ME"
assert cfg["restore_test_path"].startswith("/")
assert cfg["restic_password_file"] == "/etc/restic/password"

rule = (ROOT / "polkit/49-backup-recovery.rules.in").read_text()
assert "__BRC_USER__" in rule
assert 'org.freedesktop.systemd1.manage-units' in rule
assert 'verb === "start"' in rule
assert "run-command" not in rule.lower()

wrapper = (ROOT / "src/quickshell/modules/ii/backupRecovery/BackupRecovery.qml").read_text()
helper = (ROOT / "src/quickshell/scripts/backup-recovery/control_center.py").read_text()
collector = (ROOT / "src/backend/state_collector.py").read_text()
content = (ROOT / "src/quickshell/modules/ii/backupRecovery/BackupRecoveryContent.qml").read_text()

# Public package version and runtime protocol are deliberately separate.
assert 'backupRecoveryProtocol2' in wrapper
assert 'return "2"' in wrapper
assert "UI_CONTRACT = '2'" in helper
assert "UI_CONTRACT = '2'" in collector
assert 'ui_contract: "2"' in content
assert "Appearance.radius." not in content
assert "Appearance.rounding." in content
assert "0.1.0-alpha.6" not in wrapper

# Unknown is not zero.
assert "count': None" in collector or '"count": None' in collector
assert "mounted_identity_verified" in collector
assert "enforce_backup_mount_identity" in collector

install = (ROOT / "installer/install.sh").read_text()
uninstall = (ROOT / "installer/uninstall.sh").read_text()

# Resolver wiring regression: alpha.2 referenced undefined DEFAULT_* names.
assert "$DEFAULT_" not in install
assert '${MOUNTPOINT//\\//\\\\/}' not in install
assert "PY_FSTAB" in install
for token in [
    '"$ENV_MOUNTPOINT"',
    '"$ENV_BACKUP_SERVICE"',
    '"$ENV_BACKUP_TIMER"',
    '"$ENV_MAINT_SERVICE"',
    '"$ENV_MAINT_TIMER"',
    '"$ENV_RESTORE_TEST_PATH"',
]:
    assert token in install, token

# Installer safety/transaction contracts.
for token in [
    "run the installer as your desktop user, not with sudo",
    "Transactional rollback snapshot created",
    "post-publish verification failed",
    "backupRecoveryProtocol2",
    "namespace: quickshell:backupRecovery",
    "runtime diagnostics",
    "Timeshift target identity matches",
    "wrong filesystem mounted",
]:
    assert token in install, token


# Rollback must become active before the first live user-file mutation.
publish_idx = install.index("PUBLISHED=1")
first_live_idx = install.index('rm -rf "$MODULE_DST" "$HELPER_DST"', publish_idx)
assert publish_idx < first_live_idx
assert 'root-snapshot.tar' in install
assert 'rollback 1 || true' in install
assert 'die()' in install

# No Arch Remote prerequisite.
assert "archRemoteOpen" not in install
assert "component: ArchRemote" not in install
assert "import qs.modules.ii.archRemote" not in install

# Uninstall preserves state/config unless explicit purge.
assert "--purge" in uninstall
assert "Preserving /etc/backup-recovery/config.json" in uninstall
assert "Restic/Timeshift repositories and recovery data were not touched" in uninstall

print("✓ public contract tests passed")
