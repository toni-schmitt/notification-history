# Technical notes

## Why a bus monitor

Plasma's notification history is a `QAbstractListModel` living in plasmashell.
There is no persistence layer to hook into and no configuration key that enables
one — the notification KConfig schema
(`libnotificationmanager/kcfg/notificationsettings.kcfg`) has exactly six keys,
none of them related to storage. `plasmanotifyrc` holds per-application policy
only.

Worth knowing: the shipped `/etc/xdg/plasmanotifyrc` sets
`[Applications][@other] ShowInHistory=false`, so applications without their own
notifyrc never enter Plasma's history at all, dismissed or not. Verify with:

```bash
kreadconfig6 --file plasmanotifyrc --group Applications --group @other --key ShowInHistory
```

Notification content never reaches the journal either, so there is no
journald-based shortcut. Watching the bus is the only vantage point that sees
everything.

## Transports

Plasma 6.7 registers plasmashell as an XDG desktop portal backend:

```
/usr/share/xdg-desktop-portal/portals/plasmanotify.portal
  DBusName=org.freedesktop.impl.portal.desktop.plasmanotify
  Interfaces=org.freedesktop.impl.portal.Notification;
  UseIn=kde
```

Confirm it is live with `busctl --user list | grep plasmanotify` — the name
should be owned by the plasmashell process.

This means there are two paths into the same notification model:

| Sender | Interface | Method |
| ------------------- | ------------------------------------------ | ------------------- |
| ordinary app | `org.freedesktop.Notifications` | `Notify` |
| sandboxed (Flatpak) | `org.freedesktop.impl.portal.Notification` | `AddNotification` |

The recorder matches both. Note that for the portal path the bus sender is
`xdg-desktop-portal`, not the originating application, so `app_id` is the only
usable attribution — the recorded `pid`/`process` will point at the portal.

Only the `impl.portal` (backend) interface is matched, not the front-end
`org.freedesktop.portal.Notification`. Matching both would record each
sandboxed notification twice.

## Match rules

```
type='method_call',interface='org.freedesktop.Notifications',member='Notify'
type='method_return',sender='org.freedesktop.Notifications'
type='signal',interface='org.freedesktop.Notifications',member='NotificationClosed'
type='signal',interface='org.freedesktop.Notifications',member='ActionInvoked'
type='method_call',interface='org.freedesktop.impl.portal.Notification'
type='signal',interface='org.freedesktop.impl.portal.Notification'
```

`BecomeMonitor` puts the connection into receive-only mode — it can no longer
send anything. That is why the recorder holds a *second*, ordinary bus
connection purely for `GetConnectionUnixProcessID` lookups.

## Correlating ids

`Notify` does not carry the notification id; the server assigns it and returns
it in the method reply. The recorder therefore:

1. Inserts the row when it sees the `Notify` call, remembering
   `(caller unique name, message serial) -> row id`.
2. Reads the id from the matching `method_return` (matched on
   `(destination, reply_serial)`) and writes it into `srv_id`.
3. Uses `srv_id` to attach the later `NotificationClosed` reason and any
   `ActionInvoked` key to the right row.

Pending correlations are dropped after 120 seconds.

For the portal path the id is supplied by the caller as a string, so
`srv_id` is stored as `"<app_id>/<id>"` and no correlation step is needed.

### Replacements

A `Notify` with a non-zero `replaces_id` updates a notification already on
screen — a download progress bar can send hundreds per second. Those are folded
into the original row: `summary`, `body` and friends are overwritten, `ts` is
preserved, `ts_updated` is refreshed and `updates` is incremented. The same
applies to a portal `AddNotification` reusing an id.

## Schema

```sql
CREATE TABLE notifications (
    id            INTEGER PRIMARY KEY,
    source        TEXT    NOT NULL,  -- 'fdo' | 'portal'
    ts            REAL    NOT NULL,  -- first seen, unix time
    ts_updated    REAL,              -- last in-place replacement
    app_name      TEXT,              -- self-declared name / portal app_id
    desktop_entry TEXT,
    summary       TEXT,
    body          TEXT,              -- may contain limited HTML markup
    icon          TEXT,
    urgency       INTEGER,           -- 0 low, 1 normal, 2 critical
    category      TEXT,
    actions       TEXT,              -- JSON array, [key, label, ...]
    hints         TEXT,              -- JSON object, image payloads elided
    timeout       INTEGER,
    srv_id        TEXT,              -- server-assigned id
    sender        TEXT,              -- unique bus name
    pid           INTEGER,
    process       TEXT,              -- /proc/<pid>/comm
    updates       INTEGER NOT NULL DEFAULT 0,
    closed_ts     REAL,
    closed_reason INTEGER,           -- 1 expired, 2 dismissed, 3 by app, 4 unspecified
    action_key    TEXT,
    action_ts     REAL
);
```

The database runs in WAL mode, so the viewer reads while the recorder writes.
The viewer opens it read-only (`mode=ro`) and can never corrupt the archive.

Image hints (`image-data`, `image_data`, `icon_data`) are replaced with a
`<image WxH>` placeholder before the hint dictionary is serialised — keeping raw
pixel buffers would bloat the database for no real benefit.

