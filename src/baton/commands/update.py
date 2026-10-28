"""`baton update`: check main for a newer Baton and install it now.

`--check` only reports. `--auto` is the background run hooks start once an update is
due (see baton.update); it prints nothing.
"""

from __future__ import annotations

import argparse
import sys

from .. import package, system, ui, update


def add_arguments(p) -> None:
    p.add_argument("--check", action="store_true", help="Only say whether there's a newer version")
    p.add_argument("--auto", action="store_true", help=argparse.SUPPRESS)


def run(args) -> int:
    if args.auto:
        return _auto()
    if not args.check and not package.upgrade_command():
        print("This copy of Baton wasn't installed with pipx or uv, so it can't update itself.\n"
              "Reinstall with: " + package.install_command(), file=sys.stderr)
        return 1
    try:
        result = update.update(check_only=args.check)
    except update.Busy:
        print("An update is already running.", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(ui.red("Update failed: %s: %s" % (type(e).__name__, e)), file=sys.stderr)
        return 1
    current, latest = result["current"], result["latest"]
    if result["upgraded"] and system.windows():
        print(ui.green("Updating") + " Baton %s -> %s. It finishes a few seconds after this "
              "command exits; then start a new Claude Code session." % (current, latest))
    elif result["upgraded"]:
        print(ui.green("Updated") + " Baton %s -> %s. Start a new Claude Code session to use it."
              % (current, latest))
    elif update.is_newer(latest, current):
        print("Baton %s is available (this is %s). Run `baton update` to install it."
              % (latest, current))
    else:
        print("Baton %s is up to date." % current)
    return 0


def _auto() -> int:
    """Background run: silent, and quiet about another run holding the lock."""
    try:
        update.update()
    except Exception:  # noqa: BLE001  (already logged; Busy needs no log)
        pass
    return 0
