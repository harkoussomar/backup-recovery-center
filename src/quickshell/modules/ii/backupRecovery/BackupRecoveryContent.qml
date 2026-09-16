import qs
import qs.modules.common
import qs.modules.common.widgets
import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import Quickshell
import Quickshell.Io

Item {
    id: root
    signal closeRequested()

    property string helperPath: Quickshell.shellPath("scripts/backup-recovery/control_center.py")
    property int selectedPage: 0
    property int activePage: 0
    property int previousPage: 0
    property bool pageShown: true
    property real pageShift: 0
    property bool loading: false
    property bool actionPending: false
    property string lastRequestedAction: ""
    property int actionWaitTicks: 0
    property string toastMessage: ""
    property bool toastError: false
    property bool helpOpen: false
    property bool confirmOpen: false
    property string confirmAction: ""
    property string confirmTitle: ""
    property string confirmDetail: ""

    property var protectionState: ({
        schema_version: 2,
        ui_contract: "2",
        generated_at: 0,
        overall: {status:"unverified", label:"Not verified", summary:"Collecting backup state…"},
        disk: {connected:false, mounted:false, status:"offline", filesystem_accessible:false, free_bytes:0, total_bytes:0, model:"", transport:"", used_percent:0},
        restic: {configured:false, known:false, availability:"unavailable", count:null, snapshots:[], latest:null, age_hours:null, check:{known:false,ok:null,time:0}, restore_test:{known:false,ok:null,time:0}, timer:{}, maintenance_timer:{}},
        timeshift: {configured:false, known:false, availability:"unavailable", count:null, snapshots:[], latest:null, mode:"RSYNC", schedule:{daily:false,daily_keep:0,weekly:false,weekly_keep:0}},
        smart: {known:false, available:false, availability:"unavailable", health:"unknown", condition:"unknown", temperature_c:null, attributes:{}, historical_error_count:null, last_test:null, test_durations:{}},
        recovery: {core_ready:0,core_total:6,core_checks:[],ready:0,total:6,checks:[],resilience_checks:[],restore_doc:"",manifests_updated_at:0},
        history: {dotfiles:{available:false,clean:false,changed_count:null,latest:null},etc:{available:false,clean:false,changed_count:null,latest:null}},
        attention: [],
        activity: [],
        actions: ({}),
        action_running: false,
        current_action: ""
    })

    readonly property var pages: [
        {name:"Overview", icon:"space_dashboard"},
        {name:"Backups", icon:"backup"},
        {name:"Restore", icon:"restore"},
        {name:"History", icon:"history"},
        {name:"Disk Health", icon:"hard_drive"},
        {name:"Recovery", icon:"health_and_safety"}
    ]

    focus: true

    Component.onCompleted: {
        Qt.callLater(() => root.forceActiveFocus())
        root.refresh(true)
    }

    Keys.priority: Keys.AfterItem
    Keys.onPressed: event => {
        if (event.key === Qt.Key_Escape) {
            if (root.confirmOpen) root.confirmOpen = false
            else if (root.helpOpen) root.helpOpen = false
            else root.closeRequested()
            event.accepted = true
        } else if (event.modifiers === Qt.ControlModifier && event.key === Qt.Key_R) {
            root.refresh(true)
            event.accepted = true
        } else if (event.modifiers === Qt.ControlModifier
                   && event.key >= Qt.Key_1 && event.key <= Qt.Key_6) {
            root.switchPage(event.key - Qt.Key_1)
            event.accepted = true
        }
    }

    function switchPage(index) {
        const next = Math.max(0, Math.min(root.pages.length - 1, index))
        if (next === root.selectedPage) return
        root.previousPage = root.selectedPage
        root.selectedPage = next
        root.pageShift = next > root.previousPage ? 8 : -8
        root.pageShown = false
        pageTimer.restart()
    }

    function refresh(full) {
        if (snapshotProc.running) return
        root.loading = true
        snapshotProc.command = full
            ? ["python3", root.helperPath, "snapshot", "--refresh"]
            : ["python3", root.helperPath, "snapshot"]
        snapshotProc.running = true
    }

    function requestAction(name) {
        if (actionProc.running || root.actionPending) return
        root.lastRequestedAction = name
        root.actionPending = true
        root.actionWaitTicks = 0
        actionProc.command = ["python3", root.helperPath, "action", name]
        actionProc.running = true
        actionPollTimer.start()
    }

    function confirm(name, title, detail) {
        root.confirmAction = name
        root.confirmTitle = title
        root.confirmDetail = detail
        root.confirmOpen = true
    }

    function actionLabel(name) {
        if (name === "backup") return "Backup"
        if (name === "timeshift") return "Restore point"
        if (name === "restic-check") return "Repository check"
        if (name === "restore-test") return "Restore verification"
        if (name === "smart-short") return "SMART short test"
        if (name === "smart-long") return "SMART extended test"
        if (name === "mount") return "Mount"
        if (name === "eject") return "Safe unmount"
        if (name === "refresh-manifests") return "Recovery refresh"
        return name
    }

    function actionState(name) {
        const actions = root.protectionState.actions || ({})
        return actions[name] || ({})
    }

    function finishPendingActionIfReady() {
        if (!root.actionPending) return
        root.actionWaitTicks += 1
        if (root.protectionState.action_running) return
        if (root.actionWaitTicks < 2) return

        const item = root.actionState(root.lastRequestedAction)
        const failed = item.result && item.result !== "success"
        root.toastError = failed
        root.toastMessage = failed
            ? `${root.actionLabel(root.lastRequestedAction)} failed · open the relevant page for status`
            : `${root.actionLabel(root.lastRequestedAction)} completed`
        toastTimer.restart()
        root.actionPending = false
        actionPollTimer.stop()
        root.refresh(true)
    }

    function ageText(value) {
        let timestamp = 0
        if (typeof value === "number") timestamp = value
        else if (value) {
            const parsed = Date.parse(String(value))
            if (!isNaN(parsed)) timestamp = parsed / 1000
        }
        if (!timestamp || timestamp <= 0) return "Never"
        const seconds = Math.max(0, Date.now() / 1000 - timestamp)
        if (seconds < 60) return "now"
        if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
        if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
        return `${Math.floor(seconds / 86400)}d ago`
    }

    function updatedText(value) {
        const age = root.ageText(value)
        return age === "now" ? "Updated now" : age === "Never" ? "Not updated yet" : `Updated ${age}`
    }

    function bytesText(value) {
        const n = Number(value || 0)
        if (n <= 0) return "—"
        const gib = n / 1073741824
        if (gib >= 10) return `${gib.toFixed(0)} GiB`
        return `${gib.toFixed(1)} GiB`
    }

    function knownCount(domain) {
        return domain && domain.known === true && domain.count !== null && domain.count !== undefined
    }

    function countText(domain) {
        return root.knownCount(domain) ? String(domain.count) : "Unknown"
    }

    function inventoryText(domain, noun) {
        if (!root.knownCount(domain)) return "Inventory unavailable"
        const n = Number(domain.count || 0)
        return `${n} ${noun}${n === 1 ? "" : "s"}`
    }

    function domainFreshness(domain) {
        if (!domain) return "Not verified"
        if (domain.availability === "live") return "Live verification"
        if (domain.availability === "cached" && domain.verified_at)
            return `Last verified ${root.ageText(domain.verified_at)}`
        return "Not verified"
    }

    function latestBackupText() {
        const r = root.protectionState.restic
        if (r && r.latest) return root.ageText(r.latest.time)
        return root.knownCount(r) && Number(r.count) === 0 ? "No snapshots" : "Unknown"
    }

    function latestRestoreText() {
        const ts = root.protectionState.timeshift
        if (ts && ts.latest) return root.ageText(ts.latest.created_at)
        return root.knownCount(ts) && Number(ts.count) === 0 ? "No restore points" : "Unknown"
    }

    function overallPillState() {
        const status = root.protectionState.overall.status
        return status === "critical" ? "danger"
             : status === "protected" || status === "protected-unmounted" || status === "protected-offline" ? "active"
             : "neutral"
    }

    function diskSubtitle() {
        const d = root.protectionState.disk
        if (d.mounted && d.filesystem_accessible) return `${root.bytesText(d.free_bytes)} free`
        if (d.mounted) return "Mounted · filesystem unavailable"
        if (d.connected) return "Connected · not mounted"
        const latest = root.protectionState.restic.latest
        return latest ? `Offline · last backup ${root.ageText(latest.time)}` : "Offline by design"
    }

    function changedFilesText(item) {
        if (!item || !item.available) return "Unavailable"
        if (item.clean) return "Clean"
        const n = Number(item.changed_count || 0)
        return `${n} changed file${n === 1 ? "" : "s"}`
    }

    function markerText(marker) {
        if (!marker || marker.known !== true) return "Not verified"
        return marker.ok === true ? "Verified" : "Failed"
    }

    function markerState(marker) {
        if (!marker || marker.known !== true) return "neutral"
        return marker.ok === true ? "active" : "danger"
    }

    function latestSmartTestText() {
        const test = root.protectionState.smart.last_test
        if (!test) return "No result detected"
        return `${test.type || "SMART test"} · ${test.status_label || test.status || "Unknown"}`
    }

    function latestSmartTestDetail() {
        const test = root.protectionState.smart.last_test
        if (!test) return root.domainFreshness(root.protectionState.smart)
        const progress = test.status_label === "Passed" ? "Completed"
            : test.status_label === "Running" ? `${test.completed_percent || 0}% complete`
            : test.status_label === "Aborted by user" ? `${test.completed_percent || 0}% completed before abort`
            : `${test.completed_percent || 0}% complete`
        return `${progress} · at ${test.lifetime_hours || "?"} power-on hours`
    }

    function smartDuration(kind) {
        const durations = root.protectionState.smart.test_durations || ({})
        const minutes = kind === "short" ? durations.short_minutes : durations.extended_minutes
        if (!minutes) return kind === "short" ? "Short test" : "Extended test"
        if (minutes < 60) return `${kind === "short" ? "Short" : "Extended"} test · ~${minutes} min`
        const hours = Math.floor(minutes / 60)
        const mins = minutes % 60
        return `${kind === "short" ? "Short" : "Extended"} test · ~${hours}h${mins ? ` ${mins}m` : ""}`
    }

    function recoveryReady() {
        const r = root.protectionState.recovery || ({})
        return r.core_ready !== undefined ? r.core_ready : (r.ready || 0)
    }

    function recoveryTotal() {
        const r = root.protectionState.recovery || ({})
        return r.core_total !== undefined ? r.core_total : (r.total || 0)
    }

    function recoveryChecks() {
        const r = root.protectionState.recovery || ({})
        return r.core_checks || r.checks || []
    }

    function recoveryStatusText(check) {
        if (!check) return "Unknown"
        if (check.ok) return check.cached ? "Ready · cached" : "Ready"
        if (check.state === "recommended") return "Recommended"
        if (check.state === "untested") return "Untested"
        if (check.state === "failed") return "Failed"
        if (check.state === "missing") return "Missing"
        if (check.state === "pending") return "Pending"
        return "Unknown"
    }

    function attentionTag(item) {
        if (item && item.tag) return item.tag
        if (!item) return "INFO"
        if (item.severity === "critical") return "ERROR"
        if (item.severity === "warning") return "DUE"
        if (item.severity === "history") return "HISTORY"
        if (item.severity === "unverified") return "UNVERIFIED"
        return "INFO"
    }

    function snapshotTagText(item) {
        if (!item || !item.tags) return "Snapshot"
        if (item.tags.length === 0) return "Snapshot"
        return item.tags.join(" · ")
    }

    function openRestoreGuide() {
        if (!root.protectionState.disk.mounted) {
            root.toastError = true
            root.toastMessage = "Connect and mount the backup HDD first"
            toastTimer.restart()
            return
        }
        Quickshell.execDetached(["xdg-open", root.protectionState.recovery.restore_doc])
    }

    Process {
        id: snapshotProc
        stdout: StdioCollector {
            onStreamFinished: {
                root.loading = false
                try {
                    root.protectionState = JSON.parse(text)
                    root.finishPendingActionIfReady()
                } catch (e) {
                    root.toastError = true
                    root.toastMessage = "Backup state could not be parsed"
                    toastTimer.restart()
                }
            }
        }
        stderr: StdioCollector {
            onStreamFinished: {
                if (text.trim().length > 0 && !root.toastMessage.length) {
                    root.toastError = true
                    root.toastMessage = text.trim().split("\n").slice(-1)[0]
                    toastTimer.restart()
                }
            }
        }
    }

    Process {
        id: actionProc
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    const data = JSON.parse(text)
                    if (!data.ok) {
                        root.actionPending = false
                        actionPollTimer.stop()
                        root.toastError = true
                        root.toastMessage = data.message || "Action rejected"
                        toastTimer.restart()
                    }
                } catch (e) {
                    root.actionPending = false
                    actionPollTimer.stop()
                    root.toastError = true
                    root.toastMessage = "Action response could not be parsed"
                    toastTimer.restart()
                }
            }
        }
    }

    Timer {
        id: pageTimer
        interval: 70
        repeat: false
        onTriggered: {
            root.activePage = root.selectedPage
            root.pageShift = 0
            root.pageShown = true
        }
    }
    Timer {
        interval: 60000
        repeat: true
        running: true
        onTriggered: root.refresh(true)
    }
    Timer {
        id: actionPollTimer
        interval: 2500
        repeat: true
        onTriggered: root.refresh(false)
    }
    Timer {
        id: toastTimer
        interval: 3600
        repeat: false
        onTriggered: root.toastMessage = ""
    }

    component HeaderButton: RippleButton {
        id: button
        property string iconName: ""
        property string tooltipText: ""
        property bool active: false
        implicitWidth: 40
        implicitHeight: 40
        activeFocusOnTab: true
        Accessible.role: Accessible.Button
        Accessible.name: tooltipText
        buttonRadius: Appearance.rounding.normal
        buttonRadiusPressed: Appearance.rounding.normal
        colBackground: active ? Appearance.colors.colLayer2Base : "transparent"
        colBackgroundHover: Appearance.colors.colLayer2Hover
        colRipple: Appearance.colors.colLayer2Active
        contentItem: MaterialSymbol {
            anchors.centerIn: parent
            text: button.iconName
            iconSize: 19
            fill: button.active ? 1 : 0
            color: button.active || button.hovered ? Appearance.colors.colPrimary : Appearance.colors.colSubtext
        }
        StyledToolTip {
            extraVisibleCondition: parent !== null && parent.hovered === true
            delay: 450
            text: button.tooltipText
        }
    }

    component Pill: Rectangle {
        id: pill
        property string label: ""
        property string state: "neutral"
        property bool compact: false
        implicitWidth: pillText.implicitWidth + (compact ? 14 : 20)
        implicitHeight: compact ? 24 : 28
        radius: Appearance.rounding.full
        color: state === "danger" ? Appearance.colors.colErrorContainer
             : state === "active" ? Appearance.colors.colSecondaryContainer
             : Appearance.colors.colLayer2Base
        StyledText {
            id: pillText
            anchors.centerIn: parent
            text: pill.label
            color: pill.state === "danger" ? Appearance.colors.colOnErrorContainer
                 : pill.state === "active" ? Appearance.colors.colOnSecondaryContainer
                 : Appearance.colors.colSubtext
            font.pixelSize: Appearance.font.pixelSize.smallest
            font.weight: Font.DemiBold
        }
    }

    component SmallButton: RippleButton {
        id: button
        property string iconName: ""
        property string label: ""
        property bool danger: false
        property bool emphasized: false
        implicitHeight: 38
        implicitWidth: Math.max(82, labelText.implicitWidth + 42)
        activeFocusOnTab: true
        Accessible.role: Accessible.Button
        Accessible.name: label
        buttonRadius: Appearance.rounding.normal
        buttonRadiusPressed: Appearance.rounding.normal
        colBackground: danger ? Appearance.colors.colErrorContainer
                     : emphasized ? Appearance.colors.colSecondaryContainer
                     : Appearance.colors.colLayer2Base
        colBackgroundHover: danger ? Appearance.colors.colErrorContainer
                          : emphasized ? Appearance.colors.colSecondaryContainerHover
                          : Appearance.colors.colLayer2Hover
        colRipple: danger ? Appearance.colors.colErrorContainer
                 : emphasized ? Appearance.colors.colSecondaryContainerActive
                 : Appearance.colors.colLayer2Active
        contentItem: Row {
            anchors.centerIn: parent
            spacing: 7
            MaterialSymbol {
                anchors.verticalCenter: parent.verticalCenter
                text: button.iconName
                iconSize: 16
                color: button.danger ? Appearance.colors.colOnErrorContainer
                     : button.emphasized ? Appearance.colors.colOnSecondaryContainer
                     : Appearance.colors.colOnLayer1
            }
            StyledText {
                id: labelText
                anchors.verticalCenter: parent.verticalCenter
                text: button.label
                color: button.danger ? Appearance.colors.colOnErrorContainer
                     : button.emphasized ? Appearance.colors.colOnSecondaryContainer
                     : Appearance.colors.colOnLayer1
                font.pixelSize: Appearance.font.pixelSize.small
                font.weight: Font.DemiBold
            }
        }
    }

    component NavButton: RippleButton {
        id: nav
        required property int pageIndex
        required property var pageData
        Layout.fillWidth: true
        implicitHeight: 44
        readonly property bool selected: root.selectedPage === pageIndex
        activeFocusOnTab: true
        Accessible.role: Accessible.Button
        Accessible.name: pageData.name
        buttonRadius: Appearance.rounding.normal
        buttonRadiusPressed: Appearance.rounding.normal
        colBackground: selected ? Appearance.colors.colLayer2Base : "transparent"
        colBackgroundHover: Appearance.colors.colLayer2Hover
        colRipple: Appearance.colors.colLayer2Active
        onClicked: root.switchPage(pageIndex)
        contentItem: RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 11
            anchors.rightMargin: 11
            spacing: 9
            Rectangle {
                Layout.preferredWidth: 3
                Layout.preferredHeight: 22
                radius: Appearance.rounding.full
                color: nav.selected ? Appearance.colors.colPrimary : "transparent"
            }
            MaterialSymbol {
                text: nav.pageData.icon
                iconSize: 19
                fill: nav.selected ? 1 : 0
                color: nav.selected || nav.hovered ? Appearance.colors.colPrimary : Appearance.colors.colSubtext
            }
            StyledText {
                Layout.fillWidth: true
                text: nav.pageData.name
                color: Appearance.colors.colOnLayer1
                font.pixelSize: Appearance.font.pixelSize.small
                font.weight: nav.selected ? Font.DemiBold : Font.Normal
                elide: Text.ElideRight
            }
        }
    }

    component Heading: RowLayout {
        id: heading
        property string title: ""
        property string subtitle: ""
        property string meta: ""
        Layout.fillWidth: true
        spacing: 12
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 2
            StyledText {
                Layout.fillWidth: true
                text: heading.title
                color: Appearance.colors.colOnLayer0
                font.family: Appearance.font.family.title
                font.pixelSize: Appearance.font.pixelSize.larger
                font.weight: Font.DemiBold
                elide: Text.ElideRight
            }
            StyledText {
                Layout.fillWidth: true
                text: heading.subtitle
                color: Appearance.colors.colSubtext
                font.pixelSize: Appearance.font.pixelSize.small
                wrapMode: Text.WordWrap
            }
        }
        StyledText {
            visible: heading.meta.length > 0
            text: heading.meta
            color: Appearance.colors.colSubtext
            font.pixelSize: Appearance.font.pixelSize.smallest
            horizontalAlignment: Text.AlignRight
        }
    }

    component Section: RowLayout {
        id: section
        property string title: ""
        property string detail: ""
        property string iconName: ""
        Layout.fillWidth: true
        spacing: 7
        MaterialSymbol {
            visible: section.iconName.length > 0
            text: section.iconName
            iconSize: 15
            color: Appearance.colors.colSubtext
        }
        StyledText {
            Layout.fillWidth: true
            text: section.title
            color: Appearance.colors.colSubtext
            font.pixelSize: Appearance.font.pixelSize.small
            font.weight: Font.DemiBold
            font.capitalization: Font.AllUppercase
        }
        StyledText {
            visible: section.detail.length > 0
            text: section.detail
            color: Appearance.colors.colSubtext
            font.pixelSize: Appearance.font.pixelSize.smallest
        }
    }

    component Metric: Rectangle {
        id: metric
        property string iconName: ""
        property string value: "—"
        property string label: ""
        property string subtitle: ""
        property string state: "neutral"
        Layout.fillWidth: true
        implicitHeight: 82
        radius: Appearance.rounding.normal
        color: Appearance.colors.colLayer1Base
        border.width: 1
        border.color: Appearance.colors.colLayer0Border
        RowLayout {
            anchors.fill: parent
            anchors.margins: 11
            spacing: 10
            Rectangle {
                Layout.preferredWidth: 36
                Layout.preferredHeight: 36
                radius: Appearance.rounding.normal
                color: metric.state === "danger" ? Appearance.colors.colErrorContainer
                     : metric.state === "active" ? Appearance.colors.colSecondaryContainer
                     : Appearance.colors.colLayer2Base
                MaterialSymbol {
                    anchors.centerIn: parent
                    text: metric.iconName
                    iconSize: 19
                    fill: metric.state === "active" ? 1 : 0
                    color: metric.state === "danger" ? Appearance.colors.colOnErrorContainer
                         : metric.state === "active" ? Appearance.colors.colOnSecondaryContainer
                         : Appearance.colors.colSubtext
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1
                StyledText {
                    Layout.fillWidth: true
                    text: metric.value
                    color: Appearance.colors.colOnLayer1
                    font.pixelSize: Appearance.font.pixelSize.normal
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                StyledText {
                    Layout.fillWidth: true
                    text: metric.label
                    color: Appearance.colors.colSubtext
                    font.pixelSize: Appearance.font.pixelSize.small
                    elide: Text.ElideRight
                }
                StyledText {
                    Layout.fillWidth: true
                    text: metric.subtitle
                    color: Appearance.colors.colSubtext
                    font.pixelSize: Appearance.font.pixelSize.smallest
                    elide: Text.ElideRight
                }
            }
        }
    }

    component InfoRow: RowLayout {
        id: row
        property string label: ""
        property string value: ""
        property string iconName: ""
        Layout.fillWidth: true
        spacing: 8
        MaterialSymbol {
            visible: row.iconName.length > 0
            text: row.iconName
            iconSize: 15
            color: Appearance.colors.colSubtext
        }
        StyledText {
            Layout.preferredWidth: 150
            text: row.label
            color: Appearance.colors.colSubtext
            font.pixelSize: Appearance.font.pixelSize.smallest
        }
        StyledText {
            Layout.fillWidth: true
            text: row.value
            color: Appearance.colors.colOnLayer1
            font.pixelSize: Appearance.font.pixelSize.small
            horizontalAlignment: Text.AlignRight
            elide: Text.ElideRight
        }
    }

    component SnapshotRow: Rectangle {
        id: row
        property var item: ({})
        property string kind: "restic"
        Layout.fillWidth: true
        implicitHeight: 58
        radius: Appearance.rounding.normal
        color: Appearance.colors.colLayer2Base
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 11
            anchors.rightMargin: 11
            spacing: 9
            MaterialSymbol {
                text: row.kind === "restic" ? "encrypted" : "restore"
                iconSize: 18
                color: Appearance.colors.colPrimary
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1
                StyledText {
                    Layout.fillWidth: true
                    text: row.kind === "restic"
                        ? ((row.item.short_id || "snapshot") + " · " + root.snapshotTagText(row.item))
                        : (row.item.comments || row.item.name || "Restore point")
                    color: Appearance.colors.colOnLayer1
                    font.pixelSize: Appearance.font.pixelSize.small
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                StyledText {
                    Layout.fillWidth: true
                    text: row.kind === "restic"
                        ? root.ageText(row.item.time)
                        : `${row.item.name || ""} · ${root.ageText(row.item.created_at || 0)}`
                    color: Appearance.colors.colSubtext
                    font.pixelSize: Appearance.font.pixelSize.smallest
                    elide: Text.ElideRight
                }
            }
            Pill {
                compact: true
                label: row.kind === "restic" ? root.snapshotTagText(row.item) : (row.item.tags || "On-demand")
                state: "neutral"
            }
        }
    }

    component RecoveryRow: Rectangle {
        id: row
        property var check: ({})
        Layout.fillWidth: true
        implicitHeight: 58
        radius: Appearance.rounding.normal
        color: Appearance.colors.colLayer2Base
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 11
            anchors.rightMargin: 11
            spacing: 9
            MaterialSymbol {
                text: row.check.ok ? "check_circle"
                    : row.check.state === "failed" || row.check.state === "missing" ? "error"
                    : row.check.state === "recommended" ? "recommend" : "radio_button_unchecked"
                iconSize: 18
                fill: row.check.ok ? 1 : 0
                color: row.check.state === "failed" || row.check.state === "missing"
                    ? Appearance.colors.colError : row.check.ok ? Appearance.colors.colPrimary : Appearance.colors.colSubtext
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 0
                StyledText {
                    Layout.fillWidth: true
                    text: row.check.label || "Recovery item"
                    color: Appearance.colors.colOnLayer1
                    font.pixelSize: Appearance.font.pixelSize.small
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                StyledText {
                    Layout.fillWidth: true
                    text: row.check.detail || ""
                    color: Appearance.colors.colSubtext
                    font.pixelSize: Appearance.font.pixelSize.smallest
                    elide: Text.ElideRight
                }
            }
            Pill {
                compact: true
                label: root.recoveryStatusText(row.check)
                state: row.check.state === "failed" || row.check.state === "missing" ? "danger" : row.check.ok ? "active" : "neutral"
            }
        }
    }

    component ActivityRow: RowLayout {
        id: row
        property var eventData: ({})
        Layout.fillWidth: true
        spacing: 8
        MaterialSymbol {
            text: row.eventData.severity === "error" ? "error" : row.eventData.severity === "success" ? "check_circle" : "history"
            iconSize: 16
            color: row.eventData.severity === "error" ? Appearance.colors.colError : Appearance.colors.colSubtext
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 0
            StyledText {
                Layout.fillWidth: true
                text: row.eventData.title || "Activity"
                color: Appearance.colors.colOnLayer1
                font.pixelSize: Appearance.font.pixelSize.small
                elide: Text.ElideRight
            }
            StyledText {
                Layout.fillWidth: true
                text: `${root.ageText(row.eventData.time || 0)} · ${row.eventData.detail || ""}`
                color: Appearance.colors.colSubtext
                font.pixelSize: Appearance.font.pixelSize.smallest
                elide: Text.ElideRight
            }
        }
    }

    component EmptyState: Rectangle {
        id: empty
        property string iconName: "info"
        property string title: "Nothing to show"
        property string detail: ""
        Layout.fillWidth: true
        implicitHeight: 118
        radius: Appearance.rounding.normal
        color: Appearance.colors.colLayer1Base
        border.width: 1
        border.color: Appearance.colors.colLayer0Border
        ColumnLayout {
            anchors.centerIn: parent
            width: Math.min(parent.width - 40, 520)
            spacing: 5
            MaterialSymbol {
                Layout.alignment: Qt.AlignHCenter
                text: empty.iconName
                iconSize: 24
                color: Appearance.colors.colSubtext
            }
            StyledText {
                Layout.alignment: Qt.AlignHCenter
                text: empty.title
                color: Appearance.colors.colOnLayer1
                font.pixelSize: Appearance.font.pixelSize.small
                font.weight: Font.DemiBold
            }
            StyledText {
                Layout.fillWidth: true
                text: empty.detail
                color: Appearance.colors.colSubtext
                font.pixelSize: Appearance.font.pixelSize.smallest
                horizontalAlignment: Text.AlignHCenter
                wrapMode: Text.WordWrap
            }
        }
    }

    MouseArea {
        anchors.fill: parent
        onClicked: mouse => {
            if (mouse.x < modal.x || mouse.x > modal.x + modal.width
                || mouse.y < modal.y || mouse.y > modal.y + modal.height)
                root.closeRequested()
        }
    }

    Rectangle {
        id: modal
        anchors.centerIn: parent
        width: Math.min(1160, parent.width - 48)
        height: Math.min(740, parent.height - 48)
        radius: Appearance.rounding.large
        color: Appearance.colors.colLayer0Base
        border.width: 1
        border.color: Appearance.colors.colLayer0Border
        clip: true
        opacity: 0
        transform: Translate { id: modalTranslate; y: 10 }
        Component.onCompleted: {
            modal.opacity = 1
            modalTranslate.y = 0
        }
        Behavior on opacity {
            animation: Appearance.animation.elementMoveFast.numberAnimation.createObject(this)
        }

        ColumnLayout {
            anchors.fill: parent
            spacing: 0

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 70
                Layout.leftMargin: 16
                Layout.rightMargin: 12
                spacing: 10

                Rectangle {
                    Layout.preferredWidth: 42
                    Layout.preferredHeight: 42
                    radius: Appearance.rounding.normal
                    color: Appearance.colors.colSecondaryContainer
                    MaterialSymbol {
                        anchors.centerIn: parent
                        text: "backup"
                        iconSize: 22
                        fill: 1
                        color: Appearance.colors.colOnSecondaryContainer
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    StyledText {
                        Layout.fillWidth: true
                        text: "Backup & Recovery"
                        color: Appearance.colors.colOnLayer0
                        font.family: Appearance.font.family.title
                        font.pixelSize: Appearance.font.pixelSize.large
                        font.weight: Font.DemiBold
                    }
                    StyledText {
                        Layout.fillWidth: true
                        text: `${root.protectionState.overall.summary || "System protection & disaster recovery"} · ${root.loading ? "Refreshing…" : root.updatedText(root.protectionState.generated_at)}`
                        color: Appearance.colors.colSubtext
                        font.pixelSize: Appearance.font.pixelSize.smallest
                        elide: Text.ElideRight
                    }
                }

                Pill {
                    label: root.protectionState.overall.label || "Checking"
                    state: root.overallPillState()
                }

                HeaderButton {
                    iconName: "help"
                    tooltipText: "Protection layers"
                    active: root.helpOpen
                    onClicked: root.helpOpen = !root.helpOpen
                }
                HeaderButton {
                    iconName: "refresh"
                    tooltipText: "Full refresh · Ctrl+R"
                    onClicked: root.refresh(true)
                }
                HeaderButton {
                    iconName: "close"
                    tooltipText: "Close · Esc"
                    onClicked: root.closeRequested()
                }
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1
                color: Appearance.colors.colLayer0Border
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.margins: 10
                spacing: 10

                Rectangle {
                    Layout.preferredWidth: 174
                    Layout.fillHeight: true
                    radius: Appearance.rounding.large
                    color: Appearance.colors.colLayer1Base
                    border.width: 1
                    border.color: Appearance.colors.colLayer0Border

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: 8
                        spacing: 4
                        Repeater {
                            model: root.pages
                            delegate: NavButton {
                                required property int index
                                required property var modelData
                                pageIndex: index
                                pageData: modelData
                            }
                        }
                        Item { Layout.fillHeight: true }
                        Rectangle {
                            Layout.fillWidth: true
                            Layout.preferredHeight: 1
                            color: Appearance.colors.colLayer0Border
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.margins: 5
                            spacing: 2
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                MaterialSymbol {
                                    text: root.protectionState.disk.mounted ? "hard_drive" : "hard_drive"
                                    iconSize: 15
                                    color: root.protectionState.disk.mounted ? Appearance.colors.colPrimary : Appearance.colors.colSubtext
                                }
                                StyledText {
                                    Layout.fillWidth: true
                                    text: "Backup HDD"
                                    color: Appearance.colors.colOnLayer1
                                    font.pixelSize: Appearance.font.pixelSize.small
                                    font.weight: Font.DemiBold
                                    elide: Text.ElideRight
                                }
                            }
                            StyledText {
                                Layout.fillWidth: true
                                text: root.diskSubtitle()
                                color: Appearance.colors.colSubtext
                                font.pixelSize: Appearance.font.pixelSize.smallest
                                elide: Text.ElideRight
                            }
                        }
                    }
                }

                Item {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    Item {
                        anchors.fill: parent
                        opacity: root.pageShown ? 1 : 0
                        transform: Translate {
                            x: root.pageShift
                            Behavior on x {
                                animation: Appearance.animation.elementMoveFast.numberAnimation.createObject(this)
                            }
                        }
                        Behavior on opacity {
                            animation: Appearance.animation.elementMoveFast.numberAnimation.createObject(this)
                        }
                        Loader {
                            anchors.fill: parent
                            sourceComponent: root.activePage === 0 ? overviewPage
                                : root.activePage === 1 ? backupsPage
                                : root.activePage === 2 ? restorePointsPage
                                : root.activePage === 3 ? historyPage
                                : root.activePage === 4 ? diskHealthPage : recoveryPage
                        }
                    }
                }
            }
        }
    }

    Rectangle {
        visible: root.helpOpen
        anchors.centerIn: modal
        width: Math.min(520, modal.width - 80)
        implicitHeight: helpColumn.implicitHeight + 36
        z: 80
        radius: Appearance.rounding.large
        color: Appearance.colors.colLayer1Base
        border.width: 1
        border.color: Appearance.colors.colLayer0Border
        ColumnLayout {
            id: helpColumn
            anchors.fill: parent
            anchors.margins: 18
            spacing: 10
            RowLayout {
                Layout.fillWidth: true
                StyledText {
                    Layout.fillWidth: true
                    text: "Protection layers"
                    color: Appearance.colors.colOnLayer1
                    font.family: Appearance.font.family.title
                    font.pixelSize: Appearance.font.pixelSize.normal
                    font.weight: Font.DemiBold
                }
                HeaderButton {
                    iconName: "close"
                    tooltipText: "Close"
                    onClicked: root.helpOpen = false
                }
            }
            StyledText {
                Layout.fillWidth: true
                text: "Timeshift rolls the Arch system back. Restic is the encrypted disaster backup. Git tracks personal configuration. etckeeper tracks /etc. Recovery manifests and RESTORE.md describe a full rebuild."
                color: Appearance.colors.colSubtext
                font.pixelSize: Appearance.font.pixelSize.small
                wrapMode: Text.WordWrap
            }
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: helpRows.implicitHeight + 20
                radius: Appearance.rounding.normal
                color: Appearance.colors.colLayer2Base
                ColumnLayout {
                    id: helpRows
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 7
                    InfoRow { label: "Timeshift"; value: "System rollback" }
                    InfoRow { label: "Restic"; value: "Encrypted data recovery" }
                    InfoRow { label: "Dotfiles Git"; value: "Personal config history" }
                    InfoRow { label: "etckeeper"; value: "/etc history" }
                    InfoRow { label: "Recovery guide"; value: "Full disaster rebuild" }
                }
            }
        }
    }

    Rectangle {
        visible: root.confirmOpen
        anchors.centerIn: modal
        width: Math.min(480, modal.width - 80)
        implicitHeight: confirmColumn.implicitHeight + 36
        z: 90
        radius: Appearance.rounding.large
        color: Appearance.colors.colLayer1Base
        border.width: 1
        border.color: Appearance.colors.colLayer0Border
        ColumnLayout {
            id: confirmColumn
            anchors.fill: parent
            anchors.margins: 18
            spacing: 10
            StyledText {
                Layout.fillWidth: true
                text: root.confirmTitle
                color: Appearance.colors.colOnLayer1
                font.family: Appearance.font.family.title
                font.pixelSize: Appearance.font.pixelSize.normal
                font.weight: Font.DemiBold
            }
            StyledText {
                Layout.fillWidth: true
                text: root.confirmDetail
                color: Appearance.colors.colSubtext
                font.pixelSize: Appearance.font.pixelSize.small
                wrapMode: Text.WordWrap
            }
            RowLayout {
                Layout.fillWidth: true
                Item { Layout.fillWidth: true }
                SmallButton {
                    iconName: "close"
                    label: "Cancel"
                    onClicked: root.confirmOpen = false
                }
                SmallButton {
                    iconName: root.confirmAction === "eject" ? "eject" : "science"
                    label: root.confirmAction === "eject" ? "Unmount safely" : "Start test"
                    emphasized: true
                    onClicked: {
                        const action = root.confirmAction
                        root.confirmOpen = false
                        root.requestAction(action)
                    }
                }
            }
        }
    }

    Rectangle {
        visible: root.toastMessage.length > 0
        anchors.horizontalCenter: modal.horizontalCenter
        anchors.bottom: modal.bottom
        anchors.bottomMargin: 16
        width: Math.min(520, modal.width - 60)
        implicitHeight: 44
        z: 100
        radius: Appearance.rounding.normal
        color: root.toastError ? Appearance.colors.colErrorContainer : Appearance.colors.colLayer1Base
        border.width: 1
        border.color: root.toastError ? Appearance.colors.colError : Appearance.colors.colLayer0Border
        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 11
            anchors.rightMargin: 11
            spacing: 7
            MaterialSymbol {
                text: root.toastError ? "error" : "check_circle"
                iconSize: 17
                color: root.toastError ? Appearance.colors.colOnErrorContainer : Appearance.colors.colPrimary
            }
            StyledText {
                Layout.fillWidth: true
                text: root.toastMessage
                color: root.toastError ? Appearance.colors.colOnErrorContainer : Appearance.colors.colOnLayer1
                font.pixelSize: Appearance.font.pixelSize.small
                elide: Text.ElideRight
            }
        }
    }

    Component {
        id: overviewPage
        ColumnLayout {
            spacing: 9
            Heading {
                title: "Protection"
                subtitle: "Live state, last-known evidence, and the next useful recovery action."
                meta: root.updatedText(root.protectionState.generated_at)
            }
            ScrollView {
                id: overviewScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ScrollBar.vertical.policy: ScrollBar.AsNeeded
                ColumnLayout {
                    width: overviewScroll.availableWidth
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Metric {
                            iconName: "encrypted"
                            value: root.latestBackupText()
                            label: "Restic backup"
                            subtitle: root.knownCount(root.protectionState.restic)
                                ? `${root.inventoryText(root.protectionState.restic, "snapshot")} · ${root.domainFreshness(root.protectionState.restic)}`
                                : root.domainFreshness(root.protectionState.restic)
                            state: root.knownCount(root.protectionState.restic) && Number(root.protectionState.restic.count) > 0 ? "active"
                                : root.protectionState.restic.availability === "live" && Number(root.protectionState.restic.count) === 0 ? "danger" : "neutral"
                        }
                        Metric {
                            iconName: "restore"
                            value: root.countText(root.protectionState.timeshift)
                            label: "Timeshift points"
                            subtitle: root.latestRestoreText() === "Unknown"
                                ? root.domainFreshness(root.protectionState.timeshift)
                                : `Latest ${root.latestRestoreText()} · ${root.domainFreshness(root.protectionState.timeshift)}`
                            state: root.knownCount(root.protectionState.timeshift) && Number(root.protectionState.timeshift.count) > 0 ? "active"
                                : root.protectionState.timeshift.availability === "live" && Number(root.protectionState.timeshift.count) === 0 ? "danger" : "neutral"
                        }
                        Metric {
                            iconName: "hard_drive"
                            value: root.protectionState.disk.mounted ? "Mounted" : root.protectionState.disk.connected ? "Connected" : "Offline"
                            label: "Backup HDD"
                            subtitle: root.diskSubtitle()
                            state: root.protectionState.disk.mounted ? "active" : "neutral"
                        }
                        Metric {
                            iconName: "verified_user"
                            value: `${root.recoveryReady()} / ${root.recoveryTotal()}`
                            label: "Core recovery"
                            subtitle: root.recoveryReady() === root.recoveryTotal() ? "Core recovery ready" : "Preparedness items remain"
                            state: root.recoveryReady() >= Math.max(1, root.recoveryTotal() - 1) ? "active" : "neutral"
                        }
                    }

                    Section { title: "Next action"; iconName: "bolt" }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        SmallButton {
                            visible: root.protectionState.disk.connected && !root.protectionState.disk.mounted
                            iconName: "drive_file_move"
                            label: "Mount HDD"
                            emphasized: true
                            enabled: !root.actionPending
                            onClicked: root.requestAction("mount")
                        }
                        SmallButton {
                            visible: root.protectionState.disk.mounted
                            iconName: "backup"
                            label: "Back up now"
                            emphasized: true
                            enabled: !root.actionPending
                            onClicked: root.requestAction("backup")
                        }
                        SmallButton {
                            visible: root.protectionState.disk.mounted
                            iconName: "restore"
                            label: "Restore point"
                            enabled: !root.actionPending
                            onClicked: root.requestAction("timeshift")
                        }
                        SmallButton {
                            visible: root.protectionState.disk.mounted
                            iconName: "verified"
                            label: "Check repository"
                            enabled: !root.actionPending
                            onClicked: root.requestAction("restic-check")
                        }
                        Item { Layout.fillWidth: true }
                        SmallButton {
                            visible: root.protectionState.disk.mounted
                            iconName: "eject"
                            label: "Safely unmount"
                            enabled: !root.actionPending
                            onClicked: root.confirm("eject", "Safely unmount backup HDD", "Running backup, restore-test, manifest, or repository-check operations must be finished first. The filesystem will be synced and unmounted; after success it is safe to disconnect the drive.")
                        }
                    }

                    Rectangle {
                        visible: root.actionPending || root.protectionState.action_running
                        Layout.fillWidth: true
                        implicitHeight: 58
                        radius: Appearance.rounding.normal
                        color: Appearance.colors.colSecondaryContainer
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 12
                            anchors.rightMargin: 12
                            spacing: 9
                            MaterialSymbol { text: "progress_activity"; iconSize: 19; color: Appearance.colors.colOnSecondaryContainer }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 1
                                StyledText {
                                    text: `${root.actionLabel(root.protectionState.current_action || root.lastRequestedAction)} in progress…`
                                    color: Appearance.colors.colOnSecondaryContainer
                                    font.pixelSize: Appearance.font.pixelSize.small
                                    font.weight: Font.DemiBold
                                }
                                StyledText {
                                    text: "The systemd job continues safely if you change pages or close this window."
                                    color: Appearance.colors.colOnSecondaryContainer
                                    font.pixelSize: Appearance.font.pixelSize.smallest
                                }
                            }
                        }
                    }

                    Section {
                        visible: root.protectionState.attention.length > 0
                        title: "Attention"
                        detail: `${root.protectionState.attention.length} item(s)`
                        iconName: "warning"
                    }
                    Rectangle {
                        visible: root.protectionState.attention.length > 0
                        Layout.fillWidth: true
                        implicitHeight: attentionColumn.implicitHeight + 18
                        radius: Appearance.rounding.normal
                        color: Appearance.colors.colLayer1Base
                        border.width: 1
                        border.color: Appearance.colors.colLayer0Border
                        ColumnLayout {
                            id: attentionColumn
                            anchors.fill: parent
                            anchors.margins: 9
                            spacing: 8
                            Repeater {
                                model: root.protectionState.attention.slice(0, 5)
                                delegate: Rectangle {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    implicitHeight: 56
                                    radius: Appearance.rounding.normal
                                    color: modelData.severity === "critical" ? Appearance.colors.colErrorContainer : Appearance.colors.colLayer2Base
                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 10
                                        anchors.rightMargin: 10
                                        spacing: 9
                                        MaterialSymbol {
                                            text: modelData.severity === "critical" ? "error"
                                                : modelData.severity === "warning" ? "warning"
                                                : modelData.severity === "history" ? "history" : "info"
                                            iconSize: 18
                                            color: modelData.severity === "critical" ? Appearance.colors.colOnErrorContainer : Appearance.colors.colSubtext
                                        }
                                        ColumnLayout {
                                            Layout.fillWidth: true
                                            spacing: 0
                                            StyledText {
                                                Layout.fillWidth: true
                                                text: modelData.title
                                                color: modelData.severity === "critical" ? Appearance.colors.colOnErrorContainer : Appearance.colors.colOnLayer1
                                                font.pixelSize: Appearance.font.pixelSize.small
                                                font.weight: Font.DemiBold
                                                elide: Text.ElideRight
                                            }
                                            StyledText {
                                                Layout.fillWidth: true
                                                text: modelData.detail
                                                color: modelData.severity === "critical" ? Appearance.colors.colOnErrorContainer : Appearance.colors.colSubtext
                                                font.pixelSize: Appearance.font.pixelSize.smallest
                                                elide: Text.ElideRight
                                            }
                                        }
                                        Pill {
                                            compact: true
                                            label: root.attentionTag(modelData)
                                            state: modelData.severity === "critical" ? "danger" : "neutral"
                                        }
                                    }
                                }
                            }
                        }
                    }

                    Section { title: "Core recovery"; detail: `${root.recoveryReady()}/${root.recoveryTotal()}`; iconName: "health_and_safety" }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: readinessColumn.implicitHeight + 18
                        radius: Appearance.rounding.normal
                        color: Appearance.colors.colLayer1Base
                        border.width: 1
                        border.color: Appearance.colors.colLayer0Border
                        ColumnLayout {
                            id: readinessColumn
                            anchors.fill: parent
                            anchors.margins: 9
                            spacing: 6
                            Repeater {
                                model: root.recoveryChecks().slice(0, 6)
                                delegate: RecoveryRow { required property var modelData; check: modelData }
                            }
                        }
                    }

                    Section { visible: root.protectionState.activity.length > 0; title: "Recent activity"; detail: `${Math.min(4, root.protectionState.activity.length)} shown`; iconName: "history" }
                    Rectangle {
                        visible: root.protectionState.activity.length > 0
                        Layout.fillWidth: true
                        implicitHeight: activityColumn.implicitHeight + 16
                        radius: Appearance.rounding.normal
                        color: Appearance.colors.colLayer1Base
                        border.width: 1
                        border.color: Appearance.colors.colLayer0Border
                        ColumnLayout {
                            id: activityColumn
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 8
                            spacing: 8
                            Repeater {
                                model: root.protectionState.activity.slice(0, 4)
                                delegate: ActivityRow { required property var modelData; eventData: modelData }
                            }
                        }
                    }
                    Item { Layout.preferredHeight: 6 }
                }
            }
        }
    }

    Component {
        id: backupsPage
        ColumnLayout {
            spacing: 9
            Heading {
                title: "Backups"
                subtitle: "Encrypted Restic snapshots, scheduling, and repository verification."
                meta: root.protectionState.restic.latest ? `Latest ${root.ageText(root.protectionState.restic.latest.time)}` : root.domainFreshness(root.protectionState.restic)
            }
            ScrollView {
                id: backupsScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    width: backupsScroll.availableWidth
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Metric {
                            iconName: "encrypted"
                            value: root.countText(root.protectionState.restic)
                            label: "Snapshots"
                            subtitle: root.protectionState.restic.latest
                                ? `Latest ${root.ageText(root.protectionState.restic.latest.time)} · ${root.domainFreshness(root.protectionState.restic)}`
                                : root.domainFreshness(root.protectionState.restic)
                            state: root.knownCount(root.protectionState.restic) && Number(root.protectionState.restic.count) > 0 ? "active"
                                : root.protectionState.restic.availability === "live" && Number(root.protectionState.restic.count) === 0 ? "danger" : "neutral"
                        }
                        Metric {
                            iconName: "schedule"
                            value: root.protectionState.restic.timer.ActiveState === "active" ? "Enabled" : "Needs review"
                            label: "Daily backup"
                            subtitle: "Systemd timer"
                            state: root.protectionState.restic.timer.ActiveState === "active" ? "active" : "neutral"
                        }
                        Metric {
                            iconName: "verified"
                            value: root.markerText(root.protectionState.restic.check)
                            label: "Repository check"
                            subtitle: root.protectionState.restic.check && root.protectionState.restic.check.known
                                ? root.ageText(root.protectionState.restic.check.time) : "No Recovery Center check recorded"
                            state: root.markerState(root.protectionState.restic.check)
                        }
                    }

                    Section { title: "Actions"; iconName: "bolt" }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        SmallButton {
                            visible: root.protectionState.disk.mounted
                            iconName: "backup"
                            label: "Back up now"
                            emphasized: true
                            enabled: !root.actionPending
                            onClicked: root.requestAction("backup")
                        }
                        SmallButton {
                            visible: root.protectionState.disk.mounted
                            iconName: "verified"
                            label: "Check repository"
                            enabled: !root.actionPending
                            onClicked: root.requestAction("restic-check")
                        }
                        SmallButton {
                            visible: root.protectionState.disk.connected && !root.protectionState.disk.mounted
                            iconName: "drive_file_move"
                            label: "Mount HDD"
                            emphasized: true
                            enabled: !root.actionPending
                            onClicked: root.requestAction("mount")
                        }
                        SmallButton {
                            visible: root.protectionState.disk.mounted
                            iconName: "eject"
                            label: "Safely unmount"
                            enabled: !root.actionPending
                            onClicked: root.confirm("eject", "Safely unmount backup HDD", "The filesystem will be synced and unmounted. Active backup or verification jobs will block this action.")
                        }
                        Item { Layout.fillWidth: true }
                    }

                    Section {
                        title: "Snapshot inventory"
                        detail: root.knownCount(root.protectionState.restic)
                            ? `${root.protectionState.restic.count} total · ${root.protectionState.restic.availability === "cached" ? "last known" : "live"}`
                            : "Unavailable"
                        iconName: "history"
                    }
                    Rectangle {
                        visible: root.protectionState.restic.snapshots.length > 0
                        Layout.fillWidth: true
                        implicitHeight: backupSnapshotsColumn.implicitHeight + 18
                        radius: Appearance.rounding.normal
                        color: Appearance.colors.colLayer1Base
                        border.width: 1
                        border.color: Appearance.colors.colLayer0Border
                        ColumnLayout {
                            id: backupSnapshotsColumn
                            anchors.fill: parent
                            anchors.margins: 9
                            spacing: 7
                            Repeater {
                                model: root.protectionState.restic.snapshots.slice(0, 10)
                                delegate: SnapshotRow { required property var modelData; item: modelData; kind: "restic" }
                            }
                        }
                    }
                    EmptyState {
                        visible: root.protectionState.restic.snapshots.length === 0
                        iconName: "backup"
                        title: !root.knownCount(root.protectionState.restic)
                            ? (root.protectionState.disk.connected && !root.protectionState.disk.mounted ? "Mount HDD to inspect backups" : "Backup inventory not verified")
                            : "No Restic snapshots"
                        detail: !root.knownCount(root.protectionState.restic)
                            ? "Unknown is not treated as zero. Connect and mount the backup HDD to verify the repository inventory."
                            : "The repository was queried successfully and contains no snapshots."
                    }

                    Section { title: "Storage state"; iconName: "hard_drive" }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: storageRows.implicitHeight + 22
                        radius: Appearance.rounding.normal
                        color: Appearance.colors.colLayer1Base
                        border.width: 1
                        border.color: Appearance.colors.colLayer0Border
                        ColumnLayout {
                            id: storageRows
                            anchors.fill: parent
                            anchors.margins: 11
                            spacing: 7
                            InfoRow { label: "Physical disk"; value: root.protectionState.disk.connected ? "Connected" : "Offline" }
                            InfoRow { label: "Filesystem"; value: root.protectionState.disk.mounted ? (root.protectionState.disk.filesystem_accessible ? "Mounted · accessible" : "Mounted · unavailable") : "Not mounted" }
                            InfoRow { label: "Repository data"; value: root.domainFreshness(root.protectionState.restic) }
                            InfoRow { label: "Free space"; value: root.protectionState.disk.mounted ? root.bytesText(root.protectionState.disk.free_bytes) : "—" }
                        }
                    }

                    Section { title: "Retention"; iconName: "calendar_month" }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: retentionRows.implicitHeight + 22
                        radius: Appearance.rounding.normal
                        color: Appearance.colors.colLayer1Base
                        border.width: 1
                        border.color: Appearance.colors.colLayer0Border
                        ColumnLayout {
                            id: retentionRows
                            anchors.fill: parent
                            anchors.margins: 11
                            spacing: 7
                            InfoRow { label: "Daily"; value: "Keep 7" }
                            InfoRow { label: "Weekly"; value: "Keep 4" }
                            InfoRow { label: "Monthly"; value: "Keep 6" }
                            InfoRow { label: "Baseline"; value: "Preserved by tag" }
                            InfoRow { label: "Maintenance"; value: root.protectionState.restic.maintenance_timer.ActiveState === "active" ? "Weekly timer enabled" : "Timer needs review" }
                        }
                    }
                    Item { Layout.preferredHeight: 6 }
                }
            }
        }
    }

    Component {
        id: restorePointsPage
        ColumnLayout {
            spacing: 9
            Heading {
                title: "Restore Points"
                subtitle: "Timeshift system rollback points. Configuration remains visible even when storage is offline."
                meta: root.knownCount(root.protectionState.timeshift) ? `${root.protectionState.timeshift.count} known` : root.domainFreshness(root.protectionState.timeshift)
            }
            ScrollView {
                id: restoreScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    width: restoreScroll.availableWidth
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Metric {
                            iconName: "restore"
                            value: root.countText(root.protectionState.timeshift)
                            label: "Restore points"
                            subtitle: `${root.protectionState.timeshift.mode || "RSYNC"} · ${root.domainFreshness(root.protectionState.timeshift)}`
                            state: root.knownCount(root.protectionState.timeshift) && Number(root.protectionState.timeshift.count) > 0 ? "active"
                                : root.protectionState.timeshift.availability === "live" && Number(root.protectionState.timeshift.count) === 0 ? "danger" : "neutral"
                        }
                        Metric {
                            iconName: "today"
                            value: root.protectionState.timeshift.schedule.daily ? `Keep ${root.protectionState.timeshift.schedule.daily_keep}` : "Off"
                            label: "Daily"
                            subtitle: "Configured policy"
                            state: root.protectionState.timeshift.schedule.daily ? "active" : "neutral"
                        }
                        Metric {
                            iconName: "date_range"
                            value: root.protectionState.timeshift.schedule.weekly ? `Keep ${root.protectionState.timeshift.schedule.weekly_keep}` : "Off"
                            label: "Weekly"
                            subtitle: "Configured policy"
                            state: root.protectionState.timeshift.schedule.weekly ? "active" : "neutral"
                        }
                    }

                    Section { title: "Actions"; iconName: "bolt" }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        SmallButton {
                            visible: root.protectionState.disk.mounted
                            iconName: "add_circle"
                            label: "Create restore point"
                            emphasized: true
                            enabled: !root.actionPending
                            onClicked: root.requestAction("timeshift")
                        }
                        SmallButton {
                            visible: root.protectionState.disk.connected && !root.protectionState.disk.mounted
                            iconName: "drive_file_move"
                            label: "Mount HDD"
                            emphasized: true
                            enabled: !root.actionPending
                            onClicked: root.requestAction("mount")
                        }
                        SmallButton {
                            visible: root.protectionState.disk.mounted
                            iconName: "menu_book"
                            label: "Recovery guide"
                            onClicked: root.openRestoreGuide()
                        }
                        Item { Layout.fillWidth: true }
                    }

                    Section {
                        title: "Available points"
                        detail: root.knownCount(root.protectionState.timeshift)
                            ? `${root.protectionState.timeshift.count} total · ${root.protectionState.timeshift.availability === "cached" ? "last known" : "live"}`
                            : "Unavailable"
                        iconName: "history"
                    }
                    Rectangle {
                        visible: root.protectionState.timeshift.snapshots.length > 0
                        Layout.fillWidth: true
                        implicitHeight: restoreSnapshotsColumn.implicitHeight + 18
                        radius: Appearance.rounding.normal
                        color: Appearance.colors.colLayer1Base
                        border.width: 1
                        border.color: Appearance.colors.colLayer0Border
                        ColumnLayout {
                            id: restoreSnapshotsColumn
                            anchors.fill: parent
                            anchors.margins: 9
                            spacing: 7
                            Repeater {
                                model: root.protectionState.timeshift.snapshots.slice(0, 10)
                                delegate: SnapshotRow { required property var modelData; item: modelData; kind: "timeshift" }
                            }
                        }
                    }
                    EmptyState {
                        visible: root.protectionState.timeshift.snapshots.length === 0
                        iconName: "restore"
                        title: !root.knownCount(root.protectionState.timeshift)
                            ? (root.protectionState.disk.connected && !root.protectionState.disk.mounted ? "Mount HDD to inspect restore points" : "Restore-point inventory not verified")
                            : "No restore points"
                        detail: !root.knownCount(root.protectionState.timeshift)
                            ? "The configured Daily/Weekly policy is still known. Mount the HDD to verify the snapshot inventory."
                            : "Timeshift was queried successfully and contains no restore points."
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: 76
                        radius: Appearance.rounding.normal
                        color: Appearance.colors.colLayer1Base
                        border.width: 1
                        border.color: Appearance.colors.colLayer0Border
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 12
                            spacing: 10
                            MaterialSymbol { text: "info"; iconSize: 19; color: Appearance.colors.colSubtext }
                            StyledText {
                                Layout.fillWidth: true
                                text: "System restore remains deliberately guided rather than a one-click destructive action. Use the Recovery page and RESTORE.md when rollback is actually needed."
                                color: Appearance.colors.colSubtext
                                font.pixelSize: Appearance.font.pixelSize.small
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                    Item { Layout.preferredHeight: 6 }
                }
            }
        }
    }

    Component {
        id: historyPage
        ColumnLayout {
            spacing: 9
            Heading {
                title: "Configuration History"
                subtitle: "Git history for personal desktop configuration and /etc system configuration."
                meta: "Read-only by design"
            }
            ScrollView {
                id: historyScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    width: historyScroll.availableWidth
                    spacing: 10

                    Section { title: "Personal configuration"; iconName: "person" }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: dotfilesRows.implicitHeight + 24
                        radius: Appearance.rounding.normal
                        color: Appearance.colors.colLayer1Base
                        border.width: 1
                        border.color: Appearance.colors.colLayer0Border
                        ColumnLayout {
                            id: dotfilesRows
                            anchors.fill: parent
                            anchors.margins: 12
                            spacing: 7
                            RowLayout {
                                Layout.fillWidth: true
                                StyledText {
                                    Layout.fillWidth: true
                                    text: "~/.dotfiles"
                                    color: Appearance.colors.colOnLayer1
                                    font.pixelSize: Appearance.font.pixelSize.small
                                    font.weight: Font.DemiBold
                                }
                                Pill {
                                    compact: true
                                    label: root.changedFilesText(root.protectionState.history.dotfiles)
                                    state: root.protectionState.history.dotfiles.available && root.protectionState.history.dotfiles.clean ? "active" : "neutral"
                                }
                            }
                            InfoRow {
                                label: "Latest commit"
                                value: root.protectionState.history.dotfiles.latest
                                    ? `${root.protectionState.history.dotfiles.latest.short_id} · ${root.protectionState.history.dotfiles.latest.subject}` : "—"
                            }
                            InfoRow {
                                label: "When"
                                value: root.protectionState.history.dotfiles.latest ? root.ageText(root.protectionState.history.dotfiles.latest.timestamp) : "—"
                            }
                        }
                    }

                    Section { title: "System configuration"; iconName: "settings" }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: etcRows.implicitHeight + 24
                        radius: Appearance.rounding.normal
                        color: Appearance.colors.colLayer1Base
                        border.width: 1
                        border.color: Appearance.colors.colLayer0Border
                        ColumnLayout {
                            id: etcRows
                            anchors.fill: parent
                            anchors.margins: 12
                            spacing: 7
                            RowLayout {
                                Layout.fillWidth: true
                                StyledText {
                                    Layout.fillWidth: true
                                    text: "/etc · etckeeper"
                                    color: Appearance.colors.colOnLayer1
                                    font.pixelSize: Appearance.font.pixelSize.small
                                    font.weight: Font.DemiBold
                                }
                                Pill {
                                    compact: true
                                    label: root.changedFilesText(root.protectionState.history.etc)
                                    state: root.protectionState.history.etc.available && root.protectionState.history.etc.clean ? "active" : "neutral"
                                }
                            }
                            InfoRow {
                                label: "Latest commit"
                                value: root.protectionState.history.etc.latest
                                    ? `${root.protectionState.history.etc.latest.short_id} · ${root.protectionState.history.etc.latest.subject}` : "—"
                            }
                            InfoRow {
                                label: "When"
                                value: root.protectionState.history.etc.latest ? root.ageText(root.protectionState.history.etc.latest.timestamp) : "—"
                            }
                        }
                    }

                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: 76
                        radius: Appearance.rounding.normal
                        color: Appearance.colors.colLayer1Base
                        border.width: 1
                        border.color: Appearance.colors.colLayer0Border
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 12
                            spacing: 10
                            MaterialSymbol { text: "info"; iconSize: 19; color: Appearance.colors.colSubtext }
                            StyledText {
                                Layout.fillWidth: true
                                text: "History stays read-only until a safe diff, commit, restore, and confirmation workflow is implemented. This prevents the control center from hiding Git operations behind ambiguous buttons."
                                color: Appearance.colors.colSubtext
                                font.pixelSize: Appearance.font.pixelSize.small
                                wrapMode: Text.WordWrap
                            }
                        }
                    }
                    Item { Layout.preferredHeight: 6 }
                }
            }
        }
    }

    Component {
        id: diskHealthPage
        ColumnLayout {
            spacing: 9
            Heading {
                title: "Disk Health"
                subtitle: "Current SMART evidence, historical errors, and human-readable self-test status."
                meta: root.protectionState.disk.model || "Backup disk"
            }
            ScrollView {
                id: healthScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    width: healthScroll.availableWidth
                    spacing: 10

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Metric {
                            iconName: "health_and_safety"
                            value: !root.protectionState.smart.known ? "Unavailable"
                                : root.protectionState.smart.condition === "critical" ? "Problem"
                                : root.protectionState.smart.condition === "history-warning" ? "Current OK" : "Healthy"
                            label: "SMART condition"
                            subtitle: !root.protectionState.smart.known ? "No verified SMART evidence"
                                : root.protectionState.smart.availability === "cached" ? `Last verified ${root.ageText(root.protectionState.smart.verified_at)}`
                                : root.protectionState.smart.condition === "history-warning" ? "Historical errors recorded · current counters clean" : "Live attribute check"
                            state: root.protectionState.smart.condition === "critical" ? "danger" : root.protectionState.smart.known ? "active" : "neutral"
                        }
                        Metric {
                            iconName: "device_thermostat"
                            value: root.protectionState.smart.temperature_c !== null && root.protectionState.smart.temperature_c !== undefined ? `${root.protectionState.smart.temperature_c}°C` : "—"
                            label: "Temperature"
                            subtitle: root.protectionState.smart.availability === "live" ? "Current drive temperature"
                                : root.protectionState.smart.known ? "Last known temperature" : "Unavailable"
                            state: "neutral"
                        }
                        Metric {
                            iconName: "schedule"
                            value: root.protectionState.smart.attributes.power_on_hours !== undefined ? `${root.protectionState.smart.attributes.power_on_hours} h` : "—"
                            label: "Power-on time"
                            subtitle: root.protectionState.smart.known ? "Lifetime hours" : "Unavailable"
                            state: "neutral"
                        }
                    }

                    Section { title: "Current sector counters"; detail: root.protectionState.smart.availability === "live" ? "live" : root.domainFreshness(root.protectionState.smart); iconName: "storage" }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: smartRows.implicitHeight + 22
                        radius: Appearance.rounding.normal
                        color: Appearance.colors.colLayer1Base
                        border.width: 1
                        border.color: Appearance.colors.colLayer0Border
                        ColumnLayout {
                            id: smartRows
                            anchors.fill: parent
                            anchors.margins: 11
                            spacing: 7
                            InfoRow { label: "Reallocated"; value: String(root.protectionState.smart.attributes.reallocated !== undefined ? root.protectionState.smart.attributes.reallocated : "—") }
                            InfoRow { label: "Pending"; value: String(root.protectionState.smart.attributes.pending !== undefined ? root.protectionState.smart.attributes.pending : "—") }
                            InfoRow { label: "Uncorrectable"; value: String(root.protectionState.smart.attributes.uncorrectable !== undefined ? root.protectionState.smart.attributes.uncorrectable : "—") }
                            InfoRow { label: "CRC errors"; value: String(root.protectionState.smart.attributes.crc_errors !== undefined ? root.protectionState.smart.attributes.crc_errors : "—") }
                            InfoRow { label: "Historical ATA errors"; value: String(root.protectionState.smart.historical_error_count !== null && root.protectionState.smart.historical_error_count !== undefined ? root.protectionState.smart.historical_error_count : "—") }
                        }
                    }

                    Rectangle {
                        visible: Number(root.protectionState.smart.historical_error_count || 0) > 0
                        Layout.fillWidth: true
                        implicitHeight: 70
                        radius: Appearance.rounding.normal
                        color: Appearance.colors.colLayer1Base
                        border.width: 1
                        border.color: Appearance.colors.colLayer0Border
                        RowLayout {
                            anchors.fill: parent
                            anchors.margins: 12
                            spacing: 10
                            MaterialSymbol { text: "history"; iconSize: 19; color: Appearance.colors.colSubtext }
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 1
                                StyledText {
                                    text: "Historical read errors are recorded"
                                    color: Appearance.colors.colOnLayer1
                                    font.pixelSize: Appearance.font.pixelSize.small
                                    font.weight: Font.DemiBold
                                }
                                StyledText {
                                    Layout.fillWidth: true
                                    text: "Historical ATA errors remain visible as history. Active risk is evaluated separately from the current reallocated, pending, and uncorrectable counters."
                                    color: Appearance.colors.colSubtext
                                    font.pixelSize: Appearance.font.pixelSize.smallest
                                    wrapMode: Text.WordWrap
                                }
                            }
                            Pill { compact: true; label: "HISTORY"; state: "neutral" }
                        }
                    }

                    Section { title: "Self-tests"; iconName: "science" }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: testRows.implicitHeight + 22
                        radius: Appearance.rounding.normal
                        color: Appearance.colors.colLayer1Base
                        border.width: 1
                        border.color: Appearance.colors.colLayer0Border
                        ColumnLayout {
                            id: testRows
                            anchors.fill: parent
                            anchors.margins: 11
                            spacing: 8
                            InfoRow { label: "Latest test"; value: root.latestSmartTestText() }
                            InfoRow { label: "Result detail"; value: root.latestSmartTestDetail() }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                SmallButton {
                                    visible: root.protectionState.disk.connected
                                    iconName: "bolt"
                                    label: root.smartDuration("short")
                                    enabled: !root.actionPending
                                    onClicked: root.requestAction("smart-short")
                                }
                                SmallButton {
                                    visible: root.protectionState.disk.connected
                                    iconName: "science"
                                    label: root.smartDuration("long")
                                    enabled: !root.actionPending
                                    onClicked: {
                                        const mins = root.protectionState.smart.test_durations.extended_minutes || 188
                                        root.confirm("smart-long", "Start extended SMART test", `The drive reports an estimated ${mins} minutes. Keep the HDD connected while the self-test is running. This does not mount or modify the filesystem.`)
                                    }
                                }
                                Item { Layout.fillWidth: true }
                            }
                        }
                    }
                    Item { Layout.preferredHeight: 6 }
                }
            }
        }
    }

    Component {
        id: recoveryPage
        ColumnLayout {
            spacing: 9
            Heading {
                title: "Recovery"
                subtitle: "Core disaster-recovery readiness, resilience recommendations, manifests, and the rebuild guide."
                meta: `${root.recoveryReady()}/${root.recoveryTotal()} core ready`
            }
            ScrollView {
                id: recoveryScroll
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    width: recoveryScroll.availableWidth
                    spacing: 10

                    Section { title: "Core recovery"; detail: `${root.recoveryReady()}/${root.recoveryTotal()}`; iconName: "checklist" }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: recoveryChecksColumn.implicitHeight + 18
                        radius: Appearance.rounding.normal
                        color: Appearance.colors.colLayer1Base
                        border.width: 1
                        border.color: Appearance.colors.colLayer0Border
                        ColumnLayout {
                            id: recoveryChecksColumn
                            anchors.fill: parent
                            anchors.margins: 9
                            spacing: 6
                            Repeater {
                                model: root.recoveryChecks()
                                delegate: RecoveryRow { required property var modelData; check: modelData }
                            }
                        }
                    }

                    Section { title: "Resilience"; iconName: "shield" }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: resilienceColumn.implicitHeight + 18
                        radius: Appearance.rounding.normal
                        color: Appearance.colors.colLayer1Base
                        border.width: 1
                        border.color: Appearance.colors.colLayer0Border
                        ColumnLayout {
                            id: resilienceColumn
                            anchors.fill: parent
                            anchors.margins: 9
                            spacing: 6
                            Repeater {
                                model: root.protectionState.recovery.resilience_checks || []
                                delegate: RecoveryRow { required property var modelData; check: modelData }
                            }
                        }
                    }

                    Section { title: "Verification actions"; iconName: "verified" }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        SmallButton {
                            visible: root.protectionState.disk.mounted
                            iconName: "verified"
                            label: "Verify restore"
                            emphasized: root.protectionState.restic.restore_test && root.protectionState.restic.restore_test.ok !== true
                            enabled: !root.actionPending
                            onClicked: root.requestAction("restore-test")
                        }
                        SmallButton {
                            visible: root.protectionState.disk.mounted
                            iconName: "sync"
                            label: "Refresh manifests"
                            enabled: !root.actionPending
                            onClicked: root.requestAction("refresh-manifests")
                        }
                        SmallButton {
                            visible: root.protectionState.disk.connected && !root.protectionState.disk.mounted
                            iconName: "drive_file_move"
                            label: "Mount HDD"
                            emphasized: true
                            enabled: !root.actionPending
                            onClicked: root.requestAction("mount")
                        }
                        Item { Layout.fillWidth: true }
                    }

                    Section { title: "Recovery material"; iconName: "folder_open" }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: recoveryInfoColumn.implicitHeight + 24
                        radius: Appearance.rounding.normal
                        color: Appearance.colors.colLayer1Base
                        border.width: 1
                        border.color: Appearance.colors.colLayer0Border
                        ColumnLayout {
                            id: recoveryInfoColumn
                            anchors.fill: parent
                            anchors.margins: 12
                            spacing: 8
                            InfoRow { label: "Guide"; value: root.protectionState.recovery.restore_doc_known ? "RESTORE.md verified" : "Not currently verified" }
                            InfoRow { label: "Guide path"; value: root.protectionState.recovery.restore_doc || "Not available" }
                            InfoRow { label: "Manifests"; value: root.protectionState.recovery.manifests_known ? "Available" : "Not currently verified" }
                            InfoRow { label: "Manifest freshness"; value: root.protectionState.recovery.manifests_updated_at ? root.ageText(root.protectionState.recovery.manifests_updated_at) : "Unknown" }
                            InfoRow { label: "Stored Arch ISO"; value: (root.protectionState.recovery.iso_files || []).length > 0 ? `${root.protectionState.recovery.iso_files.length} file(s)` : "None known" }
                            InfoRow { label: "Backup UUID"; value: root.protectionState.disk.uuid || "Not configured" }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6
                                SmallButton {
                                    visible: root.protectionState.disk.mounted && root.protectionState.recovery.restore_doc_known
                                    iconName: "menu_book"
                                    label: "Open RESTORE.md"
                                    onClicked: root.openRestoreGuide()
                                }
                                Item { Layout.fillWidth: true }
                            }
                        }
                    }

                    Section { title: "Recovery strategy"; iconName: "route" }
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: strategyColumn.implicitHeight + 24
                        radius: Appearance.rounding.normal
                        color: Appearance.colors.colLayer1Base
                        border.width: 1
                        border.color: Appearance.colors.colLayer0Border
                        ColumnLayout {
                            id: strategyColumn
                            anchors.fill: parent
                            anchors.margins: 12
                            spacing: 7
                            InfoRow { label: "Config mistake"; value: "Git / etckeeper" }
                            InfoRow { label: "Deleted file"; value: "Restic" }
                            InfoRow { label: "Broken Arch"; value: "Timeshift" }
                            InfoRow { label: "Dead NVMe"; value: "Arch reinstall + manifests + Restic" }
                            InfoRow { label: "Windows"; value: "Separate reinstall / image" }
                        }
                    }
                    Item { Layout.preferredHeight: 6 }
                }
            }
        }
    }
}