## Layout

`notification_history/` is an ordinary Python package:

| Module | Role |
| ------------ | ----------------------------------------------------- |
| `archive.py` | schema, `Archive` (write), `Reader` (read-only) |
| `format.py` | display helpers shared by the front-ends |
| `logger.py` | the bus monitor |
| `viewer.py` | the Qt window |
| `query.py` | JSON output consumed by the Plasma widget |

`install.sh` copies the package to `~/.local/share/notification-history/lib`
and writes three shell wrappers into `~/.local/bin` that set `PYTHONPATH` and
run `python3 -m notification_history.<module>`. That keeps one copy of the data
layer instead of duplicating queries across three standalone scripts.

Everything else is a template the installer consumes: `bin/` holds the wrapper
stubs (with `@LIBDIR@` substituted at install time) and `data/` holds the two
desktop entries and the systemd unit. There is no `src/` level — that layout
only earns its keep when a project is pip-installed, which this one is not.

## Why the tray side is a plasmoid, not a Qt tray icon

The first attempt used `QSystemTrayIcon`. It cannot behave like a widget:

- Plasma decides what a left-click does from the StatusNotifierItem
  `ItemIsMenu` property. Qt hardcodes it to `false` — verifiable on any running
  Qt tray app with
  `gdbus call --session --dest <name> --object-path /StatusNotifierItem
  --method org.freedesktop.DBus.Properties.Get org.kde.StatusNotifierItem ItemIsMenu`
  — so left-click is always routed to `Activate`, never "show the menu".
- Showing a popup panel manually is not an option either: Wayland clients
  cannot position their own toplevels, and `QSystemTrayIcon.geometry()` returns
  an empty rect under StatusNotifierItem.

A Plasma applet has none of these problems — plasmashell owns the popup and
anchors it to the icon. The cost is the data path: QML cannot open an arbitrary
SQLite file, because `LocalStorage` only reaches databases it created under its
own hashed directory. So the widget shells out to
`notification-history-query`, which prints JSON, through
`Plasma5Support.DataSource`'s executable engine. It runs only while the popup is
open, plus a 5-second refresh while it stays open.

## PATH and absolute paths

Neither plasmashell nor the systemd user manager has `~/.local/bin` on `PATH`
(it comes from `/etc/profile.d`, which only shells read):

```bash
tr '\0' '\n' < /proc/$(pgrep -x plasmashell)/environ | grep ^PATH=
systemctl --user show-environment | grep ^PATH=
```

So bare command names fail from the launcher, the widget and the unit alike.
`install.sh` substitutes `@BINDIR@` into the `.desktop` file and the widget's
`main.qml`, and the systemd unit uses `%h/.local/bin/…`.

## Single-instance viewer

The widget launches `notification-history --select <id>` on every click, so the
viewer registers `org.kde.notificationhistory.Viewer` on the session bus. A
second launch finds the name taken, calls `Present(id)` on the running window
and exits, instead of stacking up windows.

## Development

Run the recorder in the foreground to watch what it captures:

```bash
systemctl --user stop notification-logger
python3 -m notification_history.logger --verbose --db /tmp/test.db
```

In another terminal:

```bash
notify-send -u critical "Test" "Body text"
python3 -m notification_history.viewer --db /tmp/test.db
python3 -m notification_history.query --db /tmp/test.db --limit 5
```

Iterating on the widget means reinstalling it and restarting the shell:

```bash
kpackagetool6 --type Plasma/Applet --upgrade plasmoid
systemctl --user restart plasma-plasmashell
```

To check the QML without a panel:

```bash
QT_QPA_PLATFORM=offscreen tools/check-qml.py plasmoid/contents/ui
```

**Compiling `main.qml` alone proves very little.** `fullRepresentation` and
`compactRepresentation` are `QQmlComponent` properties, so their contents are
compiled *lazily* — errors inside them surface only when plasmashell
instantiates the popup on first click, and they land in the plasmashell journal
rather than anywhere obvious. That is why the popup lives in its own
`FullRepresentation.qml` taking `entries` as a property: `tools/check-qml.py`
can then instantiate it with mock data and check for runtime errors, a zero
implicit size, and whether the list actually binds.

When something misbehaves on a real panel, the applet's own messages are here:

```bash
journalctl --user -u plasma-plasmashell -b | grep notificationhistory
```

## Why compactRepresentation is defined explicitly

An applet that does not define one gets Plasma's default compact
representation, and *that* is what turns a click into `expanded = true`. Left
click did nothing in the system tray while the applet relied on the default, so
the applet now ships its own `MouseArea`. It samples `expanded` on press rather
than reading it in `onClicked`, because clicking a tray icon while its popup is
open collapses the popup first — reading the state in `onClicked` would see it
already `false` and immediately reopen.

To see the raw traffic for comparison:

```bash
dbus-monitor "interface='org.freedesktop.Notifications'" \
             "interface='org.freedesktop.impl.portal.Notification'"
```

## Things to re-check on Plasma upgrades

- Whether the portal backend interface or its version changes
- Whether [bug 486483](https://bugs.kde.org/show_bug.cgi?id=486483) finally gets
  an implementation upstream, making this redundant
