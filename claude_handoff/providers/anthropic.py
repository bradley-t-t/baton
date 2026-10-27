"""Anthropic Messages API provider (optional)."""

from __future__ import annotations

from .base import DEFAULT_SYSTEM, Provider, ProviderError, http_json


class AnthropicProvider(Provider):
    def complete(self, task: str, context: dict = None, system: str = "") -> str:
        key = self.require_key()
        model = self.cfg.get("model") or "claude-sonnet-4-5"
        content = task
        if context:
            import json
            content = "Context from the declined session (JSON):\n" + json.dumps(context, indent=2, default=str)[:12000] + "\n\n" + task
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
