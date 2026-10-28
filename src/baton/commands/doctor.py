"""`baton doctor`: check the install, config, key and hooks, and say what to fix."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

from .. import __version__, keys, models, package, providers, ui, update
from ..claude import settings
from ..config import config_path, load, old_config_dir, provider_config
from ..runner import allows_all

OK, WARN, FAIL = "ok", "warn", "fail"


def checks(online: bool = False) -> list:
    """[(level, title, detail)] for everything doctor looks at."""
    out = []

    def add(level: str, title: str, detail: str = "") -> None:
        out.append((level, title, detail))

    _python(add)
    _config_file(add)
    cfg = load()
    if not cfg.get("enabled", True):
        add(WARN, "Enabled", "Turned off (\"enabled\": false or BATON=0)")
    pcfg = provider_config(cfg)
    key_env = _provider(add, pcfg)
    _secrets_file(add)
    _commands(add, cfg, pcfg)
    _hooks(add)
    _updates(add)
    if online and pcfg is not None:
        key = os.environ.get(key_env, "") or keys.read().get(key_env, "")
        ok, msg = providers.ping(pcfg, key_env, key)
        add(OK if ok else FAIL, "Connection", msg)
    return out


def _python(add) -> None:
    v = sys.version_info
    add(OK if v >= (3, 9) else FAIL, "Python %d.%d.%d" % v[:3],
        "" if v >= (3, 9) else "Baton needs Python 3.9 or newer")


def _config_file(add) -> None:
    path = config_path()
    if not path.exists():
        add(FAIL, "Config", "%s doesn't exist. Run `baton setup`." % path)
        return
    try:
        json.loads(path.read_text())
        add(OK, "Config", str(path))
    except ValueError as e:
        add(FAIL, "Config", "%s isn't valid JSON: %s" % (path, e))
    if old_config_dir() in path.parents:
        add(WARN, "Config location", "Still using ~/.config/claude-handoff. "
            "Run `baton install` or `baton setup` to copy it to ~/.config/baton.")


def _provider(add, pcfg: dict | None) -> str:
    """Check the provider and its key. Returns the key's variable name."""
    if pcfg is None:
        add(FAIL, "Provider", "None chosen, so nothing is ever sent. Run `baton setup`.")
        return ""
    if pcfg.get("type") not in providers.PROVIDERS:
        add(FAIL, "Provider", "%s has unknown type %r" % (pcfg["name"], pcfg.get("type")))
    else:
        model = pcfg.get("model") or ""
        if models.is_auto(model):
            now = models.cached(pcfg)
            model = "auto" + ((", now " + now) if now else "")
        where = pcfg.get("base_url") or ""
        add(OK, "Provider", "%s (%s, model %s)%s" % (
            pcfg["name"], pcfg["type"], model, (" at " + where) if where else ""))
    key_env = pcfg.get("api_key_env") or ""
    if not key_env:
        add(OK, "API key", "Not needed for this provider")
    elif os.environ.get(key_env):
        add(OK, "API key", "%s is set in the environment" % key_env)
    elif keys.read().get(key_env):
        add(OK, "API key", "%s is in %s" % (key_env, keys.secrets_path()))
    else:
        add(FAIL, "API key", "%s isn't set. Run `baton setup` to add it." % key_env)
    return key_env


def _secrets_file(add) -> None:
    m = keys.mode()
    if m is None:
        return
    path = keys.secrets_path()
    if m & 0o077:
        add(WARN, "Secrets file", "%s is mode %o. Run: chmod 600 %s" % (path, m, path))
    else:
        add(OK, "Secrets file", "%s (mode %o)" % (path, m))


def _commands(add, cfg: dict, pcfg: dict | None) -> None:
    ra = cfg.get("run_actions") or {}
    cap = ra.get("max_commands", 20)
    if not ra.get("enabled"):
        add(OK, "Commands", "Off. The provider's commands are never run.")
    elif allows_all(cfg, (pcfg or {}).get("name") or ""):
        add(OK, "Commands", "On, any command (full permissions), up to %s per reply" % cap)
    else:
        allow = ra.get("allow")
        if isinstance(allow, list):
            n = len(allow)
        elif isinstance(allow, dict):
            n = sum(len(v) for v in allow.values() if isinstance(v, list))
        else:
            n = 0
        add(OK if n else WARN, "Commands", "On%s, %d allowed, up to %s per reply" % (
            " (read-only)" if ra.get("read_only") else "", n, cap))


def _hooks(add) -> None:
    path = settings.settings_path()
    data = settings.read(path)
    for event in settings.EVENTS:
        title = "Hook %s" % event
        ours = settings.our_commands(data, event)
        if not ours:
            add(FAIL, title, "Not installed in %s. Run `baton install`." % path)
            continue
        if len(ours) > 1:
            add(WARN, title, "%d Baton entries; `baton install` keeps one" % len(ours))
        problems = _command_problems(ours[0])
        if "claude_handoff.hooks" in ours[0]:
            problems.append("old claude-handoff entry; run `baton install`")
        add(WARN if problems else OK, title, "; ".join(problems) or "installed")


def _updates(add) -> None:
    """This version, the newest seen on main, and how the last check went."""
    last = update.last()
    latest = last.get("latest") or ""
    title = "Version %s" % __version__ + (", latest %s" % latest if latest else "")
    if not package.upgrade_command():
        add(WARN, title, "This copy wasn't installed with pipx or uv, so it can't update "
            "itself. Reinstall with: curl -fsSL https://taylorurl.com/baton | sh")
        return
    if not last.get("checked_at"):
        add(OK, title, "Not checked for updates yet")
        return
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(last["checked_at"])))
    outcome = last.get("outcome") or "checking"
    add(WARN if outcome.startswith("failed") else OK, title,
        "Last update check %s: %s" % (when, outcome))


def _command_problems(cmd: str) -> list:
    """The Python and package folder a hook command points at must still exist."""
    m = re.search(r'PYTHONPATH="([^"]+)"\s+"([^"]+)"', cmd)
    if not m:
        return []
    root, py = Path(m.group(1)), m.group(2)
    problems = []
    if not Path(py).exists():
        problems.append("Python %s is gone; run `baton install` again" % py)
    if not (root / "baton").is_dir() and not (root / "claude_handoff").is_dir():
        problems.append("package folder %s is gone; run `baton install` again"
                        % shlex.quote(str(root)))
    return problems


def run(args) -> int:
    results = checks(online=getattr(args, "online", False))
    mark = {OK: ui.green("ok  "), WARN: ui.yellow("warn"), FAIL: ui.red("fail")}
    for level, title, detail in results:
        print("%s  %s%s" % (mark[level], ui.bold(title), ("  " + detail) if detail else ""))
    fails = sum(1 for r in results if r[0] == FAIL)
    warns = sum(1 for r in results if r[0] == WARN)
    print()
    if fails:
        print(ui.red("%d problem%s to fix." % (fails, "" if fails == 1 else "s")))
        return 1
    print(ui.green("All good.") + (" %d warning%s." % (warns, "" if warns == 1 else "s")
                                   if warns else ""))
    return 0
