"""Record every desktop notification passing over the session bus.

Attaches to the session bus in monitor mode (org.freedesktop.DBus.Monitoring
BecomeMonitor) and records both notification transports that Plasma 6 uses:

  * org.freedesktop.Notifications             — the classic freedesktop interface
  * org.freedesktop.impl.portal.Notification  — the XDG portal backend that
    plasmashell registers for sandboxed (Flatpak) applications

The recorder never implements the notification service itself, so Plasma's own
server keeps handling popups, grouping and Do Not Disturb exactly as before.

Known blind spot: plasmashell can inject notifications straight into its model
through libnotificationmanager's C++ ``Server::add()`` with no bus traffic at
all. Those are invisible here, and Plasma itself keeps them out of its history.
"""

import argparse
import json
import signal
import sys
import time

import dbus
import dbus.lowlevel
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib

try:  # GLib.unix_signal_add is deprecated in newer PyGObject
    from gi.repository import GLibUnix
    add_signal_handler = GLibUnix.signal_add
except ImportError:  # pragma: no cover - older PyGObject
    add_signal_handler = GLib.unix_signal_add

from .archive import Archive, Reader, REASON_CLOSED, default_db_path

FDO = "org.freedesktop.Notifications"
PORTAL = "org.freedesktop.impl.portal.Notification"

MATCH_RULES = (
    f"type='method_call',interface='{FDO}',member='Notify'",
    f"type='method_return',sender='{FDO}'",
    f"type='signal',interface='{FDO}',member='NotificationClosed'",
    f"type='signal',interface='{FDO}',member='ActionInvoked'",
    f"type='method_call',interface='{PORTAL}'",
    f"type='signal',interface='{PORTAL}'",
)

# Hints whose payload is raw pixel data — recorded as a placeholder, never stored.
IMAGE_HINTS = ("image-data", "image_data", "icon_data")

# Portal priority strings mapped onto freedesktop urgency levels.
PRIORITY_URGENCY = {"low": 0, "normal": 1, "high": 2, "urgent": 2}


def as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def plain(value):
    """Convert a D-Bus value tree into something json.dumps can handle."""
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, (bytes, bytearray)):
        return f"<{len(value)} bytes>"
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    return str(value)


def describe_image(value):
    """Summarise an image hint without keeping the pixels."""
    try:
        return f"<image {int(value[0])}x{int(value[1])}>"
    except (IndexError, KeyError, TypeError, ValueError):
        return "<image>"


def clean_hints(hints):
    cleaned = {}
    for key, value in (hints or {}).items():
        key = str(key)
        cleaned[key] = describe_image(value) if key in IMAGE_HINTS else plain(value)
    return cleaned


