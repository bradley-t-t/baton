"""Keeping Baton current: compare this version with the one on main and, when main is
newer, reinstall with the tool that installed Baton.

Every hook calls start_if_due() as it finishes. At most once a minute it starts
`baton update --auto` as a detached process, so a check never slows a hook and never
prints to Claude Code. The check asks GitHub with the ETag of the last answer, so an
unchanged main costs a 304 and nothing against GitHub's rate limit. Hooks are fresh
processes, so the next hook after an update already runs the new copy.

The check time is recorded before the process starts. After a failure the next check
waits 5 minutes, doubling with each failure in a row up to an hour. While an update
holds the lock, or a Windows reinstall is still finishing, no new check starts.
Outcomes go to handoff.log and update.json in ~/.local/state/baton
(%LOCALAPPDATA%\\baton on Windows).

Windows keeps a running program's files locked, so there the reinstall starts once
this process has exited, and the outcome says it was started rather than finished.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

from . import __version__, package, system
from .claude import settings
from .config import state_dir
from .state import log

_FILE = "src/baton/__init__.py"
# The API is current within a minute and answers an unchanged file with a free 304.
# raw.githubusercontent.com is the fallback when the API refuses (its rate limit); its
# cache can be up to 5 minutes behind.
API_URL = "https://api.github.com/repos/%s/contents/%s?ref=%s" % (
    package.REPO.split("github.com/", 1)[1], _FILE, package.BRANCH)
RAW_URL = "%s/%s/%s" % (package.REPO.replace("github.com", "raw.githubusercontent.com"),
                        package.BRANCH, _FILE)
THROTTLE_SECONDS = 60            # at most one check a minute
BACKOFF_SECONDS = 300            # after a failure; doubles with each one in a row
MAX_BACKOFF_SECONDS = 3600
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


def _version_in(text: str, url: str) -> str:
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', text)
    if not m:
        raise ValueError("no version in %s" % url)
    parse(m.group(1))
    return m.group(1)


def _get(url: str, headers: dict, timeout: float):
    req = urllib.request.Request(url, headers=dict({"User-Agent": "baton/" + __version__}, **headers))
    return urllib.request.urlopen(req, timeout=timeout)


def latest_version(cached: dict | None = None, timeout: float = CHECK_TIMEOUT) -> tuple:
    """(version on main, its ETag). cached is the last check's {"latest", "etag"}: when
    the file hasn't changed GitHub answers 304 and that version still holds."""
    cached = cached or {}
    headers = {"Accept": "application/vnd.github.raw"}
    if cached.get("etag") and cached.get("latest"):
        headers["If-None-Match"] = cached["etag"]
    try:
        with _get(API_URL, headers, timeout) as r:
            text = r.read(65536).decode("utf-8", "replace")
            etag = r.headers.get("ETag") or ""
        return _version_in(text, API_URL), etag
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return cached["latest"], cached["etag"]
        if e.code not in (403, 429):
            raise
    with _get(RAW_URL, {}, timeout) as r:
        text = r.read(65536).decode("utf-8", "replace")
    return _version_in(text, RAW_URL), ""


def last() -> dict:
    """What the last check recorded: checked_at, latest, outcome."""
    try:
        data = json.loads((state_dir() / STATE_FILE).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _record(**fields) -> None:
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (STATE_FILE + ".tmp")
    tmp.write_text(json.dumps(dict(last(), **fields), indent=2), encoding="utf-8")
    os.replace(tmp, d / STATE_FILE)


def wait(record: dict) -> float:
    """Seconds between the last check and the next one."""
    outcome = record.get("outcome") or ""
    if outcome.startswith("updating"):
        return UPGRADE_TIMEOUT  # a Windows reinstall is still finishing
    if outcome.startswith("failed"):
        try:
            n = max(1, int(record.get("failures") or 1))
        except (TypeError, ValueError):
            n = 1
        return min(BACKOFF_SECONDS * 2 ** (n - 1), MAX_BACKOFF_SECONDS)
    return THROTTLE_SECONDS


def due(now: float | None = None) -> bool:
    """Installed with pipx or uv, and the wait since the last check is over."""
    if not package.installer():
        return False
    now = time.time() if now is None else now
    record = last()
    try:
        checked = float(record.get("checked_at") or 0)
    except (TypeError, ValueError):
        checked = 0.0
    return now - checked >= wait(record)


def running() -> bool:
    """Another update holds the lock."""
    try:
        with _lock():
            return False
    except Busy:
        return True


def start_if_due() -> bool:
    """Start a background update when one is due. Never raises; True when started."""
    try:
        if not due() or running():
            return False
        _record(checked_at=time.time(), outcome="checking")
        system.start_detached([sys.executable, "-m", "baton", "update", "--auto"],
                              cwd=str(state_dir()))
        return True
    except Exception as e:  # noqa: BLE001
        log("update: couldn't start a check: %s: %s" % (type(e).__name__, e))
        return False


@contextmanager
def _lock():
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    try:
        with system.exclusive_lock(d / LOCK_FILE):
            yield
    except system.Locked:
        raise Busy("another update is running") from None


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
    "upgraded"}; raises on failure, after recording it. A check_only run doesn't stamp
    the check time, so it never holds back an update."""

    def record(outcome: str, **fields) -> None:
        stamp = {} if check_only else {"checked_at": time.time()}
        _record(latest=result["latest"], outcome=outcome, **dict(stamp, **fields))
        log("update: " + outcome)

    with _lock():
        result = {"current": __version__, "latest": "", "upgraded": False}
        try:
            before = last()
            latest, etag = latest_version(before)
            result["latest"] = latest
            if etag != before.get("etag"):
                _record(latest=latest, etag=etag)
            if not is_newer(latest):
                result["outcome"] = "up to date"
            elif check_only:
                result["outcome"] = "%s is available" % latest
            else:
                cmd = package.upgrade_command()
                if not cmd:
                    raise RuntimeError("Baton wasn't installed with pipx or uv, or that tool "
                                       "is missing")
                # The new copy re-registers its hooks in case their command changed (a
                # new Python, say). settings.json is only rewritten if it did.
                steps = [cmd] + ([[sys.executable, "-m", "baton", "install"]]
                                 if hooks_installed() else [])
                if system.windows():
                    # Then a check by the new copy replaces this "updating" record.
                    system.run_after_exit(steps + [[sys.executable, "-m", "baton", "update",
                                                    "--check"]])
                    outcome = "updating %s -> %s once Baton exits" % (__version__, latest)
                else:
                    record("updating %s -> %s" % (__version__, latest))
                    for step, timeout in zip(steps, (UPGRADE_TIMEOUT, 60)):
                        _run(step, timeout)
                    outcome = "updated %s -> %s" % (__version__, latest)
                result.update(outcome=outcome, upgraded=True)
        except Exception as e:  # noqa: BLE001
            record("failed: %s: %s" % (type(e).__name__, e),
                   failures=int(last().get("failures") or 0) + 1)
            raise
        record(result["outcome"], failures=0)
        return result
