"""The baton command: subcommands that aren't setup, doctor or uninstall."""
import json
import unittest

from baton import __version__
from tests.helpers import Home


class Cli(Home):
    def test_plain_install_says_no_provider_yet(self):
        rc, out = self.cli("install")
        self.assertEqual(rc, 0)
        self.assertIn("No provider is set yet", out)
        self.assertTrue(self.cfg_file.exists())
        self.assertEqual(len(self.ours("Stop")), 1)

    def test_install_migrates_claude_handoff_files(self):
        old = self.home / ".config" / "claude-handoff"
        old.mkdir(parents=True)
        (old / "config.json").write_text(json.dumps({"provider": "openai"}))
        rc, out = self.cli("install")
        self.assertEqual(rc, 0, out)
        self.assertIn("Copied", out)
        self.assertEqual(json.loads(self.cfg_file.read_text())["provider"], "openai")

    def test_status_and_version(self):
        rc, out = self.cli("status")
        self.assertEqual(rc, 0)
        self.assertIn("Stop", out)
        rc, out = self.cli("version")
        self.assertEqual(rc, 0)
        self.assertIn(__version__, out)

    def test_old_hook_subcommands_are_gone(self):
        for name in ("prompt", "stop", "check"):
            with self.assertRaises(SystemExit):
                self.cli(name)

if __name__ == "__main__":
    unittest.main()
