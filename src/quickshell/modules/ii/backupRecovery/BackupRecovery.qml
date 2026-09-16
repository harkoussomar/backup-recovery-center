import qs
import qs.modules.common
import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Hyprland

Scope {
    id: root

    function openCenter() { GlobalStates.backupRecoveryOpen = true }
    function closeCenter() { GlobalStates.backupRecoveryOpen = false }
    function toggleCenter() { GlobalStates.backupRecoveryOpen = !GlobalStates.backupRecoveryOpen }

    IpcHandler {
        target: "backupRecovery"
        function toggle() { root.toggleCenter() }
        function open() { root.openCenter() }
        function close() { root.closeCenter() }
        function ping() { return "ready" }
        function uiVersion() { return "2" }
    }

    // Runtime-proof endpoint. qs ipc call does not print QML return values, so
    // a protocol-specific target proves a compatible wrapper is live.
    IpcHandler {
        target: "backupRecoveryProtocol2"
        function ping() {}
    }

    GlobalShortcut {
        name: "backupRecoveryToggle"
        description: "Toggle Backup & Recovery Center"
        onPressed: root.toggleCenter()
    }

    LazyLoader {
        active: GlobalStates.backupRecoveryOpen

        component: PanelWindow {
            anchors { left: true; right: true; top: true; bottom: true }
            exclusionMode: ExclusionMode.Ignore
            color: "transparent"
            WlrLayershell.namespace: "quickshell:backupRecovery"
            WlrLayershell.layer: WlrLayer.Overlay
            WlrLayershell.keyboardFocus: WlrKeyboardFocus.Exclusive

            BackupRecoveryContent {
                anchors.fill: parent
                onCloseRequested: root.closeCenter()
            }
        }
    }
}
