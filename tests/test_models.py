"""Model "auto": picking the newest flagship from a provider's list, with a fallback."""
import os
import tempfile
import unittest
from unittest import mock

from baton import models
from baton.providers import get


class Pick(unittest.TestCase):
    def test_xai_newest_plain_grok(self):
        listing = [{"id": "grok-4.20-0309-reasoning", "created": 500},
                   {"id": "grok-4.7", "created": 400}, {"id": "grok-4.6", "created": 300},
                   {"id": "grok-build-0.1", "created": 600}, {"id": "grok-imagine-image", "created": 700}]
        self.assertEqual(models.pick("xai", listing), "grok-4.7")

    def test_openai_newest_astra(self):
        listing = [{"id": "gpt-6.1-sol", "created": 900}, {"id": "gpt-6-astra", "created": 800},
                   {"id": "gpt-6-luna", "created": 950}, {"id": "gpt-5.6-cyber", "created": 1000}]
        self.assertEqual(models.pick("openai", listing), "gpt-6-astra")

    def test_anthropic_newest_active_opus_by_line(self):
        listing = [{"id": "claude-fable-5-1", "line": "fable", "created_at": "2026-10-01T00:00:00Z"},
                   {"id": "claude-opus-5-5", "line": "opus", "created_at": "2026-09-21T00:00:00Z"},
                   {"id": "claude-opus-5", "line": "opus", "created_at": "2026-05-01T00:00:00Z"},
                   {"id": "claude-opus-9", "line": "opus", "lifecycle": "deprecated",
                    "created_at": "2027-01-01T00:00:00Z"}]
        self.assertEqual(models.pick("anthropic", listing), "claude-opus-5-5")

    def test_local_first_listed(self):
        self.assertEqual(models.pick("local", [{"id": "qwen3"}, {"id": "llama"}]), "qwen3")
        self.assertEqual(models.pick("local", []), "")

    def test_no_match_gives_nothing(self):
        self.assertEqual(models.pick("xai", [{"id": "grok-imagine-image"}]), "")


class Resolve(unittest.TestCase):
    def setUp(self):
        self.env = mock.patch.dict(os.environ, {"BATON_STATE": tempfile.mkdtemp()})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_list_then_cache(self):
        with mock.patch("baton.models.list_models", return_value=[{"id": "grok-9", "created": 1}]) as lm:
            self.assertEqual(models.resolve({"type": "xai"}, "k"), ("grok-9", "list"))
            self.assertEqual(models.resolve({"type": "xai"}, "k"), ("grok-9", "cache"))
            self.assertEqual(lm.call_count, 1)
        self.assertEqual(models.cached({"type": "xai"}), "grok-9")

    def test_fallback_when_the_list_fails(self):
        with mock.patch("baton.models.list_models", side_effect=OSError("down")) as lm:
            self.assertEqual(models.resolve({"type": "openai"}, "k"), ("gpt-6-astra", "fallback"))
            # Not asked again right away.
            self.assertEqual(models.resolve({"type": "openai"}, "k")[0], "gpt-6-astra")
            self.assertEqual(lm.call_count, 1)
        self.assertEqual(models.cached({"type": "anthropic"}), "claude-opus-5-5")

    def test_provider_sends_the_resolved_model(self):
        sent = {}

        def fake_http(url, method, headers, body, timeout=120):
            if method == "GET":
                return {"data": [{"id": "grok-4.7", "created": 2}, {"id": "grok-4.6", "created": 1}]}
            sent.update(body)
            return {"choices": [{"message": {"content": "pong"}}]}
        with mock.patch("baton.providers.base.http_json", side_effect=fake_http), \
                mock.patch("baton.providers.openai_compatible.http_json", side_effect=fake_http), \
                mock.patch.dict(os.environ, {"XAI_API_KEY": "k"}):
            out = get({"type": "xai", "model": "auto", "api_key_env": "XAI_API_KEY"}).complete("hi")
        self.assertEqual(out, "pong")
        self.assertEqual(sent["model"], "grok-4.7")

    def test_a_named_model_is_used_as_is(self):
        p = get({"type": "openai", "model": "gpt-6.1-sol"})
        with mock.patch("baton.models.resolve") as r:
            self.assertEqual(p.model(), "gpt-6.1-sol")
            r.assert_not_called()

if __name__ == "__main__":
    unittest.main()
