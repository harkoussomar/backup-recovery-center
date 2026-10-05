import qs
import qs.modules.common
import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Hyprland

Scope {
    id: root

    property bool presentationClosing: false

    function openCenter() {
        presentationCloseTimer.stop()
        root.presentationClosing = false
        GlobalStates.backupRecoveryOpen = true
    }

    function closeCenter() {
        if (!GlobalStates.backupRecoveryOpen)
            return
        root.presentationClosing = true
        GlobalStates.backupRecoveryOpen = false
        presentationCloseTimer.restart()
    }

    function toggleCenter() {
        if (GlobalStates.backupRecoveryOpen)
            root.closeCenter()
        else
            root.openCenter()
    }

    Timer {
        id: presentationCloseTimer
        interval: 260 // motion-calibrated-v1
        repeat: false
        onTriggered: root.presentationClosing = false
    }

    IpcHandler {
        target: "backupRecovery"
        function toggle() { root.toggleCenter() }
        function open() { root.openCenter() }
        function close() { root.closeCenter() }
        function ping() { return "ready" }
        function uiVersion() { return "1.6.0" }
    }

    // Runtime-proof endpoint. qs ipc call does not print QML return values, so
    // a version-specific target proves this exact wrapper generation is live.
    IpcHandler {
        target: "backupRecoveryV160"
        function ping() {}
    }

    GlobalShortcut {
        name: "backupRecoveryToggle"
        description: "Toggle Backup & Recovery Center"
        onPressed: root.toggleCenter()
    }

    LazyLoader {
        // backup-recovery-persistent-host-v1
        // Keep the native layer-shell host mapped like Project Launcher.
        // Only the visual content is created/destroyed on open/close.
        active: true

        component: PanelWindow {
            visible: true
            id: backupRecoveryWindow
            anchors { left: true; right: true; top: true; bottom: true }
            exclusionMode: ExclusionMode.Ignore
            color: "transparent"
            WlrLayershell.namespace: "quickshell:backupRecovery"
            // backup-recovery-persistent-host-v1 input
            mask: Region {
                item:
                    GlobalStates.backupRecoveryOpen
                        ? backupRecoveryContentLoader.item
                        : null
            }

            WlrLayershell.layer: WlrLayer.Overlay
            // backup-recovery-snip-focus-v1
            //
            // Backup Recovery normally owns keyboard focus, but Region Selector
            // must temporarily receive the seat while the modal remains visible
            // underneath for capture.
            // backup-recovery-persistent-host-v1 focus
            WlrLayershell.keyboardFocus:
                GlobalStates.regionSelectorOpen
                    ? WlrKeyboardFocus.None
                    : (
                        GlobalStates.backupRecoveryOpen
                            ? WlrKeyboardFocus.OnDemand
                            : WlrKeyboardFocus.None
                    )

            // backup-recovery-persistent-host-loader-fix-v1
            // BackupRecoveryContent is visual, so use QtQuick Loader.
            Loader {
                id: backupRecoveryContentLoader
                anchors.fill: parent
                active:
                    GlobalStates.backupRecoveryOpen
                    || root.presentationClosing

                sourceComponent: Component {
                    BackupRecoveryContent {
                    anchors.fill: parent
                    captureScreenName:
                        backupRecoveryWindow.screen
                            ? backupRecoveryWindow.screen.name
                            : ""
                    onCloseRequested: root.closeCenter()
                    }
                }
            }
        }
    }
}
