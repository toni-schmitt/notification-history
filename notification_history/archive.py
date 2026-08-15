"""Storage layer for the notification archive.

The recorder writes through :class:`Archive`; the viewer and widget read through
:class:`Reader`, which opens the same database read-only. WAL mode lets both
happen at once.
"""

import os
import sqlite3
import time
from datetime import datetime, timedelta

SCHEMA_VERSION = 1

# Reason codes from org.freedesktop.Notifications.NotificationClosed.
REASON_EXPIRED = 1
REASON_DISMISSED = 2
REASON_CLOSED = 3
REASON_UNSPECIFIED = 4

CLOSE_REASONS = {
    REASON_EXPIRED: "expired",
    REASON_DISMISSED: "dismissed",
    REASON_CLOSED: "closed by app",
    REASON_UNSPECIFIED: "unspecified",
}

URGENCY_NAMES = {0: "Low", 1: "Normal", 2: "Critical"}

# App-name expression used consistently for display, filtering and grouping.
APP_EXPR = "COALESCE(NULLIF(app_name, ''), process, '(unknown)')"

SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
    id            INTEGER PRIMARY KEY,
    source        TEXT    NOT NULL,
    ts            REAL    NOT NULL,
    ts_updated    REAL,
    app_name      TEXT,
    desktop_entry TEXT,
    summary       TEXT,
    body          TEXT,
    icon          TEXT,
    urgency       INTEGER,
    category      TEXT,
    actions       TEXT,
    hints         TEXT,
    timeout       INTEGER,
    srv_id        TEXT,
    sender        TEXT,
    pid           INTEGER,
    process       TEXT,
    updates       INTEGER NOT NULL DEFAULT 0,
    closed_ts     REAL,
    closed_reason INTEGER,
    action_key    TEXT,
    action_ts     REAL
);
CREATE INDEX IF NOT EXISTS idx_notifications_ts  ON notifications(ts);
CREATE INDEX IF NOT EXISTS idx_notifications_app ON notifications(app_name);
CREATE INDEX IF NOT EXISTS idx_notifications_srv ON notifications(source, srv_id);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def default_db_path():
    """Location of the archive, honouring XDG_DATA_HOME."""
    data_home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return os.path.join(data_home, "notification-history", "notifications.db")


class Archive:
    """Write side: used by the recorder."""

    COLUMNS = (
        "source", "ts", "app_name", "desktop_entry", "summary", "body", "icon",
        "urgency", "category", "actions", "hints", "timeout", "srv_id",
        "sender", "pid", "process",
    )

    def __init__(self, path):
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.executescript(SCHEMA)
        self.db.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )

    def insert(self, record):
        record = dict(record)
        record.setdefault("ts", time.time())
        columns = [name for name in self.COLUMNS if name in record]
        placeholders = ", ".join("?" * len(columns))
        cursor = self.db.execute(
            f"INSERT INTO notifications ({', '.join(columns)}) VALUES ({placeholders})",
            [record[name] for name in columns],
        )
        return cursor.lastrowid

    def update_in_place(self, source, srv_id, record):
        """Fold a replacement (progress bars, counters) into the original row."""
        row = self.db.execute(
            "SELECT id FROM notifications WHERE source = ? AND srv_id = ? "
            "AND closed_ts IS NULL ORDER BY id DESC LIMIT 1",
            (source, srv_id),
        ).fetchone()
        if row is None:
            return None
        self.db.execute(
            "UPDATE notifications SET ts_updated = ?, summary = ?, body = ?, icon = ?, "
            "urgency = ?, category = ?, actions = ?, hints = ?, updates = updates + 1 "
            "WHERE id = ?",
            (
                time.time(), record["summary"], record["body"], record["icon"],
                record["urgency"], record.get("category"), record["actions"],
                record["hints"], row[0],
            ),
        )
        return row[0]

    def set_server_id(self, rowid, srv_id):
        self.db.execute("UPDATE notifications SET srv_id = ? WHERE id = ?", (srv_id, rowid))

    def close(self, source, srv_id, reason):
        self.db.execute(
            "UPDATE notifications SET closed_ts = ?, closed_reason = ? WHERE id = "
            "(SELECT id FROM notifications WHERE source = ? AND srv_id = ? "
            " AND closed_ts IS NULL ORDER BY id DESC LIMIT 1)",
            (time.time(), reason, source, srv_id),
        )

    def record_action(self, source, srv_id, action_key):
        self.db.execute(
            "UPDATE notifications SET action_key = ?, action_ts = ? WHERE id = "
            "(SELECT id FROM notifications WHERE source = ? AND srv_id = ? "
            " ORDER BY id DESC LIMIT 1)",
            (action_key, time.time(), source, srv_id),
        )


class Reader:
    """Read side: used by the viewer and the query command.

    Opening in ``mode=ro`` means a buggy front-end can never damage the archive.
    """

    def __init__(self, path=None):
        self.path = path or default_db_path()
        self.db = None
        self.connect()

    def connect(self):
        if self.db is not None:
            return True
        if not os.path.exists(self.path):
            return False
        self.db = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True,
                                  check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        return True

    @property
    def available(self):
        return self.db is not None or self.connect()

    def fingerprint(self):
        """Cheap change detector — avoids reloading the model when nothing moved."""
        if not self.available:
            return None
        return tuple(self.db.execute(
            "SELECT COUNT(*), MAX(id), MAX(COALESCE(ts_updated, ts)), "
            "MAX(COALESCE(closed_ts, 0)) FROM notifications").fetchone())

    def apps(self):
        if not self.available:
            return []
        return [row[0] for row in self.db.execute(
            f"SELECT DISTINCT {APP_EXPR} AS app FROM notifications "
            "ORDER BY app COLLATE NOCASE")]

    def fetch(self, search=None, app=None, days=None, since=None, urgency=None, limit=200):
        """Query the archive. ``days`` counts back from now; ``since`` is a timestamp."""
        if not self.available:
            return []
        clauses, params = [], []
        if search:
            clauses.append(
                "(summary LIKE ? OR body LIKE ? OR app_name LIKE ? OR process LIKE ?)")
            params += [f"%{search}%"] * 4
        if app:
            clauses.append(f"{APP_EXPR} = ?")
            params.append(app)
        if since is not None:
            clauses.append("ts >= ?")
            params.append(since)
        elif days is not None:
            clauses.append("ts >= ?")
            params.append((datetime.now() - timedelta(days=days)).timestamp())
        if urgency is not None:
            clauses.append("urgency = ?")
            params.append(urgency)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.db.execute(
            f"SELECT *, {APP_EXPR} AS app FROM notifications {where} "
            f"ORDER BY ts DESC LIMIT {int(limit)}", params).fetchall()

    def counts(self):
        """(total, today) — used for the widget tooltip."""
        if not self.available:
            return 0, 0
        total = self.db.execute("SELECT COUNT(*) FROM notifications").fetchone()[0]
        midnight = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        today = self.db.execute(
            "SELECT COUNT(*) FROM notifications WHERE ts >= ?",
            (midnight.timestamp(),)).fetchone()[0]
        return total, today

    def size(self):
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    def stats(self):
        total, first, last = self.db.execute(
            "SELECT COUNT(*), MIN(ts), MAX(ts) FROM notifications").fetchone()
        per_app = self.db.execute(
            f"SELECT {APP_EXPR} AS app, COUNT(*) AS n FROM notifications "
            "GROUP BY app ORDER BY n DESC LIMIT 15").fetchall()
        return total, first, last, per_app
