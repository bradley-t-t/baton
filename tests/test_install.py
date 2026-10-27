"""install / uninstall merge hooks into settings.json idempotently."""
import json
import os
import tempfile
import unittest
from pathlib import Path

STATE = tempfile.mkdtemp()
os.environ["CLAUDE_HANDOFF_STATE"] = STATE

from claude_handoff import install as inst  # noqa: E402


class Install(unittest.TestCase):
    def test_hook_command_sources_secrets_env(self):
        cmd = inst.hook_command("Stop")
        self.assertIn("secrets.env", cmd)
        self.assertIn("claude_handoff.hooks stop", cmd)

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.settings = Path(self.td) / "settings.json"
        self.cfg_dir = Path(self.td) / "cfg"
        os.environ["CLAUDE_SETTINGS"] = str(self.settings)
        os.environ["CLAUDE_HANDOFF_CONFIG"] = str(self.cfg_dir / "config.json")

    def test_install_uninstall_idempotent(self):
        # Pre-existing unrelated hook.
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(json.dumps({
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo other"}]}]}
        }))
        inst.install(self.settings)
        data = json.loads(self.settings.read_text())
        self.assertTrue(any(inst._is_ours(e) for e in data["hooks"]["Stop"]))
        self.assertTrue(any(inst._is_ours(e) for e in data["hooks"]["UserPromptSubmit"]))
        self.assertEqual(sum(1 for e in data["hooks"]["Stop"] if not inst._is_ours(e)), 1)
        # Idempotent.
        inst.install(self.settings)
        data2 = json.loads(self.settings.read_text())
        self.assertEqual(sum(1 for e in data2["hooks"]["Stop"] if inst._is_ours(e)), 1)
        # Uninstall keeps the other hook.
        inst.uninstall(self.settings)
        data3 = json.loads(self.settings.read_text())
        self.assertFalse(any(inst._is_ours(e) for e in data3.get("hooks", {}).get("Stop") or []))
        self.assertEqual(len(data3["hooks"]["Stop"]), 1)
        st = inst.status(self.settings)
        self.assertFalse(st["Stop"])
        self.assertFalse(st["UserPromptSubmit"])


class ProvidersUnit(unittest.TestCase):
    def test_openai_compat_parses(self):
        from claude_handoff.providers.openai_compat import OpenAICompatibleProvider
        p = OpenAICompatibleProvider({"type": "openai_compatible", "name": "local",
                                      "base_url": "http://example.invalid/v1", "model": "x"})
        with unittest.mock.patch("claude_handoff.providers.openai_compat.http_json",
                                 return_value={"choices": [{"message": {"content": "hi"}}]}):
            self.assertEqual(p.complete("ping"), "hi")

    def test_xai_requires_key(self):
        from claude_handoff.providers.base import ProviderError
        from claude_handoff.providers.openai_compat import XAIProvider
        p = XAIProvider({"type": "xai", "name": "xai", "api_key_env": "NO_SUCH_KEY_FOR_TEST"})
        os.environ.pop("NO_SUCH_KEY_FOR_TEST", None)
        with self.assertRaises(ProviderError):
            p.complete("ping")


if __name__ == "__main__":
    unittest.main()
