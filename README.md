# Notification History

A persistent, searchable archive of every desktop notification sent on KDE Plasma — including the ones you dismiss before reading them.

Plasma keeps its notification history in memory only. Clicking ✕ destroys the entry, and the whole list is gone when plasmashell restarts. There is no log file and no on-disk store ([KDE bug 486483](https://bugs.kde.org/show_bug.cgi?id=486483), open and untouched since May 2024). This records notifications as they pass over the session bus and keeps them in SQLite, forever.

It runs _alongside_ Plasma's notification server rather than replacing it, so popups, grouping and Do Not Disturb keep working exactly as before.

## Features

- Captures every notification, including ones dismissed instantly or never shown
- Covers both transports Plasma 6 uses — the classic `org.freedesktop.Notifications` interface **and** the XDG portal backend that sandboxed Flatpak apps go through
- Records what happened to each one: expired, dismissed by you, closed by the app, or a button activated
- Folds in-place updates (progress bars, message counters) into a single row instead of thousands
- Attributes each notification to the sending process and PID, not just its self-declared name
- Qt6 viewer with full-text search, per-app and per-urgency filters, date ranges and a detail pane
- Native Plasma system tray widget whose popup lists recent notifications inline — click one to open it in the viewer
- Runs as a `systemd --user` service, starting and stopping with your graphical session
- Plain SQLite — query it yourself, no lock-in

## Requirements

- KDE Plasma 6 (tested on 6.7.4, Wayland, Fedora 44)
- `python3-dbus`, `python3-gobject-base`
- `python3-pyside6` (only for the graphical viewer)

```bash
sudo dnf install python3-dbus python3-gobject-base python3-pyside6
```

## Installation

```bash
git clone <this repo> notification-history
cd notification-history
./install.sh
```

The installer checks dependencies, installs the package and three commands, enables the `notification-logger` user service, adds a launcher entry, installs the Plasma widget, and finally sends a test notification to confirm capture actually works. Nothing needs root.

| Option         | Effect                                       |
| -------------- | -------------------------------------------- |
| `--no-gui`     | recorder only, skip the viewer and widget    |
| `--no-service` | install files but do not enable the recorder |
| `--no-widget`  | skip the Plasma system tray widget           |
| `--uninstall`  | remove everything, keep the archive          |
| `--purge`      | with `--uninstall`, delete the archive too   |

## Usage

### System tray widget

The widget is installed but, like any Plasma applet, has to be switched on: right-click the system tray → **Configure System Tray** → **Entries** → set _Notification History_ to _Shown_.

Its popup lists recent notifications with a search field. Clicking an entry opens it in the full viewer; the viewer is single-instance, so repeated clicks reuse one window rather than piling up.

### Commands

Launch **Notification History** from your application menu, or:

```bash
notification-history            # open the viewer
notification-history --select 42  # open with notification 42 selected
notification-history-query -n 20  # recent notifications as JSON
notification-logger --stats     # entry count, date range, top senders
journalctl --user -u notification-logger -f   # watch the recorder
```

> **Note:** `~/.local/bin` is not on the PATH that plasmashell and the systemd user manager use, so the launcher, the widget and the service all refer to these commands by absolute path. `install.sh` substitutes the real path when it installs them.

The archive lives at `~/.local/share/notification-history/notifications.db` and is a normal SQLite file:

```bash
sqlite3 ~/.local/share/notification-history/notifications.db \
  "SELECT datetime(ts,'unixepoch','localtime'), app_name, summary
     FROM notifications ORDER BY ts DESC LIMIT 20;"
```

Roughly 300 bytes per notification — years of history stay well under 50 MB.

## How it works

The recorder asks the bus daemon for `BecomeMonitor` (the same mechanism `dbus-monitor` uses, no privileges required) with match rules for:

| Interface                                  | Carries                           |
| ------------------------------------------ | --------------------------------- |
| `org.freedesktop.Notifications`            | notifications from ordinary apps  |
| `org.freedesktop.impl.portal.Notification` | notifications from sandboxed apps |

The second one matters more than it looks. Plasma 6.7 made plasmashell its own XDG portal backend, so Flatpak notifications reach it through `AddNotification` and **never touch** the classic interface. A recorder watching only `org.freedesktop.Notifications` silently misses every Flatpak app.

Because the recorder only observes, it can never break notification delivery: if it crashes, notifications still appear normally — they just stop being archived until systemd restarts it.

See [docs/TECHNICAL.md](docs/TECHNICAL.md) for the database schema and message flow.

## Limitations

- **Only records from install time onward.** Notifications already dismissed are unrecoverable, and anything sent while the service is stopped is missed.
- **Plasma-internal notifications are invisible.** `libnotificationmanager` exposes a C++ `Server::add()` that lets plasmashell inject entries into its own model with no bus traffic — for example the Do Not Disturb summary. No bus monitor can see these. Plasma itself keeps them out of its history.
- **KIO job progress is not captured.** File transfer progress arrives over `org.kde.JobViewServer`, a separate interface, and is deliberately out of scope.
- Embedded notification images are noted but not stored; only their dimensions are recorded.

## Uninstall

```bash
./install.sh --uninstall          # keeps the archive
./install.sh --uninstall --purge  # deletes it too
```

## License

MIT — see [LICENSE](LICENSE).

## AI Honesty

This project was completely generated by LLMs.

<a href="https://www.aihonestybadge.com" target="_blank" rel="noopener"><img src="https://www.aihonestybadge.com/badges/ai-generated.svg" alt="AI Generated Badge" style="max-width: 190px; height: auto;" /></a>
