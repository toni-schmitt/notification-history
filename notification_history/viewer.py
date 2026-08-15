"""Qt6 window for browsing the notification archive."""

import argparse
import html
import json
import os
import subprocess
import sys
import time
from datetime import datetime

from PySide6.QtCore import (
    ClassInfo, QAbstractTableModel, QModelIndex, QSettings, Qt, QTimer, Slot,
)
from PySide6.QtDBus import QDBusAbstractAdaptor, QDBusConnection, QDBusInterface
from PySide6.QtGui import QAction, QColor, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMainWindow, QMessageBox, QSplitter, QTableView, QTextBrowser,
    QVBoxLayout, QWidget,
)

from .archive import CLOSE_REASONS, URGENCY_NAMES, Reader, default_db_path
from .format import format_time, status_text, strip_markup

ALL_APPS = "All applications"
COLUMNS = ("Time", "Application", "Summary", "Body", "Status")

# Single-instance handover: the Plasma widget launches this command on every
# click, and a second window each time would be obnoxious.
DBUS_SERVICE = "org.kde.notificationhistory.Viewer"
DBUS_PATH = "/Viewer"
DBUS_IFACE = "org.kde.notificationhistory.Viewer"

RANGES = (
    ("All time", None),
    ("Today", "today"),
    ("Last 7 days", 7),
    ("Last 30 days", 30),
)


class NotificationModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows = []

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0:
                return format_time(row["ts"])
            if column == 1:
                return row["app"]
            if column == 2:
                return strip_markup(row["summary"])
            if column == 3:
                return strip_markup(row["body"])
            if column == 4:
                return status_text(row)
        elif role == Qt.ItemDataRole.ForegroundRole and row["urgency"] == 0:
            return QColor(128, 128, 128)
        elif role == Qt.ItemDataRole.ToolTipRole:
            return strip_markup(row["body"]) or strip_markup(row["summary"])
        return None

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def row_at(self, index):
        return self.rows[index] if 0 <= index < len(self.rows) else None


