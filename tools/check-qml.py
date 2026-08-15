#!/usr/bin/env python3
"""Validate the plasmoid QML without a running panel.

`fullRepresentation` is a lazily compiled Component, so loading main.qml alone
proves almost nothing about the popup — errors inside it only surface when
plasmashell instantiates it on first click. FullRepresentation.qml is a separate
file precisely so it can be *instantiated* here, with mock data.

The popup is then given a concrete size, the way plasmashell sizes it, and the
laid-out geometry is inspected. Checking only the implicit size is not enough:
a popup whose content collapsed to zero height still reports a plausible
implicit size from its header and footer alone, and renders as a blank panel.

Usage: tools/check-qml.py [plasmoid/contents/ui]
"""

import sys
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuick import QQuickItem

# The size plasmashell gives a system tray popup, roughly.
POPUP_WIDTH = 460
POPUP_HEIGHT = 520

MOCK_ENTRIES = [
    {"id": 1, "app": "Discord", "summary": "New message", "body": "hello there",
     "urgency": 1, "when": "2m", "time": "12:00:00", "status": "dismissed", "updates": 0},
    {"id": 2, "app": "Battery", "summary": "Battery critical", "body": "5% remaining",
     "urgency": 2, "when": "1h", "time": "11:00:00", "status": "", "updates": 0},
    {"id": 3, "app": "Dolphin", "summary": "Copying files", "body": "100%",
     "urgency": 0, "when": "3h", "time": "09:00:00", "status": "closed by app", "updates": 12},
]

failures = []


def check(condition, label, detail=""):
    if condition:
        print(f"  ✓ {label}")
    else:
        print(f"  ✗ {label}{'  — ' + detail if detail else ''}")
        failures.append(label)
    return condition


def report_errors(label, errors):
    if errors:
        print(f"  ✗ {label}")
        for error in errors:
            print(f"      {error.line()}:{error.column()}  {error.description()}")
        failures.append(label)
        return False
    print(f"  ✓ {label}")
    return True


def walk(item, depth=0):
    """Depth-first walk over the QQuickItem tree."""
    yield item, depth
    for child in item.childItems():
        yield from walk(child, depth + 1)


def find_type(root, needle):
    for item, _ in walk(root):
        if needle in item.metaObject().className():
            return item
    return None


def check_compiles(engine, path):
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(path)))
    return report_errors(f"{path.name} compiles", component.errors()), component


def check_popup(engine, path):
    """Compile, instantiate and lay out the popup — the part plasmashell defers."""
    ok, component = check_compiles(engine, path)
    if not ok:
        return

    obj = component.createWithInitialProperties({"entries": MOCK_ENTRIES})
    if not report_errors(f"{path.name} instantiates", component.errors()):
        return
    if not check(isinstance(obj, QQuickItem), f"{path.name} is a visual item"):
        return

    # Size it the way plasmashell would, then let bindings settle.
    obj.setWidth(POPUP_WIDTH)
    obj.setHeight(POPUP_HEIGHT)
    QGuiApplication.processEvents()
    obj.polish()
    QGuiApplication.processEvents()

    entries = obj.property("visibleEntries")
    check(entries is not None and len(entries) == len(MOCK_ENTRIES),
          f"{len(MOCK_ENTRIES)} entries bound",
          f"got {0 if entries is None else len(entries)}")

    listview = find_type(obj, "ListView")
    if not check(listview is not None, "popup contains a ListView"):
        return

    width, height = listview.width(), listview.height()
    check(width > 0 and height > 0,
          f"list is laid out ({width:.0f}x{height:.0f})",
          "zero size renders as a blank popup")

    count = listview.property("count")
    check(count == len(MOCK_ENTRIES), f"list shows {len(MOCK_ENTRIES)} rows",
          f"count={count}")

    # The content area must occupy most of the popup: if it collapses, the
    # header and footer alone still yield a believable implicit size.
    if height > 0:
        share = height / POPUP_HEIGHT
        check(share > 0.5, f"list fills the popup ({share:.0%} of height)",
              "content collapsed — header/footer only")


def main():
    ui_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "plasmoid/contents/ui")
    if not ui_dir.is_dir():
        print(f"no such directory: {ui_dir}")
        return 2

    app = QGuiApplication(sys.argv[:1])  # noqa: F841 - required for QML types
    engine = QQmlEngine()
    engine.addImportPath(str(ui_dir))

    print(f"checking {ui_dir}")
    check_popup(engine, ui_dir / "FullRepresentation.qml")
    # main.qml only gets a compile check: PlasmoidItem needs a real applet
    # context, so instantiating it outside plasmashell proves nothing.
    check_compiles(engine, ui_dir / "main.qml")

    print("FAILED" if failures else "OK")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
