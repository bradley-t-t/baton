"""Configuration: ~/.config/baton/config.json, with environment overrides.

Baton was called claude-handoff. The old names still work: a CLAUDE_HANDOFF_* variable is
read when its BATON_* twin isn't set, and ~/.config/claude-handoff/ is used when
~/.config/baton/ has no file of that name yet. `baton install` copies the old files over.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path

DEFAULTS = {
    "enabled": True,
    "provider": "",
    "timeout_seconds": 300,
    "providers": {
        "xai": {"type": "xai", "model": "auto", "api_key_env": "XAI_API_KEY"},
        "openai": {"type": "openai", "model": "auto", "api_key_env": "OPENAI_API_KEY"},
        "local": {"type": "openai_compatible", "base_url": "http://localhost:11434/v1",
                  "model": "auto", "api_key_env": ""},
        "anthropic": {"type": "anthropic", "model": "auto", "api_key_env": "ANTHROPIC_API_KEY"},
    },
    "judge": {"enabled": "auto", "provider": "", "model": "", "timeout_seconds": 45},
    "run_actions": {"enabled": True, "timeout_seconds": 60, "max_commands": 20, "allow": "all"},
    "install": {"remove_old": True},
    "never_handoff_patterns": [],
    "context": {"user_messages": 4, "tool_calls": 10},
}


OLD_NAME = "claude-handoff"
OLD_ENV = "CLAUDE_HANDOFF"


def env(name: str = "", default=None):
    """BATON[_name], else the old CLAUDE_HANDOFF[_name], else default."""
    suffix = ("_" + name) if name else ""
    for var in ("BATON" + suffix, OLD_ENV + suffix):
        v = os.environ.get(var)
        if v is not None and v != "":
            return v
    return default


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config"))


def config_dir() -> Path:
    return config_home() / "baton"


def old_config_dir() -> Path:
    return config_home() / OLD_NAME


def config_file(name: str) -> Path:
    """~/.config/baton/<name>, or the claude-handoff copy when only that one exists."""
    new = config_dir() / name
    old = old_config_dir() / name
    if not new.exists() and old.exists():
        return old
    return new


def config_path() -> Path:
    p = env("CONFIG")
    if p:
        return Path(os.path.expanduser(p))
    return config_file("config.json")


def state_dir() -> Path:
    p = env("STATE")
    if p:
        return Path(os.path.expanduser(p))
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "baton"


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "allow":
            out[k] = _merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _bool(v: str) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def load(path: Path | None = None) -> dict:
    path = path or config_path()
    try:
        user = json.loads(path.read_text())
        if not isinstance(user, dict):
            user = {}
    except (OSError, ValueError):
        user = {}
    c = _merge(DEFAULTS, user)
    if env() is not None:
        c["enabled"] = _bool(env())
    if env("PROVIDER"):
        c["provider"] = env("PROVIDER")
    if env("TIMEOUT"):
        try:
            c["timeout_seconds"] = int(env("TIMEOUT"))
        except ValueError:
            pass
    if env("JUDGE"):
        c["judge"]["enabled"] = _bool(env("JUDGE"))
    if env("RUN_ACTIONS"):
        c["run_actions"]["enabled"] = _bool(env("RUN_ACTIONS"))
    p = provider_config(c)
    if env("MODEL") and p is not None:
        p["model"] = env("MODEL")
    return c


def provider_config(c: dict, name: str | None = None) -> dict | None:
    """The named provider's settings (the active one by default), with its name filled in.
    A name that isn't under "providers" but is a known type is used as that type."""
    name = name or c.get("provider") or ""
    p = (c.get("providers") or {}).get(name)
    if p is None:
        if name in ("xai", "openai", "openai_compatible", "anthropic", "github_issue"):
            p = {"type": name}
            c.setdefault("providers", {})[name] = p
        else:
            return None
    p.setdefault("type", name)
    p["name"] = name
    return p


def example_config_path() -> Path:
    return Path(__file__).resolve().parent / "config.example.json"


def ensure_file() -> Path:
    """Write the example config when there is no config file yet."""
    path = config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(example_config_path(), path)
    return path


OLD_MAX_COMMANDS = 5  # what claude-handoff's config.example.json shipped


def migrate_old_files() -> list:
    """Copy config.json and secrets.env from ~/.config/claude-handoff/ to
    ~/.config/baton/ when only the old one exists. The old files are left in place.

    A copied config still on the old shipped max_commands of 5 is raised to the new
    default, since 5 was low enough to reject ordinary multi-file tasks."""
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
            _raise_old_cap(new)
        moved.append(str(new))
    return moved


def _raise_old_cap(path: Path) -> None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    ra = data.get("run_actions") if isinstance(data, dict) else None
    if isinstance(ra, dict) and ra.get("max_commands") == OLD_MAX_COMMANDS:
        ra["max_commands"] = DEFAULTS["run_actions"]["max_commands"]
        path.write_text(json.dumps(data, indent=2) + "\n")
