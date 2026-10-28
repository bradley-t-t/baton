"""Configuration: ~/.config/baton/config.json (%APPDATA%\\baton on Windows), with
environment overrides.

DEFAULTS below is the only description of a setting and its default. A config file
holds whatever the person changed; anything missing falls back to DEFAULTS.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from . import system

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
    "context": {"user_messages": 4, "tool_calls": 10},
}


def env(name: str = "", default=None):
    """BATON[_name] from the environment, else default."""
    v = os.environ.get("BATON" + (("_" + name) if name else ""))
    return v if v else default


def config_home() -> Path:
    return system.config_home()


def config_dir() -> Path:
    return config_home() / "baton"


def config_path() -> Path:
    p = env("CONFIG")
    if p:
        return Path(os.path.expanduser(p))
    return config_dir() / "config.json"


def state_dir() -> Path:
    p = env("STATE")
    if p:
        return Path(os.path.expanduser(p))
    return system.state_home() / "baton"


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
        user = json.loads(path.read_text(encoding="utf-8"))
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


def ensure_file() -> Path:
    """Write the defaults as a starting config when there is no config file yet."""
    path = config_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(DEFAULTS, indent=2) + "\n", encoding="utf-8")
    return path
