import qs
import qs.modules.common
import qs.modules.common.widgets
import QtQuick
import QtQuick.Layouts

Rectangle {
    id: bar

    property string actionName: "Operation"
    property string phaseText: "Operation running"
    property string metaText: ""
    property real percent: -1

    implicitHeight: visible ? 68 : 0
    radius: Appearance.radius.card
    color: Appearance.colors.colSecondaryContainer
    clip: true

    Accessible.role: Accessible.StatusBar
    Accessible.name: `${bar.actionName} in progress`
    Accessible.description: `${bar.phaseText}${bar.metaText.length > 0 ? " · " + bar.metaText : ""}`

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 12
        anchors.rightMargin: 12
        anchors.topMargin: 8
        anchors.bottomMargin: 8
        spacing: 5

        RowLayout {
            Layout.fillWidth: true
            spacing: 9

            MaterialSymbol {
                text: "progress_activity"
                iconSize: 19
                color: Appearance.colors.colOnSecondaryContainer
            }

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1

                StyledText {
                    Layout.fillWidth: true
                    text: `${bar.actionName} in progress`
                    color: Appearance.colors.colOnSecondaryContainer
                    font.pixelSize: Appearance.font.pixelSize.small
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }

                StyledText {
                    Layout.fillWidth: true
                    text: `${bar.phaseText}${bar.metaText.length > 0 ? " · " + bar.metaText : ""}`
                    color: Appearance.colors.colOnSecondaryContainer
                    font.pixelSize: Appearance.font.pixelSize.smallest
                    elide: Text.ElideRight
                }
            }

            StyledText {
                visible: bar.percent >= 0
                text: `${Math.round(bar.percent)}%`
                color: Appearance.colors.colOnSecondaryContainer
                font.pixelSize: Appearance.font.pixelSize.small
                font.weight: Font.DemiBold
            }
        }

        Rectangle {
            visible: bar.percent >= 0
            Layout.fillWidth: true
            Layout.preferredHeight: 3
            radius: 2
            color: Qt.rgba(
                Appearance.colors.colOnSecondaryContainer.r,
                Appearance.colors.colOnSecondaryContainer.g,
                Appearance.colors.colOnSecondaryContainer.b,
                0.20
            )

            Rectangle {
                height: parent.height
                width: parent.width * Math.max(0, Math.min(100, bar.percent)) / 100
                radius: parent.radius
                color: Appearance.colors.colOnSecondaryContainer

                Behavior on width {
                    NumberAnimation { duration: 220 }
                }
            }
        }
    }
}