class HistoryWindow(QMainWindow):
    LIMIT = 10000

    def __init__(self, db_path=None):
        super().__init__()
        self.reader = Reader(db_path or default_db_path())
        self.fingerprint = None
        self.setWindowTitle("Notification History")

        self.model = NotificationModel()
        self._build_ui()
        self._build_menu()

        self.settings = QSettings("notification-history", "viewer")
        geometry = self.settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1100, 700)

        self.reload(force=True)

        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.reload)
        self.refresh_timer.start(3000)

        self.service_timer = QTimer(self)
        self.service_timer.timeout.connect(self.update_service_state)
        self.service_timer.start(15000)
        self.update_service_state()

    # ---------------------------------------------------------------- UI

    def _build_ui(self):
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search summary, body, application…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _: self.reload(force=True))

        self.app_filter = QComboBox()
        self.app_filter.setMinimumWidth(200)
        self.app_filter.currentIndexChanged.connect(lambda _: self.reload(force=True))

        self.range_filter = QComboBox()
        for label, _ in RANGES:
            self.range_filter.addItem(label)
        self.range_filter.currentIndexChanged.connect(lambda _: self.reload(force=True))

        self.urgency_filter = QComboBox()
        self.urgency_filter.addItem("Any urgency", None)
        for level, name in sorted(URGENCY_NAMES.items()):
            self.urgency_filter.addItem(name, level)
        self.urgency_filter.currentIndexChanged.connect(lambda _: self.reload(force=True))

        filters = QHBoxLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.app_filter)
        filters.addWidget(self.range_filter)
        filters.addWidget(self.urgency_filter)

        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.resizeSection(2, 320)
        self.table.selectionModel().selectionChanged.connect(self.show_detail)

        self.detail = QTextBrowser()
        self.detail.setOpenLinks(False)
        self.detail.setPlaceholderText("Select a notification to see its details.")

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.table)
        splitter.addWidget(self.detail)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        layout = QVBoxLayout()
        layout.addLayout(filters)
        layout.addWidget(splitter, 1)
        central = QWidget()
        central.setLayout(layout)
        self.setCentralWidget(central)

        self.service_label = QLabel()
        self.statusBar().addPermanentWidget(self.service_label)

    def _build_menu(self):
        file_menu = self.menuBar().addMenu("&File")

        export = QAction("&Export current view…", self)
        export.setShortcut(QKeySequence("Ctrl+E"))
        export.triggered.connect(self.export_view)
        file_menu.addAction(export)

        refresh = QAction("&Refresh", self)
        refresh.setShortcut(QKeySequence.StandardKey.Refresh)
        refresh.triggered.connect(lambda: self.reload(force=True))
        file_menu.addAction(refresh)
        file_menu.addSeparator()

        quit_action = QAction("&Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        find = QAction("&Find", self)
        find.setShortcut(QKeySequence.StandardKey.Find)
        find.triggered.connect(self.search.setFocus)
        self.addAction(find)

        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("&About", self)
        about.triggered.connect(self.show_about)
        help_menu.addAction(about)

    # ---------------------------------------------------------- data access

    def current_filters(self):
        label, span = RANGES[max(0, self.range_filter.currentIndex())]
        since = days = None
        if span == "today":
            since = datetime.now().replace(
                hour=0, minute=0, second=0, microsecond=0).timestamp()
        elif isinstance(span, int):
            days = span
        app = self.app_filter.currentText()
        return {
            "search": self.search.text().strip() or None,
            "app": None if app in ("", ALL_APPS) else app,
            "since": since,
            "days": days,
            "urgency": self.urgency_filter.currentData(),
            "limit": self.LIMIT,
        }

    def reload(self, force=False):
        if not self.reader.available:
            self.statusBar().showMessage(
                f"No archive yet at {self.reader.path} — is notification-logger running?")
            return

        fingerprint = self.reader.fingerprint()
        if not force and fingerprint == self.fingerprint:
            return
        self.fingerprint = fingerprint

        self.refresh_app_filter()
        rows = self.reader.fetch(**self.current_filters())

        selected = self.selected_row_id()
        self.model.set_rows(rows)
        self.restore_selection(selected)

        total = fingerprint[0]
        capped = " (capped)" if len(rows) == self.LIMIT else ""
        self.statusBar().showMessage(
            f"{len(rows)} shown{capped} · {total} archived · "
            f"{self.reader.size() / (1024 * 1024):.1f} MiB")

    def refresh_app_filter(self):
        wanted = [ALL_APPS] + self.reader.apps()
        current = self.app_filter.currentText()
        if [self.app_filter.itemText(i) for i in range(self.app_filter.count())] == wanted:
            return
        self.app_filter.blockSignals(True)
        self.app_filter.clear()
        self.app_filter.addItems(wanted)
        self.app_filter.setCurrentIndex(max(0, self.app_filter.findText(current)))
        self.app_filter.blockSignals(False)

    def selected_row_id(self):
        indexes = self.table.selectionModel().selectedRows()
        if not indexes:
            return None
        row = self.model.row_at(indexes[0].row())
        return row["id"] if row else None

    def restore_selection(self, row_id):
        if row_id is None:
            return
        for position, row in enumerate(self.model.rows):
            if row["id"] == row_id:
                self.table.selectRow(position)
                return

    def select_notification(self, row_id):
        """Jump to a specific row — used by --select and the D-Bus handover."""
        self.search.clear()
        self.app_filter.setCurrentIndex(0)
        self.range_filter.setCurrentIndex(0)
        self.urgency_filter.setCurrentIndex(0)
        self.reload(force=True)
        self.restore_selection(row_id)
        self.table.setFocus()

    # ------------------------------------------------------------- detail

    def show_detail(self):
        indexes = self.table.selectionModel().selectedRows()
        if not indexes:
            self.detail.clear()
            return
        row = self.model.row_at(indexes[0].row())
        if row is None:
            return

        def field(label, value):
            if value in (None, "", 0):
                return ""
            return (f"<tr><td style='padding-right:12px;color:palette(mid)'>{label}</td>"
                    f"<td>{html.escape(str(value))}</td></tr>")

        stamp = "%Y-%m-%d %H:%M:%S"
        process = row["process"]
        if row["pid"]:
            process = f"{process} (pid {row['pid']})"
        fields = [
            field("Application", row["app"]),
            field("Desktop entry", row["desktop_entry"]),
            field("Process", process),
            field("Received", time.strftime(stamp, time.localtime(row["ts"]))),
            field("Last update", time.strftime(stamp, time.localtime(row["ts_updated"]))
                  if row["ts_updated"] else ""),
            field("Closed", (time.strftime(stamp, time.localtime(row["closed_ts"]))
                             + f" ({CLOSE_REASONS.get(row['closed_reason'], 'unknown')})")
                  if row["closed_ts"] else ""),
            field("Activated", row["action_key"]),
            field("Urgency", URGENCY_NAMES.get(row["urgency"], row["urgency"])),
            field("Category", row["category"]),
            field("Transport", "XDG portal" if row["source"] == "portal" else "freedesktop"),
            field("Icon", row["icon"]),
            field("Updates folded in", row["updates"]),
        ]
        actions = json.loads(row["actions"] or "[]")
        if actions:
            pairs = ", ".join(f"{actions[i + 1]} ({actions[i]})"
                              for i in range(0, len(actions) - 1, 2))
            fields.append(field("Buttons", pairs))

        self.detail.setHtml(
            f"<h3 style='margin:0 0 6px 0'>{html.escape(strip_markup(row['summary']))}</h3>"
            f"<div style='margin-bottom:10px'>{row['body'] or ''}</div>"
            f"<table style='font-size:small'>{''.join(fields)}</table>")

    # ------------------------------------------------------------ actions

    def export_view(self):
        if not self.model.rows:
            QMessageBox.information(self, "Export", "Nothing to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export current view",
            os.path.expanduser(f"~/notifications-{datetime.now():%Y%m%d-%H%M%S}.json"),
            "JSON (*.json)")
        if not path:
            return
        payload = [dict(row) for row in self.model.rows]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        self.statusBar().showMessage(
            f"Exported {len(payload)} notifications to {path}", 5000)

    def update_service_state(self):
        try:
            state = subprocess.run(
                ["systemctl", "--user", "is-active", "notification-logger.service"],
                capture_output=True, text=True, timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            state = "unknown"
        colour = "palette(text)" if state == "active" else "#c9532c"
        self.service_label.setText(f"<span style='color:{colour}'>Recorder: {state}</span>")

    def show_about(self):
        QMessageBox.about(
            self, "About Notification History",
            "<h3>Notification History</h3>"
            "<p>Browses the archive written by <code>notification-logger</code>, "
            "which records every notification sent over the session bus — including "
            "the ones you dismiss before reading.</p>"
            f"<p><small>Archive: {html.escape(self.reader.path)}</small></p>")

    def closeEvent(self, event):
        self.settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)


@ClassInfo({"D-Bus Interface": DBUS_IFACE})
class ViewerAdaptor(QDBusAbstractAdaptor):
    """Lets a second launch raise the running window instead of opening another."""

    def __init__(self, window):
        super().__init__(window)
        self._window = window

    @Slot(int)
    def Present(self, row_id):
        self._window.showNormal()
        self._window.raise_()
        self._window.activateWindow()
        if row_id >= 0:
            self._window.select_notification(row_id)


def hand_over_to_running_instance(row_id):
    """Return True if an existing viewer took the request."""
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return False
    interface = QDBusInterface(DBUS_SERVICE, DBUS_PATH, DBUS_IFACE, bus)
    if not interface.isValid():
        return False
    interface.call("Present", row_id if row_id is not None else -1)
    return True


def main():
    parser = argparse.ArgumentParser(
        prog="notification-history",
        description="Browse the archive of desktop notifications.")
    parser.add_argument("--db", help="archive path")
    parser.add_argument("--select", type=int, metavar="ID",
                        help="open with this notification selected")
    options, extra = parser.parse_known_args()

    app = QApplication(sys.argv[:1] + extra)
    app.setApplicationName("Notification History")
    app.setDesktopFileName("notification-history")

    if hand_over_to_running_instance(options.select):
        return 0

    window = HistoryWindow(options.db)
    bus = QDBusConnection.sessionBus()
    if bus.isConnected() and bus.registerService(DBUS_SERVICE):
        ViewerAdaptor(window)
        bus.registerObject(DBUS_PATH, window, QDBusConnection.ExportAdaptors)

    window.show()
    if options.select is not None:
        window.select_notification(options.select)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
