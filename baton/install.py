"""Install / uninstall Claude Code hooks into ~/.claude/settings.json."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

# Strings that mark our own entries in the hooks lists. Baton used to be called
# claude-handoff, so entries from an old install count as ours too: install replaces
# them instead of running both, and uninstall removes them.
MARKERS = ("-m baton.hooks", "-m claude_handoff.hooks")


def package_root() -> Path:
    return Path(__file__).resolve().parent.parent


def settings_path() -> Path:
    p = os.environ.get("CLAUDE_SETTINGS")
    if p:
        return Path(os.path.expanduser(p))
    return Path.home() / ".claude" / "settings.json"


def secrets_env_path() -> Path:
    return Path.home() / ".config" / "baton" / "secrets.env"


def old_secrets_env_path() -> Path:
    return Path.home() / ".config" / "claude-handoff" / "secrets.env"


def hook_command(event: str) -> str:
    """A shell command Claude Code will run for this event. Absolute path so it works
    no matter where the session's cwd is. Sources ~/.config/baton/secrets.env
    when that file exists (API keys), without printing them."""
    root = package_root()
    py = sys.executable or "python3"
    event_name = {"UserPromptSubmit": "prompt", "Stop": "stop", "MessageDisplay": "display"}.get(event, "stop")
    run = 'PYTHONPATH="%s" "%s" -m baton.hooks %s' % (root, py, event_name)
    # Always wrap with a conditional source so install stays idempotent whether or not
    # the secrets file is present yet.
    new, old = str(secrets_env_path()), str(old_secrets_env_path())
    return ('if [ -f "%s" ]; then set -a; . "%s"; set +a; '
            'elif [ -f "%s" ]; then set -a; . "%s"; set +a; fi; %s'
            % (new, new, old, old, run))


def _is_ours(entry) -> bool:
    if not isinstance(entry, dict):
        return False
    # Claude Code shape: {"hooks": [{"type":"command","command":"..."}]} or a bare command.
    for h in entry.get("hooks") or []:
        if isinstance(h, dict) and _has_marker(h.get("command")):
            return True
    return _has_marker(entry.get("command"))


def _has_marker(cmd) -> bool:
    return any(m in (cmd or "") for m in MARKERS)


def _ours_entry(event: str) -> dict:
    h = {"type": "command", "command": hook_command(event)}
    if event == "MessageDisplay":
        h["timeout"] = 120  # Grok + actions; docs default 10, command max effectively 600
    return {"hooks": [h]}


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
    for event in ("UserPromptSubmit", "Stop", "MessageDisplay"):
        lst = [e for e in (hooks.get(event) or []) if not _is_ours(e)]
        lst.append(_ours_entry(event))
        hooks[event] = lst
    _save(path, data)
    migrate_old_files()
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


OLD_MAX_COMMANDS = 5  # what claude-handoff's config.example.json shipped


def migrate_old_files() -> list:
    """Copy config.json and secrets.env from ~/.config/claude-handoff/ to
    ~/.config/baton/ when only the old one exists. The old files are left in place.

    A copied config still on the old shipped max_commands of 5 is raised to the new
    default, since 5 was low enough to reject ordinary multi-file tasks."""
    from .config import DEFAULTS, config_dir, env, old_config_dir
    if env("CONFIG"):
        return []
    moved = []
    for name in ("config.json", "secrets.env"):
        old, new = old_config_dir() / name, config_dir() / name
        if not old.is_file() or new.exists():
            continue
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(old, new)
        if name == "secrets.env":
            os.chmod(new, 0o600)
        else:
            _raise_old_cap(new, DEFAULTS["run_actions"]["max_commands"])
        moved.append(str(new))
    return moved


def _raise_old_cap(path: Path, new_cap: int):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    ra = data.get("run_actions") if isinstance(data, dict) else None
    if isinstance(ra, dict) and ra.get("max_commands") == OLD_MAX_COMMANDS:
        ra["max_commands"] = new_cap
        path.write_text(json.dumps(data, indent=2) + "\n")


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
        "MessageDisplay": any(_is_ours(e) for e in hooks.get("MessageDisplay") or []),
    }
