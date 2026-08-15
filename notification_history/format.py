"""Display helpers shared by the viewer and the query command."""

import html
from datetime import datetime

from .archive import CLOSE_REASONS


def format_time(stamp):
    when = datetime.fromtimestamp(stamp)
    if when.date() == datetime.now().date():
        return when.strftime("%H:%M:%S")
    return when.strftime("%Y-%m-%d %H:%M")


def format_relative(stamp):
    """Short, widget-sized age: 'now', '4m', '3h', 'Tue', '12 Mar'."""
    delta = datetime.now() - datetime.fromtimestamp(stamp)
    seconds = delta.total_seconds()
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    if seconds < 7 * 86400:
        return datetime.fromtimestamp(stamp).strftime("%a")
    return datetime.fromtimestamp(stamp).strftime("%d %b")


def strip_markup(text):
    """Notification bodies allow a little HTML — flatten it for list display."""
    out, depth = [], 0
    for char in text or "":
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(char)
    return html.unescape("".join(out)).replace("\n", " ").strip()


def status_text(row):
    if row["closed_reason"]:
        text = CLOSE_REASONS.get(row["closed_reason"], "closed")
    elif row["closed_ts"]:
        text = "closed"
    else:
        text = "—"
    if row["action_key"]:
        text += " · activated"
    if row["updates"]:
        text += f" · {row['updates']}× updated"
    return text
