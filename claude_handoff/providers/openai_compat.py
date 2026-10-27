"""OpenAI-compatible chat completions: OpenAI, xAI (Grok), and any base_url (Ollama, LM Studio)."""

from __future__ import annotations

from .base import DEFAULT_SYSTEM, Provider, ProviderError, http_json


class OpenAICompatibleProvider(Provider):
    DEFAULT_BASE = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-4o-mini"

    def base_url(self) -> str:
        return (self.cfg.get("base_url") or self.DEFAULT_BASE).rstrip("/")

    def model(self) -> str:
        return self.cfg.get("model") or self.DEFAULT_MODEL

    def complete(self, task: str, context: dict = None, system: str = "") -> str:
        key = self.api_key()
        headers = {}
        if key:
            headers["Authorization"] = "Bearer " + key
        elif self.cfg.get("api_key_env"):
            # Local endpoints often need no key; only complain when a key env was named.
            raise ProviderError("%s: set %s" % (self.name, self.cfg["api_key_env"]))
        messages = [{"role": "system", "content": system or DEFAULT_SYSTEM}]
        if context:
            messages.append({"role": "user",
                             "content": "Context from the declined session (JSON):\n" + _brief(context)})
        messages.append({"role": "user", "content": task})
        body = {"model": self.model(), "messages": messages}
        if self.cfg.get("temperature") is not None:
            body["temperature"] = self.cfg["temperature"]
        url = self.base_url() + "/chat/completions"
        out = http_json(url, "POST", headers, body, timeout=int(self.cfg.get("timeout_seconds") or 120))
        try:
            return out["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError("%s: unexpected response: %s" % (self.name, out)) from e


class OpenAIProvider(OpenAICompatibleProvider):
    DEFAULT_BASE = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-5"


class XAIProvider(OpenAICompatibleProvider):
    DEFAULT_BASE = "https://api.x.ai/v1"
    DEFAULT_MODEL = "grok-4"

    def complete(self, task: str, context: dict = None, system: str = "") -> str:
        # xAI always needs a key.
        self.require_key()
        return super().complete(task, context, system)


def _brief(ctx: dict) -> str:
    import json
    return json.dumps(ctx, indent=2, default=str)[:12000]