class Recorder:
    """Bus monitor translating notification traffic into archive rows."""

    def __init__(self, archive, verbose=False):
        self.archive = archive
        self.verbose = verbose
        self._pending = {}        # (caller, serial) -> (rowid, seen_at)
        self._process_cache = {}  # unique bus name -> (pid, comm)
        self._lookup_bus = dbus.SessionBus(private=True)
        self._monitor_bus = dbus.SessionBus(private=True)

    def start(self):
        self._monitor_bus.call_blocking(
            "org.freedesktop.DBus", "/org/freedesktop/DBus",
            "org.freedesktop.DBus.Monitoring", "BecomeMonitor",
            "asu", (list(MATCH_RULES), dbus.UInt32(0)),
        )
        self._monitor_bus.add_message_filter(self._on_message)
        GLib.timeout_add_seconds(60, self._expire_pending)

    def _log(self, *parts):
        if self.verbose:
            print(*parts, file=sys.stderr, flush=True)

    def _expire_pending(self):
        cutoff = time.time() - 120
        for key in [k for k, (_, seen) in self._pending.items() if seen < cutoff]:
            del self._pending[key]
        return GLib.SOURCE_CONTINUE

    def _process_for(self, sender):
        """Resolve a unique bus name to (pid, process name), cached."""
        if not sender:
            return None, None
        if sender in self._process_cache:
            return self._process_cache[sender]
        pid = process = None
        try:
            pid = int(self._lookup_bus.call_blocking(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "GetConnectionUnixProcessID", "s", (sender,), timeout=2,
            ))
        except dbus.DBusException:
            pid = None
        if pid:
            try:
                with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as handle:
                    process = handle.read().strip()
            except OSError:
                process = None
        if len(self._process_cache) > 512:
            self._process_cache.clear()
        self._process_cache[sender] = (pid, process)
        return pid, process

    def _on_message(self, _bus, message):
        try:
            self._dispatch(message)
        except Exception as error:  # never let one bad message kill the recorder
            print(f"notification-logger: {error!r}", file=sys.stderr, flush=True)
        return dbus.lowlevel.HANDLER_RESULT_HANDLED

    def _dispatch(self, message):
        interface = message.get_interface()
        member = message.get_member()
        if isinstance(message, dbus.lowlevel.MethodCallMessage):
            if interface == FDO and member == "Notify":
                self._on_notify(message)
            elif interface == PORTAL and member == "AddNotification":
                self._on_portal_add(message)
            elif interface == PORTAL and member == "RemoveNotification":
                self._on_portal_remove(message)
        elif isinstance(message, dbus.lowlevel.MethodReturnMessage):
            self._on_reply(message)
        elif isinstance(message, dbus.lowlevel.SignalMessage):
            args = message.get_args_list()
            if interface == FDO and member == "NotificationClosed" and len(args) >= 2:
                self.archive.close("fdo", str(as_int(args[0])), as_int(args[1]))
            elif interface == FDO and member == "ActionInvoked" and len(args) >= 2:
                self.archive.record_action("fdo", str(as_int(args[0])), str(args[1]))
            elif interface == PORTAL and member == "ActionInvoked" and len(args) >= 3:
                self.archive.record_action("portal", f"{args[0]}/{args[1]}", str(args[2]))

    def _on_notify(self, message):
        args = list(message.get_args_list(byte_arrays=True))
        args += [None] * (8 - len(args))
        app_name, replaces_id, icon, summary, body, actions, hints, timeout = args[:8]
        hints = clean_hints(hints)
        sender = str(message.get_sender() or "")
        pid, process = self._process_for(sender)
        record = {
            "source": "fdo",
            "app_name": str(app_name or "") or (process or ""),
            "desktop_entry": hints.get("desktop-entry"),
            "summary": str(summary or ""),
            "body": str(body or ""),
            "icon": str(icon or ""),
            "urgency": as_int(hints.get("urgency"), 1),
            "category": hints.get("category"),
            "actions": json.dumps([str(item) for item in (actions or [])]),
            "hints": json.dumps(hints, ensure_ascii=False),
            "timeout": as_int(timeout),
            "sender": sender,
            "pid": pid,
            "process": process,
        }
        self._log("notify", record["app_name"], "|", record["summary"])

        replaces = as_int(replaces_id)
        if replaces:
            # An update to a notification still on screen — fold it into that row.
            record["srv_id"] = str(replaces)
            if self.archive.update_in_place("fdo", str(replaces), record) is None:
                self.archive.insert(record)
            return

        # The server assigns the id in its reply; correlate on (caller, serial).
        rowid = self.archive.insert(record)
        self._pending[(message.get_sender(), message.get_serial())] = (rowid, time.time())

    def _on_reply(self, message):
        entry = self._pending.pop(
            (message.get_destination(), message.get_reply_serial()), None)
        if entry is None:
            return
        args = message.get_args_list()
        if args:
            self.archive.set_server_id(entry[0], str(as_int(args[0])))

    def _on_portal_add(self, message):
        args = list(message.get_args_list(byte_arrays=True))
        if len(args) < 3:
            return
        app_id, notif_id, data = str(args[0]), str(args[1]), clean_hints(args[2])
        actions = []
        for button in data.get("buttons") or []:
            if isinstance(button, dict):
                actions += [str(button.get("action", "")), str(button.get("label", ""))]
        icon = data.get("icon")
        # The bus sender here is xdg-desktop-portal, not the app — app_id is the
        # only reliable attribution for portal notifications.
        sender = str(message.get_sender() or "")
        pid, process = self._process_for(sender)
        record = {
            "source": "portal",
            "app_name": app_id or (process or ""),
            "desktop_entry": app_id,
            "summary": str(data.get("title", "")),
            "body": str(data.get("body", "")),
            "icon": "" if icon is None else json.dumps(icon, ensure_ascii=False),
            "urgency": PRIORITY_URGENCY.get(str(data.get("priority", "normal")), 1),
            "category": data.get("category"),
            "actions": json.dumps(actions),
            "hints": json.dumps(data, ensure_ascii=False),
            "timeout": 0,
            "srv_id": f"{app_id}/{notif_id}",
            "sender": sender,
            "pid": pid,
            "process": process,
        }
        self._log("portal", record["app_name"], "|", record["summary"])
        if self.archive.update_in_place("portal", record["srv_id"], record) is None:
            self.archive.insert(record)

    def _on_portal_remove(self, message):
        args = message.get_args_list()
        if len(args) >= 2:
            self.archive.close("portal", f"{args[0]}/{args[1]}", REASON_CLOSED)


def print_stats(path):
    reader = Reader(path)
    if not reader.available:
        print(f"no archive at {path}")
        return
    total, first, last, per_app = reader.stats()
    print(f"archive : {path}")
    print(f"entries : {total} ({reader.size() / 1024:.1f} KiB)")
    if total:
        stamp = "%Y-%m-%d %H:%M:%S"
        print(f"earliest: {time.strftime(stamp, time.localtime(first))}")
        print(f"latest  : {time.strftime(stamp, time.localtime(last))}")
        print("\ntop senders:")
        for app, count in per_app:
            print(f"  {count:6d}  {app}")


def main():
    parser = argparse.ArgumentParser(
        prog="notification-logger",
        description="Archive every desktop notification into SQLite.")
    parser.add_argument("--db", default=default_db_path(), help="archive path")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="echo every captured notification to stderr")
    parser.add_argument("--stats", action="store_true",
                        help="print archive statistics and exit")
    options = parser.parse_args()

    if options.stats:
        print_stats(options.db)
        return 0

    archive = Archive(options.db)
    DBusGMainLoop(set_as_default=True)
    try:
        recorder = Recorder(archive, verbose=options.verbose)
        recorder.start()
    except dbus.DBusException as error:
        print(f"notification-logger: cannot monitor the session bus: {error}",
              file=sys.stderr)
        return 1

    loop = GLib.MainLoop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        add_signal_handler(GLib.PRIORITY_DEFAULT, sig, lambda: (loop.quit(), True)[1])
    print(f"notification-logger: recording to {options.db}", file=sys.stderr, flush=True)
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
