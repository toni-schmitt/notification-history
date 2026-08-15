"""JSON query command feeding the Plasma widget.

Kept free of Qt imports on purpose — the widget runs this on every refresh, so
start-up cost matters.
"""

import argparse
import json
import sys

from .archive import CLOSE_REASONS, Reader, default_db_path
from .format import format_relative, format_time, strip_markup

FIELDS = ("id", "ts", "app", "summary", "body", "urgency", "updates",
          "closed_reason", "source", "process")


def to_entry(row):
    """Flatten a row into what the widget needs, pre-formatted."""
    return {
        "id": row["id"],
        "app": row["app"],
        "summary": strip_markup(row["summary"]) or strip_markup(row["body"]),
        "body": strip_markup(row["body"]),
        "urgency": row["urgency"],
        "when": format_relative(row["ts"]),
        "time": format_time(row["ts"]),
        "status": CLOSE_REASONS.get(row["closed_reason"], ""),
        "updates": row["updates"],
    }


def main():
    parser = argparse.ArgumentParser(
        prog="notification-history-query",
        description="Print recent notifications as JSON.")
    parser.add_argument("--db", default=default_db_path(), help="archive path")
    parser.add_argument("-n", "--limit", type=int, default=50,
                        help="maximum entries to return (default: 50)")
    parser.add_argument("-s", "--search", help="filter on summary, body or application")
    parser.add_argument("--app", help="restrict to one application")
    parser.add_argument("--days", type=int, help="only the last N days")
    parser.add_argument("--raw", action="store_true",
                        help="emit full database rows instead of widget fields")
    options = parser.parse_args()

    reader = Reader(options.db)
    if not reader.available:
        json.dump([], sys.stdout)
        return 0

    rows = reader.fetch(search=options.search, app=options.app, days=options.days,
                        limit=options.limit)
    entries = [dict(row) for row in rows] if options.raw else [to_entry(r) for r in rows]
    json.dump(entries, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
