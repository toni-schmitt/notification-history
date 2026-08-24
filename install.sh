#!/usr/bin/env bash
#
# Install (or remove) the notification recorder, viewer and Plasma widget for
# the current user. Nothing here needs root — everything lands under $HOME.
#
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BIN_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
LIB_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/notification-history/lib"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/notification-history"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"

SERVICE="notification-logger.service"
DESKTOP_FILE="notification-history.desktop"
PLASMOID_ID="local.notificationhistory"
WIDGET_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/plasma/plasmoids/$PLASMOID_ID"

WITH_GUI=1
WITH_SERVICE=1
WITH_WIDGET=1
ACTION="install"
PURGE=0

if [[ -t 1 ]]; then
  BOLD=$'\e[1m'; DIM=$'\e[2m'; RED=$'\e[31m'; GREEN=$'\e[32m'; YELLOW=$'\e[33m'; RESET=$'\e[0m'
else
  BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; RESET=""
fi

info() { printf '%s\n' "$*"; }
step() { printf '%s==>%s %s\n' "$BOLD" "$RESET" "$*"; }
ok()   { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$*"; }
warn() { printf '  %s!%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
fail() { printf '  %sx%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }

usage() {
  cat <<EOF
Usage: ./install.sh [options]

  --uninstall      remove everything (keeps the archive)
  --purge          with --uninstall, also delete the archive database
  --no-gui         install the recorder only, without viewer or widget
  --no-service     install the files but do not enable the recorder service
  --no-widget      skip the Plasma system tray widget
  -h, --help       show this help

Install locations:
  commands   $BIN_DIR
  package    $LIB_DIR
  service    $UNIT_DIR
  launcher   $DESKTOP_DIR
  widget     $WIDGET_DIR
  archive    $DATA_DIR
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --uninstall)    ACTION="uninstall" ;;
    --purge)        PURGE=1 ;;
    --no-gui)       WITH_GUI=0; WITH_WIDGET=0 ;;
    --no-service)   WITH_SERVICE=0 ;;
    --no-widget)    WITH_WIDGET=0 ;;
    -h|--help)      usage; exit 0 ;;
    *)              fail "unknown option: $1 (try --help)" ;;
  esac
  shift
done

# --------------------------------------------------------------------- checks

