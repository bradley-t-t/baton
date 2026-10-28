"""OpenAI-compatible chat completions: OpenAI, xAI (Grok), and any base_url (Ollama, LM Studio)."""

from __future__ import annotations

from .. import models
from .base import DEFAULT_SYSTEM, Provider, ProviderError, brief, http_json


class OpenAICompatibleProvider(Provider):
    DEFAULT_BASE = "https://api.openai.com/v1"

    def base_url(self) -> str:
        return (self.cfg.get("base_url") or self.DEFAULT_BASE).rstrip("/")

    def model(self) -> str:
        m = self.cfg.get("model")
        if not models.is_auto(m):
            return m
        m, _ = models.resolve(dict(self.cfg, base_url=self.base_url()), self.api_key())
        if not m:
            raise ProviderError("%s: no model found at %s/models; set a model in the config"
                                % (self.name, self.base_url()))
        return m

    def complete(self, task: str, context: dict | None = None, system: str = "") -> str:
        key = self.api_key()
        headers = {}
        if key:
            headers["Authorization"] = "Bearer " + key
        elif self.cfg.get("api_key_env"):
            # Local endpoints often need no key; only complain when a key env was named.
            raise ProviderError("%s: set %s" % (self.name, self.cfg["api_key_env"]))
        messages = [{"role": "system", "content": system or DEFAULT_SYSTEM}]
        if context:
            messages.append({"role": "user", "content": brief(context)})
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


class XAIProvider(OpenAICompatibleProvider):
    DEFAULT_BASE = "https://api.x.ai/v1"

    def complete(self, task: str, context: dict | None = None, system: str = "") -> str:
        # xAI always needs a key.
        self.require_key()
        return super().complete(task, context, system)
