"""`baton doctor`: check the install, config, key and hooks, and say what to fix."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

from . import install as inst
from . import keys, ui
from .config import config_path, load, old_config_dir, provider_config

OK, WARN, FAIL = "ok", "warn", "fail"


def checks(online: bool = False) -> list:
    """[(level, title, detail)]."""
    out = []
    add = lambda lvl, title, detail="": out.append((lvl, title, detail))  # noqa: E731

    v = sys.version_info
    add(OK if v >= (3, 9) else FAIL, "Python %d.%d.%d" % v[:3],
        "" if v >= (3, 9) else "Baton needs Python 3.9 or newer")

    path = config_path()
    if not path.exists():
        add(FAIL, "Config", "%s doesn't exist. Run `baton setup`." % path)
    else:
        try:
            json.loads(path.read_text())
            add(OK, "Config", str(path))
        except ValueError as e:
            add(FAIL, "Config", "%s isn't valid JSON: %s" % (path, e))
        if old_config_dir() in path.parents:
            add(WARN, "Config location", "Still using ~/.config/claude-handoff. "
                "Run `baton install` or `baton setup` to copy it to ~/.config/baton.")
    cfg = load()
    if not cfg.get("enabled", True):
        add(WARN, "Enabled", "Turned off (\"enabled\": false or BATON=0)")

    pcfg = provider_config(cfg)
    key_env = ""
    if pcfg is None:
        add(FAIL, "Provider", "None chosen, so nothing is ever sent. Run `baton setup`.")
    else:
        from .providers import PROVIDERS
        if pcfg.get("type") not in PROVIDERS:
            add(FAIL, "Provider", "%s has unknown type %r" % (pcfg["name"], pcfg.get("type")))
        else:
            where = pcfg.get("base_url") or ""
            add(OK, "Provider", "%s (%s, model %s)%s" % (
                pcfg["name"], pcfg["type"], pcfg.get("model") or "default",
                (" at " + where) if where else ""))
        key_env = pcfg.get("api_key_env") or ""
        if key_env:
            if os.environ.get(key_env):
                add(OK, "API key", "%s is set in the environment" % key_env)
            elif keys.read().get(key_env):
                add(OK, "API key", "%s is in %s" % (key_env, keys.secrets_path()))
            else:
                add(FAIL, "API key", "%s isn't set. Run `baton setup` to add it." % key_env)
        else:
            add(OK, "API key", "Not needed for this provider")

    m = keys.mode()
    if m is not None:
        if m & 0o077:
            add(WARN, "Secrets file", "%s is mode %o. Run: chmod 600 %s"
                % (keys.secrets_path(), m, keys.secrets_path()))
        else:
            add(OK, "Secrets file", "%s (mode %o)" % (keys.secrets_path(), m))

    ra = cfg.get("run_actions") or {}
    if not ra.get("enabled"):
        add(OK, "Commands", "Off. The provider's commands are never run.")
    else:
        allow = ra.get("allow")
        n = len(allow) if isinstance(allow, list) else sum(
            len(v or []) for v in (allow or {}).values()) if isinstance(allow, dict) else 0
        add(OK if n else WARN, "Commands",
            "On%s, %d allowed, up to %s per reply" % (
                " (read-only)" if ra.get("read_only") else "", n, ra.get("max_commands", 20)))

    settings = inst.settings_path()
    data = inst._load(settings)
    hooks = data.get("hooks") or {}
    for event in inst.EVENTS:
        ours = [h for e in hooks.get(event) or [] if inst._is_ours(e)
                for h in (e.get("hooks") or [e]) if inst._has_marker(h.get("command"))]
        if not ours:
            add(FAIL, "Hook %s" % event, "Not installed in %s. Run `baton install`." % settings)
            continue
        if len(ours) > 1:
            add(WARN, "Hook %s" % event, "%d Baton entries; `baton install` keeps one"
                % len(ours))
        cmd = ours[0].get("command") or ""
        problems = _command_problems(cmd)
        if "claude_handoff.hooks" in cmd:
            problems.append("old claude-handoff entry; run `baton install`")
        add(WARN if problems else OK, "Hook %s" % event, "; ".join(problems) or "installed")

    if online and pcfg is not None:
        from .wizard import test_connection
        key = os.environ.get(key_env, "") or keys.read().get(key_env, "")
        ok, msg = test_connection(pcfg, key_env, key)
        add(OK if ok else FAIL, "Connection", msg)
    return out


def _command_problems(cmd: str) -> list:
    """The Python and package folder a hook command points at must still exist."""
    problems = []
    m = re.search(r'PYTHONPATH="([^"]+)"\s+"([^"]+)"', cmd)
    if not m:
        return problems
    root, py = m.group(1), m.group(2)
    if not Path(py).exists():
        problems.append("Python %s is gone; run `baton install` again" % py)
    if not (Path(root) / "baton").is_dir() and not (Path(root) / "claude_handoff").is_dir():
        problems.append("package folder %s is gone; run `baton install` again"
                        % shlex.quote(root))
    return problems


def run(args) -> int:
    results = checks(online=getattr(args, "online", False))
    mark = {OK: ui.green("ok  "), WARN: ui.yellow("warn"), FAIL: ui.red("fail")}
    for lvl, title, detail in results:
        print("%s  %s%s" % (mark[lvl], ui.bold(title), ("  " + detail) if detail else ""))
    fails = sum(1 for r in results if r[0] == FAIL)
    warns = sum(1 for r in results if r[0] == WARN)
    print()
    if fails:
        print(ui.red("%d problem%s to fix." % (fails, "" if fails == 1 else "s")))
        return 1
    print(ui.green("All good.") + (" %d warning%s." % (warns, "" if warns == 1 else "s")
                                   if warns else ""))
    return 0
