"""baton setup, run unattended."""
import json
import os
import stat
import unittest
from unittest import mock

from baton import keys
from baton.claude import settings
from tests.helpers import Home


class Setup(Home):
    def test_needs_a_provider_when_unattended(self):
        rc, out = self.cli("setup", "--yes")
        self.assertEqual(rc, 2)
        self.assertIn("--provider is required", out)

    def test_needs_a_key_for_hosted_providers(self):
        rc, out = self.cli("setup", "--provider", "grok", "--yes", "--no-test")
        self.assertEqual(rc, 2)
        self.assertIn("needs an API key", out)
        self.assertFalse(self.settings.exists())

    def test_unattended_setup_writes_config_key_and_hooks(self):
        os.environ["TEST_KEY"] = "sk-test-123"
        rc, out = self.cli("setup", "--provider", "grok", "--api-key-env", "TEST_KEY",
                           "--yes", "--no-test")
        self.assertEqual(rc, 0, out)
        self.assertNotIn("sk-test-123", out)
        cfg = json.loads(self.cfg_file.read_text())
        self.assertEqual(cfg["provider"], "xai")
        self.assertEqual(cfg["providers"]["xai"]["model"], "auto")
        self.assertTrue(cfg["run_actions"]["enabled"])
        self.assertEqual(cfg["run_actions"]["allow"], "all")
        self.assertIn("full permissions", out)
        self.assertIn(str(self.cfg_file), out)
        self.assertEqual(stat.S_IMODE(self.secrets.stat().st_mode), 0o600)
        self.assertEqual(keys.read()["XAI_API_KEY"], "sk-test-123")
        for event in settings.EVENTS:
            self.assertEqual(len(self.ours(event)), 1)
        # Running it again replaces rather than duplicates.
        rc, _ = self.cli("setup", "--provider", "grok", "--yes", "--no-test")
        self.assertEqual(rc, 0)
        for event in settings.EVENTS:
            self.assertEqual(len(self.ours(event)), 1)

    def test_key_from_the_environment_is_saved_for_the_hooks(self):
        os.environ["XAI_API_KEY"] = "from-env"
        rc, out = self.cli("setup", "--provider", "grok", "--yes", "--no-test")
        self.assertEqual(rc, 0, out)
        self.assertEqual(keys.read()["XAI_API_KEY"], "from-env")
        self.assertEqual(stat.S_IMODE(self.secrets.stat().st_mode), 0o600)

    def test_connection_test_failure_stops_setup(self):
        with mock.patch("baton.commands.setup.ping", return_value=(False, "HTTP 401")):
            rc, out = self.cli("setup", "--provider", "openai", "--api-key", "k", "--yes")
        self.assertEqual(rc, 2)
        self.assertIn("connection test failed", out)
        self.assertFalse(self.settings.exists())
        self.assertFalse(self.secrets.exists())

    def test_connection_test_success(self):
        with mock.patch("baton.commands.setup.ping", return_value=(True, "pong")) as t:
            rc, out = self.cli("setup", "--provider", "anthropic", "--api-key", "k", "--yes")
        self.assertEqual(rc, 0, out)
        self.assertEqual(t.call_args[0][1:], ("ANTHROPIC_API_KEY", "k"))
        self.assertIn("answered: pong", out)

    def test_local_with_read_only_commands(self):
        rc, out = self.cli("setup", "--provider", "local", "--base-url",
                           "http://127.0.0.1:9/v1", "--model", "qwen", "--commands",
                           "read-only", "--yes", "--no-test")
        self.assertEqual(rc, 0, out)
        cfg = json.loads(self.cfg_file.read_text())
        self.assertEqual(cfg["providers"]["local"]["base_url"], "http://127.0.0.1:9/v1")
        self.assertEqual(cfg["providers"]["local"]["model"], "qwen")
        ra = cfg["run_actions"]
        self.assertTrue(ra["enabled"] and ra["read_only"])
        self.assertNotIn("rm", ra["allow"])
        self.assertFalse(self.secrets.exists())

    def test_commands_off_and_back_on(self):
        rc, out = self.cli("setup", "--provider", "local", "--commands", "off", "--yes", "--no-test")
        self.assertEqual(rc, 0, out)
        self.assertFalse(json.loads(self.cfg_file.read_text())["run_actions"]["enabled"])
        self.assertNotIn("full permissions", out)
        # Re-running keeps "off": it's in the file now.
        self.cli("setup", "--provider", "local", "--yes", "--no-test")
        self.assertFalse(json.loads(self.cfg_file.read_text())["run_actions"]["enabled"])
        self.cli("setup", "--provider", "local", "--commands", "on", "--yes", "--no-test")
        ra = json.loads(self.cfg_file.read_text())["run_actions"]
        self.assertTrue(ra["enabled"])
        self.assertEqual(ra["allow"], "all")

    def test_rerun_keeps_hand_edits(self):
        self.cli("setup", "--provider", "grok", "--api-key", "k", "--yes", "--no-test")
        cfg = json.loads(self.cfg_file.read_text())
        cfg["providers"]["xai"]["model"] = "grok-4.5"
        cfg["run_actions"].update({"allow": ["ls", "rm"], "max_commands": 7})
        cfg["install"]["remove_old"] = False
        self.cfg_file.write_text(json.dumps(cfg))
        rc, out = self.cli("setup", "--provider", "grok", "--yes", "--no-test")
        self.assertEqual(rc, 0, out)
        cfg = json.loads(self.cfg_file.read_text())
        self.assertEqual(cfg["providers"]["xai"]["model"], "grok-4.5")
        self.assertEqual(cfg["run_actions"]["allow"], ["ls", "rm"])
        self.assertEqual(cfg["run_actions"]["max_commands"], 7)
        self.assertFalse(cfg["install"]["remove_old"])
        # --reset puts the model and commands back to the defaults, keeps the rest.
        rc, out = self.cli("setup", "--provider", "grok", "--reset", "--yes", "--no-test")
        cfg = json.loads(self.cfg_file.read_text())
        self.assertEqual(cfg["providers"]["xai"]["model"], "auto")
        self.assertEqual(cfg["run_actions"]["allow"], "all")
        self.assertEqual(cfg["run_actions"]["max_commands"], 7)

    def test_old_shipped_defaults_move_to_the_new_ones(self):
        self.cfg_file.parent.mkdir(parents=True)
        self.cfg_file.write_text(json.dumps({
            "provider": "xai", "providers": {"xai": {"type": "xai", "model": "grok-4"}},
            "run_actions": {"enabled": False, "timeout_seconds": 60, "max_commands": 20,
                            "allow": []}}))
        rc, out = self.cli("setup", "--provider", "grok", "--api-key", "k", "--yes", "--no-test")
        self.assertEqual(rc, 0, out)
        cfg = json.loads(self.cfg_file.read_text())
        self.assertEqual(cfg["providers"]["xai"]["model"], "auto")
        self.assertEqual(cfg["run_actions"]["allow"], "all")
        self.assertTrue(cfg["run_actions"]["enabled"])

    def test_setup_shows_what_auto_picks(self):
        listing = [{"id": "grok-4.3", "created": 100}, {"id": "grok-4.7", "created": 300},
                   {"id": "grok-4.20-0309-reasoning", "created": 200}]
        with mock.patch("baton.models.list_models", return_value=listing), \
                mock.patch("baton.commands.setup.ping", return_value=(True, "pong")) as t:
            rc, out = self.cli("setup", "--provider", "grok", "--api-key", "k", "--yes")
        self.assertEqual(rc, 0, out)
        self.assertIn("auto (grok-4.7 right now)", out)
        self.assertEqual(t.call_args[0][0]["model"], "grok-4.7")
        self.assertEqual(json.loads(self.cfg_file.read_text())["providers"]["xai"]["model"], "auto")

    def test_custom_commands_need_a_list(self):
        rc, out = self.cli("setup", "--provider", "local", "--commands", "custom",
                           "--yes", "--no-test")
        self.assertEqual(rc, 2)
        rc, out = self.cli("setup", "--provider", "local", "--commands", "custom",
                           "--allow", "ls, rm", "--yes", "--no-test")
        self.assertEqual(rc, 0, out)
        self.assertEqual(json.loads(self.cfg_file.read_text())["run_actions"]["allow"],
                         ["ls", "rm"])

    def test_dry_run_writes_nothing_and_shows_the_hooks(self):
        rc, out = self.cli("setup", "--provider", "local", "--yes", "--no-test", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn('"MessageDisplay"', out)
        self.assertIn("-m baton.hooks display", out)
        self.assertFalse(self.cfg_file.exists())
        self.assertFalse(self.settings.exists())

    def test_keeps_other_hooks_and_replaces_claude_handoff(self):
        self.settings.parent.mkdir(parents=True)
        other = {"hooks": [{"type": "command", "command": "other-tool stop"}]}
        old = {"hooks": [{"type": "command",
                          "command": 'PYTHONPATH="/x" "python3" -m claude_handoff.hooks stop'}]}
        self.settings.write_text(json.dumps({"hooks": {"Stop": [other, old]}}))
        rc, _ = self.cli("setup", "--provider", "local", "--yes", "--no-test")
        self.assertEqual(rc, 0)
        stop = self.hooks()["Stop"]
        self.assertIn(other, stop)
        self.assertNotIn(old, stop)
        self.assertEqual(len(self.ours("Stop")), 1)

    def test_migrates_claude_handoff_config(self):
        old = self.home / ".config" / "claude-handoff"
        old.mkdir(parents=True)
        (old / "config.json").write_text(json.dumps(
            {"provider": "xai", "run_actions": {"enabled": True, "allow": ["ls"]},
             "never_handoff_patterns": ["secret"]}))
        (old / "secrets.env").write_text("XAI_API_KEY=k\n")
        rc, out = self.cli("setup", "--provider", "grok", "--yes", "--no-test")
        self.assertEqual(rc, 0, out)
        cfg = json.loads(self.cfg_file.read_text())
        self.assertEqual(cfg["never_handoff_patterns"], ["secret"])
        self.assertEqual(cfg["run_actions"]["allow"], ["ls"])  # kept, not reset
        self.assertTrue(cfg["run_actions"]["enabled"])

if __name__ == "__main__":
    unittest.main()