check_dependencies() {
  step "Checking dependencies"
  command -v python3 >/dev/null || fail "python3 not found"
  ok "python3 $(python3 --version | cut -d' ' -f2)"

  local missing=()
  python3 -c "import dbus" 2>/dev/null && ok "python3-dbus" || missing+=("python3-dbus")
  python3 -c "import gi" 2>/dev/null && ok "python3-gobject" || missing+=("python3-gobject-base")
  if [[ $WITH_GUI -eq 1 ]]; then
    python3 -c "import PySide6.QtWidgets" 2>/dev/null && ok "PySide6" || missing+=("python3-pyside6")
  fi

  if [[ ${#missing[@]} -gt 0 ]]; then
    warn "missing Python modules"
    info ""
    info "  Install them with:"
    info "    ${BOLD}sudo dnf install ${missing[*]}${RESET}"
    info ""
    exit 1
  fi

  if ! command -v systemctl >/dev/null; then
    warn "systemctl not found — the recorder service will not be enabled"
    WITH_SERVICE=0
  fi
}

check_notification_server() {
  step "Checking the notification server"
  local server
  if server="$(gdbus call --session \
      --dest org.freedesktop.Notifications \
      --object-path /org/freedesktop/Notifications \
      --method org.freedesktop.Notifications.GetServerInformation 2>/dev/null)"; then
    ok "running: ${server}"
  else
    warn "no notification server responded — the recorder still installs fine"
  fi
}

# Write a wrapper from bin/, pointing it at the installed package.
install_wrapper() {
  local name="$1"
  sed "s|@LIBDIR@|$LIB_DIR|g" "$REPO_DIR/bin/$name" > "$BIN_DIR/$name"
  chmod 755 "$BIN_DIR/$name"
  ok "$BIN_DIR/$name"
}

# -------------------------------------------------------------------- install

do_install() {
  check_dependencies
  check_notification_server

  step "Installing the package"
  rm -rf "${LIB_DIR:?}/notification_history"
  mkdir -p "$LIB_DIR"
  cp -r "$REPO_DIR/notification_history" "$LIB_DIR/"
  find "$LIB_DIR/notification_history" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
  ok "$LIB_DIR/notification_history"

  step "Installing commands"
  mkdir -p "$BIN_DIR"
  install_wrapper notification-logger
  if [[ $WITH_GUI -eq 1 ]]; then
    install_wrapper notification-history
    install_wrapper notification-history-query

    # The launcher must use an absolute path: neither plasmashell nor the
    # systemd user manager has ~/.local/bin on PATH, so a bare command fails.
    mkdir -p "$DESKTOP_DIR"
    sed "s|@BINDIR@|$BIN_DIR|g" "$REPO_DIR/data/$DESKTOP_FILE" > "$DESKTOP_DIR/$DESKTOP_FILE"
    chmod 644 "$DESKTOP_DIR/$DESKTOP_FILE"
    ok "$DESKTOP_DIR/$DESKTOP_FILE"
    command -v update-desktop-database >/dev/null && \
      update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
  fi

  mkdir -p "$DATA_DIR"

  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not on your PATH — add it to use the commands directly" ;;
  esac

  if [[ $WITH_SERVICE -eq 1 ]]; then
    step "Enabling the recorder service"
    mkdir -p "$UNIT_DIR"
    install -m 644 "$REPO_DIR/data/$SERVICE" "$UNIT_DIR/$SERVICE"
    systemctl --user daemon-reload
    systemctl --user enable --now "$SERVICE"
    sleep 1
    if systemctl --user is-active --quiet "$SERVICE"; then
      ok "$SERVICE is active and recording"
    else
      warn "$SERVICE did not start — check: journalctl --user -u $SERVICE -n 30"
    fi
    verify
  else
    step "Skipping the recorder service (--no-service)"
    info "  Start it by hand with: ${BOLD}notification-logger${RESET}"
  fi

  if [[ $WITH_WIDGET -eq 1 ]]; then
    install_widget
  fi

  step "Done"
  info "  Archive : $DATA_DIR/notifications.db"
  if [[ $WITH_GUI -eq 1 ]]; then
    info "  Viewer  : ${BOLD}notification-history${RESET} (also in your application launcher)"
    info "  Query   : ${BOLD}notification-history-query --limit 20${RESET} (JSON)"
  fi
  info "  Stats   : ${BOLD}notification-logger --stats${RESET}"
  info "  Logs    : ${BOLD}journalctl --user -u $SERVICE -f${RESET}"
  info ""
  info "  ${DIM}Only notifications sent from now on are recorded.${RESET}"
  if [[ $WITH_WIDGET -eq 1 ]]; then
    info ""
    info "  ${BOLD}To add the widget:${RESET} right-click the system tray → Configure System Tray"
    info "  → Entries → set ${BOLD}Notification History${RESET} to ${BOLD}Shown${RESET}."
  fi
}

install_widget() {
  step "Installing the Plasma widget"
  if ! command -v kpackagetool6 >/dev/null; then
    warn "kpackagetool6 not found — skipping the widget"
    return 0
  fi

  local staged mode verb status
  # Stage a copy with the real bin path baked in: plasmashell runs the helper
  # commands with its own PATH, which does not include ~/.local/bin.
  staged="$(mktemp -d)"
  cp -r "$REPO_DIR/plasmoid/." "$staged/"
  sed -i "s|@BINDIR@|$BIN_DIR|g" "$staged/contents/ui/main.qml"

  if [[ -d "$WIDGET_DIR" ]]; then
    mode="--upgrade"; verb="upgraded"
  else
    mode="--install"; verb="installed"
  fi

  status=0
  kpackagetool6 --type Plasma/Applet "$mode" "$staged" >/dev/null 2>&1 || status=$?
  rm -rf "$staged"

  if [[ $status -ne 0 ]]; then
    warn "kpackagetool6 $mode failed — run it manually to see why:"
    info "    kpackagetool6 --type Plasma/Applet $mode $REPO_DIR/plasmoid"
    return 0
  fi
  ok "$PLASMOID_ID $verb"

  if [[ "$verb" == "upgraded" ]] && pgrep -x plasmashell >/dev/null 2>&1; then
    info "  ${DIM}Restart plasmashell to pick up the new version:"
    info "    systemctl --user restart plasma-plasmashell${RESET}"
  fi
}

# Send one notification through the real server and confirm it was archived.
verify() {
  command -v notify-send >/dev/null || return 0
  step "Verifying capture"
  local marker="notification-history install check $$"
  notify-send --app-name="notification-history" "Notification History" "$marker" || return 0
  local found=""
  for _ in 1 2 3 4 5 6; do
    sleep 0.5
    found="$(python3 - "$DATA_DIR/notifications.db" "$marker" <<'PY'
import sqlite3, sys
try:
    db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
    row = db.execute("SELECT 1 FROM notifications WHERE body = ?", (sys.argv[2],)).fetchone()
    print("yes" if row else "")
except sqlite3.Error:
    print("")
PY
)"
    [[ -n "$found" ]] && break
  done
  if [[ -n "$found" ]]; then
    ok "test notification was captured"
  else
    warn "test notification was not captured — check: journalctl --user -u $SERVICE -n 30"
  fi
}

# ------------------------------------------------------------------ uninstall

do_uninstall() {
  step "Stopping services"
  if command -v systemctl >/dev/null; then
    systemctl --user disable --now "$SERVICE" 2>/dev/null || true
    rm -f "$UNIT_DIR/$SERVICE"
    systemctl --user daemon-reload 2>/dev/null || true
    ok "$SERVICE removed"
  fi

  if command -v kpackagetool6 >/dev/null; then
    step "Removing the Plasma widget"
    if kpackagetool6 --type Plasma/Applet --remove "$PLASMOID_ID" >/dev/null 2>&1; then
      ok "$PLASMOID_ID removed"
    else
      info "  ${DIM}widget was not installed${RESET}"
    fi
  fi

  step "Removing files"
  rm -f "$BIN_DIR/notification-logger" "$BIN_DIR/notification-history" \
        "$BIN_DIR/notification-history-query"
  rm -f "$DESKTOP_DIR/$DESKTOP_FILE"
  rm -rf "${LIB_DIR:?}/notification_history"
  ok "commands, package and launchers removed"

  if [[ $PURGE -eq 1 ]]; then
    rm -rf "$DATA_DIR"
    ok "archive deleted ($DATA_DIR)"
  else
    info "  ${DIM}Archive kept at $DATA_DIR — delete it yourself, or re-run with --purge${RESET}"
  fi

  step "Done"
}

case "$ACTION" in
  install)   do_install ;;
  uninstall) do_uninstall ;;
esac
