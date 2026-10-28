"""Keeping Baton current: compare this version with the one on main and, when main is
newer, reinstall with the tool that installed Baton.

Hooks call start_if_due() as they finish. At most once a day it starts
`baton update --auto` as a detached process, so a check never slows a hook and never
prints to Claude Code. The check time is recorded before the process starts, so a
failing update waits a day instead of retrying on every hook. A lock
file keeps two updates from running at once. Outcomes go to handoff.log and update.json
in ~/.local/state/baton.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from contextlib import contextmanager

from . import __version__, package
from .claude import settings
from .config import state_dir
from .state import log

VERSION_URL = ("https://raw.githubusercontent.com/bradley-t-t/baton/%s/src/baton/__init__.py"
               % package.BRANCH)
INTERVAL_HOURS = 24
CHECK_TIMEOUT = 10       # seconds to read the version on main
UPGRADE_TIMEOUT = 600    # seconds for pipx or uv to reinstall
STATE_FILE = "update.json"
LOCK_FILE = "update.lock"


class Busy(Exception):
    """Another update is already running."""


def parse(version: str) -> tuple:
    """"0.10.2" -> (0, 10, 2). Anything after the numbers is ignored."""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", version or "")
    if not m:
        raise ValueError("not a version: %r" % version)
    return tuple(int(x) for x in m.group(1).split("."))


def is_newer(latest: str, current: str = __version__) -> bool:
    a, b = parse(latest), parse(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def latest_version(timeout: float = CHECK_TIMEOUT) -> str:
    """The version on main, read from its __init__.py."""
    req = urllib.request.Request(VERSION_URL, headers={"User-Agent": "baton/" + __version__})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read(65536).decode("utf-8", "replace")
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    if not m:
        raise ValueError("no version in %s" % VERSION_URL)
    parse(m.group(1))
    return m.group(1)


def last() -> dict:
    """What the last check recorded: checked_at, latest, outcome."""
    try:
        data = json.loads((state_dir() / STATE_FILE).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _record(**fields) -> None:
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (STATE_FILE + ".tmp")
    tmp.write_text(json.dumps(dict(last(), **fields), indent=2))
    os.replace(tmp, d / STATE_FILE)


def due(now: float | None = None) -> bool:
    """Installed with pipx or uv, and the last check is a day old."""
    if not package.installer():
        return False
    now = time.time() if now is None else now
    try:
        checked = float(last().get("checked_at") or 0)
    except (TypeError, ValueError):
        checked = 0.0
    return now - checked >= INTERVAL_HOURS * 3600


def start_if_due() -> bool:
    """Start a background update when one is due. Never raises; True when started."""
    try:
        if not due():
            return False
        _record(checked_at=time.time(), outcome="checking")
        subprocess.Popen([sys.executable, "-m", "baton", "update", "--auto"],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, cwd=str(state_dir()),
                         start_new_session=True, close_fds=True)
        return True
    except Exception as e:  # noqa: BLE001
        log("update: couldn't start a check: %s: %s" % (type(e).__name__, e))
        return False


@contextmanager
def _lock():
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    with open(d / LOCK_FILE, "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise Busy("another update is running") from None
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def hooks_installed() -> bool:
    status = settings.status()
    return any(status[e] for e in settings.EVENTS)


def _clean_env() -> dict:
    """The environment for pipx, uv and the new copy: without the hook's PYTHONPATH,
    which points at the old package folder, and with the usual tool folders on PATH."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PATH"] = package.search_path()
    return env


def _run(cmd: list, timeout: float) -> None:
    p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       timeout=timeout, env=_clean_env())
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip().splitlines()[-1:] or ["no output"]
        raise RuntimeError("%s exited %d: %s" % (os.path.basename(cmd[0]), p.returncode, tail[0]))


def update(check_only: bool = False) -> dict:
    """Check main and upgrade when it's newer. Returns {"current", "latest", "outcome",
    "upgraded"}; raises on failure, after recording it. A check_only run doesn't count
    as the day's check, so it never holds back an update."""

    def record(outcome: str) -> None:
        stamp = {} if check_only else {"checked_at": time.time()}
        _record(latest=result["latest"], outcome=outcome, **stamp)
        log("update: " + outcome)

    with _lock():
        result = {"current": __version__, "latest": "", "upgraded": False}
        try:
            result["latest"] = latest = latest_version()
            if not is_newer(latest):
                result["outcome"] = "up to date"
            elif check_only:
                result["outcome"] = "%s is available" % latest
            else:
                cmd = package.upgrade_command()
                if not cmd:
                    raise RuntimeError("Baton wasn't installed with pipx or uv, or that tool "
                                       "is missing")
                _run(cmd, UPGRADE_TIMEOUT)
                if hooks_installed():
                    # The new copy re-registers its hooks in case their command changed
                    # (a new Python, say). settings.json is only rewritten if it did.
                    _run([sys.executable, "-m", "baton", "install"], 60)
                result.update(outcome="updated %s -> %s" % (__version__, latest), upgraded=True)
        except Exception as e:  # noqa: BLE001
            record("failed: %s: %s" % (type(e).__name__, e))
            raise
        record(result["outcome"])
        return result
