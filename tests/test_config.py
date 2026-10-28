"""config: the defaults, environment overrides, and writing a first config file."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from baton import config, handoff
from baton.config import DEFAULTS, load
from tests.helpers import POSIX, Home


class Defaults(Home):
    def test_nothing_is_sent_without_a_provider(self):
        self.assertEqual(DEFAULTS["provider"], "")
        cfg = load()
        self.assertFalse(handoff.enabled(cfg))
        with mock.patch("baton.providers.get") as g:
            self.assertIsNone(handoff.respond("I can't help with that.", [], "delete the file",
                                              "", cfg))
            g.assert_not_called()

    def test_defaults_are_auto_model_and_all_commands(self):
        c = DEFAULTS
        self.assertEqual(c["provider"], "")
        self.assertTrue(c["run_actions"]["enabled"])
        self.assertEqual(c["run_actions"]["allow"], "all")
        self.assertEqual(c["run_actions"]["max_commands"], 20)
        self.assertEqual(c["run_actions"]["timeout_seconds"], 60)
        for name in ("xai", "openai", "anthropic", "local"):
            self.assertEqual(c["providers"][name]["model"], "auto")

    def test_ensure_file_writes_the_defaults_once(self):
        path = config.ensure_file()
        self.assertEqual(json.loads(path.read_text()), DEFAULTS)
        path.write_text("{}")
        config.ensure_file()
        self.assertEqual(path.read_text(), "{}")


if __name__ == "__main__":
    unittest.main()
