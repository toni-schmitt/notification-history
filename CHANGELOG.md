# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Recorder capturing every notification on the session bus into SQLite, covering
  both `org.freedesktop.Notifications` and the `org.freedesktop.impl.portal.Notification`
  backend that Plasma 6.7 registers for sandboxed applications
- Close reasons (expired, dismissed, closed by app) and activated actions recorded
  per notification
- In-place replacements folded into the originating row instead of inserting
  duplicates
- Qt6 viewer with search, per-application, date-range and urgency filters, a
  detail pane and JSON export
- Native Plasma system tray widget listing recent notifications in its popup
- `notification-history-query` emitting JSON for the widget to consume
- Single-instance viewer over D-Bus, so repeated widget clicks reuse one window
- `systemd --user` service for the recorder
- `install.sh` with dependency checks, capture verification and `--uninstall`

### Notes

- The launcher, widget and service all use absolute paths: `~/.local/bin` is
  absent from the PATH that plasmashell and the systemd user manager see.
- A `QSystemTrayIcon` applet was tried first and dropped — Qt hardcodes the
  StatusNotifierItem `ItemIsMenu` property to `false`, so Plasma can never be
  made to open its list on left-click.
- The widget must not set `preferredRepresentation`. The System Tray's
  `setActiveApplet()` guards every branch on `!applet.preferredRepresentation`,
  so setting it — even to `compactRepresentation` — makes the tray skip
  assigning `activeApplet` and clear its popup stack, yielding a blank popup
  with no errors logged.
