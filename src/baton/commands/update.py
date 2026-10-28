"""`baton update`: reinstall Baton from main with whatever installed it."""

from __future__ import annotations

import subprocess
import sys

from .. import __version__, package, ui


def add_arguments(p) -> None:
    return None


def run(args) -> int:
    cmd = package.upgrade_command()
    if not cmd:
        print("This copy of Baton wasn't installed with pipx or uv, so it can't update "
              "itself.\nReinstall with: " + package.install_command(), file=sys.stderr)
        return 1
    print("Updating Baton %s from %s" % (__version__, package.BRANCH))
    env = {"PATH": package.search_path()}
    p = subprocess.run(cmd, env={**__import__("os").environ, **env},
                       capture_output=True, text=True)
    if p.returncode != 0:
        print(ui.red("Update failed:") + " " + (p.stderr or p.stdout).strip()[:500],
              file=sys.stderr)
        return 1
    print(ui.green("Updated") + ". The next prompt uses it.")
    return 0
