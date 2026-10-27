"""Provider adapters. Each one takes a task and returns a text answer."""

from __future__ import annotations

from .anthropic import AnthropicProvider
from .github_issue import GitHubIssueProvider
from .openai_compat import OpenAICompatibleProvider, OpenAIProvider, XAIProvider

PROVIDERS = {
    "xai": XAIProvider,
    "openai": OpenAIProvider,
    "openai_compatible": OpenAICompatibleProvider,
    "anthropic": AnthropicProvider,
    "github_issue": GitHubIssueProvider,
}


def get(cfg: dict):
    """Build a provider from a provider config dict (must have "type")."""
    if not cfg or not cfg.get("type"):
        raise ValueError("no provider type configured")
    cls = PROVIDERS.get(cfg["type"])
    if cls is None:
        raise ValueError("unknown provider type: %s" % cfg["type"])
    return cls(cfg)
