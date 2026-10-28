"""providers: picking a provider, and the HTTP ones' request and answer."""
import os
import unittest
from unittest import mock

from baton import providers
from baton.providers.base import ProviderError, brief
from baton.providers.openai_compatible import OpenAICompatibleProvider, XAIProvider


class Providers(unittest.TestCase):
    def test_openai_compatible_parses(self):
        p = OpenAICompatibleProvider({"type": "openai_compatible", "name": "local",
                                      "base_url": "http://example.invalid/v1", "model": "x"})
        with mock.patch("baton.providers.openai_compatible.http_json",
                        return_value={"choices": [{"message": {"content": "hi"}}]}) as h:
            self.assertEqual(p.complete("ping", {"cwd": "/w"}), "hi")
        messages = h.call_args[0][3]["messages"]
        self.assertEqual(messages[1]["content"], brief({"cwd": "/w"}))
        self.assertEqual(messages[-1], {"role": "user", "content": "ping"})

    def test_xai_requires_key(self):
        p = XAIProvider({"type": "xai", "name": "xai", "api_key_env": "NO_SUCH_KEY_FOR_TEST"})
        with mock.patch.dict(os.environ):
            os.environ.pop("NO_SUCH_KEY_FOR_TEST", None)
            with self.assertRaises(ProviderError):
                p.complete("ping")

    def test_anthropic_puts_the_context_before_the_task(self):
        p = providers.get({"type": "anthropic", "name": "anthropic", "model": "claude-x",
                           "api_key_env": "TEST_ANTHROPIC_KEY"})
        with mock.patch.dict(os.environ, {"TEST_ANTHROPIC_KEY": "k"}), \
                mock.patch("baton.providers.anthropic.http_json",
                           return_value={"content": [{"type": "text", "text": "ok"}]}) as h:
            self.assertEqual(p.complete("do it", {"cwd": "/w"}), "ok")
        content = h.call_args[0][3]["messages"][0]["content"]
        self.assertEqual(content, brief({"cwd": "/w"}) + "\n\ndo it")

    def test_ping_reports_failure_without_raising(self):
        with mock.patch("baton.providers.openai_compatible.http_json",
                        side_effect=ProviderError("HTTP 401")):
            ok, detail = providers.ping({"type": "openai", "name": "openai", "model": "gpt-x"},
                                        "OPENAI_API_KEY", "k")
        self.assertFalse(ok)
        self.assertIn("401", detail)


if __name__ == "__main__":
    unittest.main()
