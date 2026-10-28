"""config: defaults, the example file, and the claude-handoff fallbacks and migration."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from baton import config, handoff
from baton.config import DEFAULTS, load
from tests.helpers import Home


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
        example = json.loads(config.example_config_path().read_text())
        for c in (DEFAULTS, example):
            self.assertEqual(c["provider"], "")
            self.assertTrue(c["run_actions"]["enabled"])
            self.assertEqual(c["run_actions"]["allow"], "all")
            self.assertEqual(c["run_actions"]["max_commands"], 20)
            self.assertEqual(c["run_actions"]["timeout_seconds"], 60)
            self.assertTrue(c["install"]["remove_old"])
            for name in ("xai", "openai", "anthropic", "local"):
                self.assertEqual(c["providers"][name]["model"], "auto")

    def test_ensure_file_copies_the_example_once(self):
        path = config.ensure_file()
        self.assertEqual(json.loads(path.read_text()), json.loads(
            config.example_config_path().read_text()))
        path.write_text("{}")
        config.ensure_file()
        self.assertEqual(path.read_text(), "{}")


class OldNames(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.home, "HOME": self.home})
        self.env.start()
        for k in [k for k in os.environ if k.startswith(("BATON_CONFIG", "CLAUDE_HANDOFF"))]:
            os.environ.pop(k)
        os.environ.pop("BATON_PROVIDER", None)

    def tearDown(self):
        self.env.stop()

    def test_old_env_vars_still_work_and_new_ones_win(self):
        os.environ["CLAUDE_HANDOFF_PROVIDER"] = "openai"
        self.assertEqual(config.load()["provider"], "openai")
        os.environ["BATON_PROVIDER"] = "local"
        self.assertEqual(config.load()["provider"], "local")
        os.environ["CLAUDE_HANDOFF"] = "0"
        self.assertFalse(config.load()["enabled"])

    def test_old_config_dir_is_used_until_there_is_a_new_one(self):
        old = Path(self.home) / "claude-handoff" / "config.json"
        old.parent.mkdir(parents=True)
        old.write_text("{}")
        self.assertEqual(config.config_path(), old)
        new = Path(self.home) / "baton" / "config.json"
        new.parent.mkdir(parents=True)
        new.write_text("{}")
        self.assertEqual(config.config_path(), new)

    def test_migration_copies_files_and_raises_the_old_cap(self):
        old_dir = Path(self.home) / "claude-handoff"
        old_dir.mkdir()
        example = json.loads(config.example_config_path().read_text())
        example["run_actions"]["max_commands"] = config.OLD_MAX_COMMANDS
        (old_dir / "config.json").write_text(json.dumps(example))
        (old_dir / "secrets.env").write_text("XAI_API_KEY=x\n")
        self.assertEqual(sorted(Path(m).name for m in config.migrate_old_files()),
                         ["config.json", "secrets.env"])
        new_cfg = json.loads((Path(self.home) / "baton" / "config.json").read_text())
        self.assertEqual(new_cfg["run_actions"]["max_commands"], 20)
        secrets = Path(self.home) / "baton" / "secrets.env"
        self.assertEqual(secrets.read_text(), "XAI_API_KEY=x\n")
        self.assertEqual(secrets.stat().st_mode & 0o777, 0o600)
        self.assertTrue((old_dir / "config.json").exists())
        self.assertEqual(config.migrate_old_files(), [])

    def test_a_cap_someone_chose_is_kept(self):
        old_dir = Path(self.home) / "claude-handoff"
        old_dir.mkdir()
        (old_dir / "config.json").write_text(json.dumps({"run_actions": {"max_commands": 8}}))
        config.migrate_old_files()
        new_cfg = json.loads((Path(self.home) / "baton" / "config.json").read_text())
        self.assertEqual(new_cfg["run_actions"]["max_commands"], 8)

if __name__ == "__main__":
    unittest.main()
