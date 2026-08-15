/*
 * Popup contents: search field, notification list, footer button.
 *
 * Deliberately self-contained — it takes `entries` and emits signals rather
 * than reaching into the applet root, so it can be instantiated and checked
 * outside plasmashell (see tools/check-qml.py).
 *
 * Structure follows the shape documented in PlasmaExtras.Representation and
 * used by the shipped tray applets: exactly one `contentItem`, a ScrollView
 * whose own `contentItem` is the ListView. A Representation is a Page, and a
 * Page with more than one default child cannot compute a content size — it
 * collapses to zero and the popup has no implicit size outside the tray.
 */
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.components as PlasmaComponents
import org.kde.plasma.extras as PlasmaExtras
import org.kde.kirigami as Kirigami

PlasmaExtras.Representation {
    id: view

    /** Array of objects from notification-history-query. */
    property var entries: []

    signal entryActivated(int entryId)
    signal openFullRequested

    readonly property var visibleEntries: {
        const needle = searchField.text.toLowerCase();
        if (needle.length === 0) {
            return entries;
        }
        return entries.filter(entry =>
            (entry.app || "").toLowerCase().indexOf(needle) >= 0
            || (entry.summary || "").toLowerCase().indexOf(needle) >= 0
            || (entry.body || "").toLowerCase().indexOf(needle) >= 0);
    }

    // Consulted for a panel popup or desktop widget only. Inside the System
    // Tray these are ignored: PlasmoidPopupsContainer strips our anchors and
    // binds width/height to its own fixed-size stack.
    Layout.minimumWidth: Kirigami.Units.gridUnit * 20
    Layout.minimumHeight: Kirigami.Units.gridUnit * 16
    Layout.preferredWidth: Kirigami.Units.gridUnit * 26
    Layout.preferredHeight: Kirigami.Units.gridUnit * 24
    Layout.maximumWidth: Kirigami.Units.gridUnit * 80
    Layout.maximumHeight: Kirigami.Units.gridUnit * 40

    focus: true
    collapseMarginsHint: true

    header: PlasmaExtras.PlasmoidHeading {
        contentItem: RowLayout {
            spacing: Kirigami.Units.smallSpacing

            PlasmaExtras.SearchField {
                id: searchField

                Layout.fillWidth: true
                placeholderText: i18n("Search notifications…")

                Keys.onDownPressed: event => {
                    list.forceActiveFocus();
                    event.accepted = true;
                }
            }
        }
    }

    footer: PlasmaExtras.PlasmoidHeading {
        // The enum comes from the ToolBar base type; PlasmoidHeading.Position
        // is not reliably re-exported by QML-defined types.
        position: PlasmaComponents.ToolBar.Footer

        contentItem: RowLayout {
            spacing: Kirigami.Units.smallSpacing

            Item {
                Layout.fillWidth: true
            }

            PlasmaComponents.Button {
                text: i18n("Open full history…")
                icon.name: "view-list-details"
                onClicked: view.openFullRequested()
            }
        }
    }

    contentItem: PlasmaComponents.ScrollView {
        id: scrollView

        focus: true
        background: null
        contentWidth: availableWidth - list.leftMargin - list.rightMargin

        PlasmaComponents.ScrollBar.horizontal.policy: PlasmaComponents.ScrollBar.AlwaysOff

        contentItem: ListView {
            id: list

            focus: true
            currentIndex: -1
            model: view.visibleEntries
            reuseItems: true
            boundsBehavior: Flickable.StopAtBounds

            topMargin: Kirigami.Units.largeSpacing
            bottomMargin: Kirigami.Units.largeSpacing
            leftMargin: Kirigami.Units.largeSpacing
            rightMargin: Kirigami.Units.largeSpacing
            spacing: Kirigami.Units.smallSpacing

            highlight: PlasmaExtras.Highlight {}
            highlightMoveDuration: Kirigami.Units.shortDuration
            highlightResizeDuration: Kirigami.Units.shortDuration

            delegate: PlasmaComponents.ItemDelegate {
                id: entryDelegate

                required property var modelData

                width: ListView.view.width - ListView.view.leftMargin - ListView.view.rightMargin
                hoverEnabled: true
                onClicked: view.entryActivated(entryDelegate.modelData.id)

                contentItem: ColumnLayout {
                    spacing: 0

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Kirigami.Units.smallSpacing

                        PlasmaComponents.Label {
                            Layout.fillWidth: true
                            text: entryDelegate.modelData.summary || entryDelegate.modelData.app
                            elide: Text.ElideRight
                            textFormat: Text.PlainText
                            font.weight: entryDelegate.modelData.urgency === 2 ? Font.Bold : Font.Normal
                        }

                        PlasmaComponents.Label {
                            text: entryDelegate.modelData.when
                            font: Kirigami.Theme.smallFont
                            textFormat: Text.PlainText
                            opacity: 0.6
                        }
                    }

                    PlasmaComponents.Label {
                        Layout.fillWidth: true
                        text: entryDelegate.modelData.body
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                        font: Kirigami.Theme.smallFont
                        opacity: 0.75
                        visible: text.length > 0
                    }

                    PlasmaComponents.Label {
                        Layout.fillWidth: true
                        text: entryDelegate.modelData.status.length > 0
                            ? entryDelegate.modelData.app + " · " + entryDelegate.modelData.status
                            : entryDelegate.modelData.app
                        elide: Text.ElideRight
                        textFormat: Text.PlainText
                        font: Kirigami.Theme.smallFont
                        opacity: 0.45
                    }
                }
            }

            // Inside the ListView on purpose: a second default child on the
            // Representation would zero out its content size.
            Loader {
                anchors.centerIn: parent
                width: parent.width - Kirigami.Units.gridUnit * 4
                active: list.count === 0
                visible: active
                asynchronous: true

                sourceComponent: PlasmaExtras.PlaceholderMessage {
                    width: parent.width
                    iconName: view.entries.length === 0
                        ? "preferences-desktop-notification"
                        : "edit-none"
                    text: view.entries.length === 0
                        ? i18n("No notifications recorded yet")
                        : i18n("No matches")
                    explanation: view.entries.length === 0
                        ? i18n("Notifications are archived as they arrive.")
                        : ""
                }
            }
        }
    }
}
