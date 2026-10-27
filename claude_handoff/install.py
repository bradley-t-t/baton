"""Install / uninstall Claude Code hooks into ~/.claude/settings.json."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

MARKER = "claude-handoff"  # used to find our own entries in the hooks lists


def package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def settings_path() -> Path:
    p = os.environ.get("CLAUDE_SETTINGS")
    if p:
        return Path(os.path.expanduser(p))
    return Path.home() / ".claude" / "settings.json"


def secrets_env_path() -> Path:
    return Path.home() / ".config" / "claude-handoff" / "secrets.env"


def hook_command(event: str) -> str:
    """A shell command Claude Code will run for this event. Absolute path so it works
    no matter where the session's cwd is. Sources ~/.config/claude-handoff/secrets.env
    when that file exists (API keys), without printing them."""
    root = package_root()
    py = sys.executable or "python3"
    event_name = "prompt" if event == "UserPromptSubmit" else "stop"
    run = 'PYTHONPATH="%s" "%s" -m claude_handoff.hooks %s' % (root, py, event_name)
    # Always wrap with a conditional source so install stays idempotent whether or not
    # the secrets file is present yet.
    secrets = str(secrets_env_path())
    return ('if [ -f "%s" ]; then set -a; . "%s"; set +a; fi; %s'
            % (secrets, secrets, run))


def _is_ours(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    # Claude Code shape: {"hooks": [{"type":"command","command":"..."}]} or a bare command.
    for h in entry.get("hooks") or []:
        if isinstance(h, dict) and MARKER in (h.get("command") or ""):
            return True
    if MARKER in (entry.get("command") or ""):
        return True
    return False


def _ours_entry(event: str) -> dict:
    return {"hooks": [{"type": "command", "command": hook_command(event)}]}


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_suffix(path.suffix + ".bak-%s" % time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(path, backup)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def install(path: Path = None) -> Path:
    path = path or settings_path()
    data = _load(path)
    hooks = data.setdefault("hooks", {})
    for event in ("UserPromptSubmit", "Stop"):
        lst = [e for e in (hooks.get(event) or []) if not _is_ours(e)]
        lst.append(_ours_entry(event))
        hooks[event] = lst
    _save(path, data)
    # Seed an example config if none exists.
    from .config import config_path, DEFAULTS
    cfg = config_path()
    if not cfg.exists():
        cfg.parent.mkdir(parents=True, exist_ok=True)
        example = package_root() / "config.example.json"
        if example.exists():
            shutil.copy2(example, cfg)
        else:
            cfg.write_text(json.dumps(DEFAULTS, indent=2) + "\n")
    return path


def uninstall(path: Path = None) -> Path:
    path = path or settings_path()
    data = _load(path)
    hooks = data.get("hooks") or {}
    changed = False
    for event in list(hooks):
        before = hooks[event] or []
        after = [e for e in before if not _is_ours(e)]
        if after != before:
            changed = True
        if after:
            hooks[event] = after
        else:
            hooks.pop(event, None)
    if changed:
        data["hooks"] = hooks
        _save(path, data)
    return path


def status(path: Path = None) -> dict:
    path = path or settings_path()
    data = _load(path)
    hooks = data.get("hooks") or {}
    return {
        "settings": str(path),
        "UserPromptSubmit": any(_is_ours(e) for e in hooks.get("UserPromptSubmit") or []),
        "Stop": any(_is_ours(e) for e in hooks.get("Stop") or []),
    }
