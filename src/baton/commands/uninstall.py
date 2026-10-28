"""`baton uninstall`: remove the hooks, and with --purge or --all, Baton's files. --all
also removes the PATH entry the installer added and the baton command itself. On Windows
the command is removed just after this process exits, since Windows won't delete a
running program."""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from pathlib import Path

from .. import package, system, ui
from ..claude import settings
from ..config import config_dir, old_config_dir, state_dir


def data_paths() -> list:
    """Baton's own config and state folders, plus claude-handoff's if they're still there."""
    out = []
    for p in (config_dir(), state_dir(), old_config_dir(), system.state_home() / "claude-handoff"):
        if p not in out:
            out.append(p)
    return out


def purge(paths: list) -> list:
    """Delete the given files and folders that exist. Returns what was removed."""
    removed = []
    for p in paths:
        if p.is_dir() and not p.is_symlink():
            shutil.rmtree(p)
        elif p.exists() or p.is_symlink():
            p.unlink()
        else:
            continue
        removed.append(str(p))
    return removed


def run(args) -> int:
    remove = package.removal_command()
    remove_text = " ".join(shlex.quote(x) for x in remove)

    path = settings.uninstall()
    print("Removed Baton's hooks from %s" % path)
    if args.purge or args.all:
        rc = _purge(path, args.yes)
        if rc is not None:
            return rc
    else:
        print("Config and keys are still in ~/.config/baton. `baton uninstall --purge` deletes them.")

    if not remove:
        return 0
    if not args.all:
        print("To remove the baton command too: %s" % remove_text)
        print("Or run `baton uninstall --all` to do everything in one step.")
        return 0
    program = shutil.which(remove[0]) or package.tool(remove[0])
    if not program:
        _remove_path_lines()
        print("Last step, remove the baton command: %s" % remove_text)
        return 0
    if not args.yes and not ui.confirm("Remove the baton command now (%s)?" % remove_text, True):
        print("Kept it. To remove it later: %s" % remove_text)
        return 0
    _remove_path_lines()
    if system.windows():
        system.run_after_exit([[program] + remove[1:]])
        print("Removing the baton command in a few seconds: %s" % remove_text)
        return 0
    print("Running: %s" % remove_text)
    sys.stdout.flush()
    sys.stderr.flush()
    os.execvp(remove[0], remove)  # replaces this process, so baton can remove itself
    return 0


def _remove_path_lines() -> None:
    for f in package.remove_path_lines():
        print("Removed the PATH entry Baton added in %s" % f)


def _purge(settings_file: Path, yes: bool) -> int | None:
    """Delete config, keys, logs and backups. Returns an exit code to stop with."""
    targets = [q for q in data_paths() if q.exists()] + settings.backups(settings_file)
    if not targets:
        print("No Baton config or logs to delete.")
        return None
    print("This deletes:")
    for q in targets:
        print("  %s" % q)
    if not yes:
        if not ui.interactive():
            print("Pass --yes to delete them without a prompt.", file=sys.stderr)
            return 1
        if not ui.confirm("Delete these, including any API keys in them?", False):
            print("Kept them.")
            return 0
    for q in purge(targets):
        print("Deleted %s" % q)
    return None
