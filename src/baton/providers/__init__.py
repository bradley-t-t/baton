"""Provider adapters. Each one takes a task and returns a text answer."""

from __future__ import annotations

import os

from .anthropic import AnthropicProvider
from .base import Provider
from .github_issue import GitHubIssueProvider
from .openai_compatible import OpenAICompatibleProvider, OpenAIProvider, XAIProvider

PROVIDERS = {
    "xai": XAIProvider,
    "openai": OpenAIProvider,
    "openai_compatible": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
    "github_issue": GitHubIssueProvider,
}


def get(cfg: dict) -> Provider:
    """Build a provider from a provider config dict (must have "type")."""
    if not cfg or not cfg.get("type"):
        raise ValueError("no provider type configured")
    cls = PROVIDERS.get(cfg["type"])
    if cls is None:
        raise ValueError("unknown provider type: %s" % cfg["type"])
    return cls(cfg)


def ping(pcfg: dict, key_env: str, key: str) -> tuple:
    """(ok, message) from one short prompt, with key set in key_env for the call."""
    old = os.environ.get(key_env) if key_env else None
    try:
        if key_env and key:
            os.environ[key_env] = key
        out = get(dict(pcfg, timeout_seconds=30)).complete(
            "Reply with exactly: pong", system="Reply with exactly the word pong.")
        return True, (out or "").strip()[:80] or "(empty reply)"
    except Exception as e:  # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, str(e)[:300])
    finally:
        if key_env and key:
            if old is None:
                os.environ.pop(key_env, None)
            else:
                os.environ[key_env] = old
