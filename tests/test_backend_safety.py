#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


ra = load(ROOT / "src/backend/root_actions.py", "brc_root_actions_test")
sc = load(ROOT / "src/backend/state_collector.py", "brc_state_collector_test")

cfg = {
    "mountpoint": "/mnt/backup",
    "backup_uuid": "EXPECTED",
    "restore_test_path": "/etc/hostname",
}

# Root action: exact UUID is accepted.
ra.require_mount = lambda _m: None
ra.mounted_uuid = lambda _m: "EXPECTED"
ra.verify_expected_mount(cfg)

# Empty UUID is unverified and MUST fail closed.
ra.mounted_uuid = lambda _m: ""
try:
    ra.verify_expected_mount(cfg)
except RuntimeError:
    pass
else:
    raise AssertionError("empty mounted UUID was accepted")

# Wrong UUID MUST fail closed.
ra.mounted_uuid = lambda _m: "WRONG"
try:
    ra.verify_expected_mount(cfg)
except RuntimeError:
    pass
else:
    raise AssertionError("wrong mounted UUID was accepted")

# Collector: correct mounted filesystem is trusted.
base = {
    "connected": True,
    "mounted": True,
    "mounted_uuid": "EXPECTED",
    "filesystem_accessible": True,
    "status": "mounted",
}
d = sc.enforce_backup_mount_identity(dict(base), "EXPECTED")
assert d["identity_verified"] is True
assert d["mounted_identity_verified"] is True
assert d["filesystem_accessible"] is True

# Mounted + empty UUID is quarantined, even if the directory is readable.
d = sc.enforce_backup_mount_identity({**base, "mounted_uuid": ""}, "EXPECTED")
assert d["identity_verified"] is False
assert d["mounted_identity_verified"] is False
assert d["raw_filesystem_accessible"] is True
assert d["filesystem_accessible"] is False
assert d["status"] == "wrong-filesystem-mounted"

# Mounted + wrong UUID is quarantined.
d = sc.enforce_backup_mount_identity({**base, "mounted_uuid": "WRONG"}, "EXPECTED")
assert d["identity_verified"] is False
assert d["filesystem_accessible"] is False
assert d["status"] == "wrong-filesystem-mounted"

# Correct disk connected but unmounted is valid identity, but not filesystem access.
d = sc.enforce_backup_mount_identity({
    "connected": True,
    "mounted": False,
    "mounted_uuid": "",
    "filesystem_accessible": False,
    "status": "connected-unmounted",
}, "EXPECTED")
assert d["identity_verified"] is True
assert d["mounted_identity_verified"] is False
assert d["filesystem_accessible"] is False

# Disconnected is neutral/unverified.
d = sc.enforce_backup_mount_identity({
    "connected": False,
    "mounted": False,
    "mounted_uuid": "",
    "filesystem_accessible": False,
    "status": "offline",
}, "EXPECTED")
assert d["identity_verified"] is False
assert d["filesystem_accessible"] is False

# SMART self-test detection must survive starter-unit exit.
ra.resolve_disk = lambda _uuid: "/dev/fake"
ra.run = lambda *a, **k: subprocess.CompletedProcess(
    a[0], 0,
    "Self-test execution status: ( 249) Self-test routine in progress... 90% of test remaining.",
    "",
)
running, _ = ra.smart_self_test_in_progress(cfg)
assert running is True

ra.run = lambda *a, **k: subprocess.CompletedProcess(
    a[0], 0, "SMART overall-health self-assessment test result: PASSED", ""
)
running, _ = ra.smart_self_test_in_progress(cfg)
assert running is False

# Configurable restore-test path is part of the implementation contract.
root_text = (ROOT / "src/backend/root_actions.py").read_text()
assert "restore_test_path" in root_text
assert "'--include', test_path" in root_text
assert "test_path.lstrip('/')" in root_text

print("✓ backend safety tests passed")
