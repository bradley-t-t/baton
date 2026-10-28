"""Anthropic Messages API provider (optional)."""

from __future__ import annotations

from .. import models
from .base import DEFAULT_SYSTEM, Provider, ProviderError, brief, http_json


class AnthropicProvider(Provider):
    def complete(self, task: str, context: dict | None = None, system: str = "") -> str:
        key = self.require_key()
        model = self.cfg.get("model")
        if models.is_auto(model):
            model, _ = models.resolve(self.cfg, key)
        content = task
        if context:
            content = brief(context) + "\n\n" + task
        body = {
            "model": model,
            "max_tokens": int(self.cfg.get("max_tokens") or 4096),
            "system": system or DEFAULT_SYSTEM,
            "messages": [{"role": "user", "content": content}],
        }
        out = http_json("https://api.anthropic.com/v1/messages", "POST",
                        {"x-api-key": key, "anthropic-version": "2023-06-01"},
                        body, timeout=int(self.cfg.get("timeout_seconds") or 120))
        try:
            parts = [b.get("text", "") for b in out.get("content") or [] if isinstance(b, dict)]
            return "\n".join(p for p in parts if p)
        except (KeyError, TypeError) as e:
            raise ProviderError("anthropic: unexpected response: %s" % out) from e
