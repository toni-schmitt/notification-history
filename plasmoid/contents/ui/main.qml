/*
 * Notification History — Plasma 6 applet.
 *
 * Reads the archive through `notification-history-query`, which prints JSON.
 * QML cannot open an arbitrary SQLite file (LocalStorage only reaches its own
 * hashed database directory), so a helper process is the practical route.
 * It only runs while the popup is open.
 */
import QtQuick
import QtQuick.Layouts
import org.kde.plasma.plasmoid
import org.kde.plasma.plasma5support as Plasma5Support
import org.kde.kirigami as Kirigami

PlasmoidItem {
    id: root

    property var entries: []

    // Absolute paths: plasmashell's PATH does not include ~/.local/bin, so bare
    // command names would fail. install.sh substitutes @BINDIR@.
    readonly property string binDir: "@BINDIR@"
    readonly property string queryCommand: binDir + "/notification-history-query --limit 60"

    Plasmoid.icon: "preferences-desktop-notification"
    toolTipMainText: i18n("Notification History")
    toolTipSubText: i18np("%1 recent notification", "%1 recent notifications", entries.length)

    // Do NOT set preferredRepresentation. The System Tray's setActiveApplet()
    // guards every branch on `!applet.preferredRepresentation`, so setting it —
    // even to compactRepresentation — makes the tray skip assigning activeApplet
    // and clear its popup stack. The result is a blank popup with no errors:
    // `expanded` still flips and the full representation is still built, they
    // are simply never shown. The form factor already selects compact in a panel
    // (AppletQuickItemPrivate::appletShouldBeExpanded).
    switchWidth: Kirigami.Units.gridUnit * 10
    switchHeight: Kirigami.Units.gridUnit * 10

    Plasma5Support.DataSource {
        id: runner

        engine: "executable"
        connectedSources: []

        onNewData: function (source, data) {
            disconnectSource(source);
            if (source !== root.queryCommand) {
                return;
            }
            if (data["exit code"] !== 0) {
                root.entries = [];
                return;
            }
            try {
                root.entries = JSON.parse(data.stdout);
            } catch (error) {
                root.entries = [];
            }
        }

        function run(command) {
            connectSource(command);
        }
    }

    function refresh() {
        runner.run(queryCommand);
    }

    function openViewer(entryId) {
        var viewer = binDir + "/notification-history";
        runner.run(entryId >= 0 ? viewer + " --select " + entryId : viewer);
        root.expanded = false;
    }

    onExpandedChanged: function (isExpanded) {
        if (isExpanded) {
            refresh();
        }
    }

    Timer {
        interval: 5000
        repeat: true
        running: root.expanded
        onTriggered: root.refresh()
    }

    // The tray looks for a MouseArea inside the compact representation and
    // forwards clicks to it. This mirrors libplasma's own compactrepresentation
    // example; the default would work too, but this adds the hover feedback.
    compactRepresentation: MouseArea {
        id: compact

        property bool wasExpanded: false

        Layout.minimumWidth: Kirigami.Units.iconSizes.small
        Layout.minimumHeight: Kirigami.Units.iconSizes.small

        hoverEnabled: true
        acceptedButtons: Qt.LeftButton

        Accessible.name: Plasmoid.title
        Accessible.role: Accessible.Button

        // Clicking outside collapses the popup before the click lands here, so
        // sample the state on press to make this a true toggle.
        onPressed: wasExpanded = root.expanded
        onClicked: root.expanded = !wasExpanded

        Kirigami.Icon {
            anchors.fill: parent
            source: "preferences-desktop-notification"
            active: compact.containsMouse
        }
    }

    fullRepresentation: FullRepresentation {
        focus: true
        entries: root.entries
        onEntryActivated: entryId => root.openViewer(entryId)
        onOpenFullRequested: root.openViewer(-1)
    }
}
