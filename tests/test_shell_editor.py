#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "installer/shell_edit.py"
spec = importlib.util.spec_from_file_location("shell_edit", path)
m = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(m)

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    global_qml = td / "GlobalStates.qml"
    family_qml = td / "IllogicalImpulseFamily.qml"

    # Deliberately contains NO Arch Remote integration.
    global_qml.write_text("""import QtQuick
import Quickshell
pragma Singleton

Singleton {
    property bool sidebarOpen: false
}
""")
    family_qml.write_text("""import qs
import qs.modules.common
import QtQuick

Scope {
    PanelLoader { component: ExistingPanel {} }
}
""")

    m.add_global(global_qml)
    m.add_family(family_qml)

    g = global_qml.read_text()
    f = family_qml.read_text()
    assert g.count("property bool backupRecoveryOpen:") == 1
    assert f.count("import qs.modules.ii.backupRecovery") == 1
    assert f.count("PanelLoader { component: BackupRecovery {} }") == 1
    assert "archRemote" not in g
    assert "archRemote" not in f

    # Repeat install is idempotent.
    m.add_global(global_qml)
    m.add_family(family_qml)
    assert global_qml.read_text().count("property bool backupRecoveryOpen:") == 1
    assert family_qml.read_text().count("import qs.modules.ii.backupRecovery") == 1
    assert family_qml.read_text().count("PanelLoader { component: BackupRecovery {} }") == 1

    # Uninstall removes only our integration, preserving unrelated shell content.
    m.remove_global(global_qml)
    m.remove_family(family_qml)
    g = global_qml.read_text()
    f = family_qml.read_text()
    assert "backupRecoveryOpen" not in g
    assert "backupRecovery" not in f
    assert "sidebarOpen" in g
    assert "ExistingPanel" in f

print("✓ shell editor tests passed (no Arch Remote dependency)")
