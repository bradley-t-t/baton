"""claude.settings: merging Baton's hooks into ~/.claude/settings.json."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from baton.claude import settings
from tests.helpers import Home


class Install(unittest.TestCase):
    def setUp(self):
        self.settings = Path(tempfile.mkdtemp()) / "settings.json"

    def test_hook_command_sources_secrets_and_runs_the_module(self):
        cmd = settings.hook_command("Stop")
        self.assertIn("secrets.env", cmd)
        self.assertIn("-m baton.hooks stop", cmd)
        self.assertEqual(settings.hook_entry("MessageDisplay")["hooks"][0]["timeout"],
                         settings.DISPLAY_TIMEOUT)

    def test_install_uninstall_idempotent(self):
        other = {"hooks": [{"type": "command", "command": "echo other"}]}
        self.settings.write_text(json.dumps({"hooks": {"Stop": [other]}}))
        settings.install(self.settings)
        data = json.loads(self.settings.read_text())
        for event in settings.EVENTS:
            self.assertEqual(sum(settings.is_ours(e) for e in data["hooks"][event]), 1, event)
        self.assertIn(other, data["hooks"]["Stop"])
        settings.install(self.settings)
        data = json.loads(self.settings.read_text())
        self.assertEqual(sum(settings.is_ours(e) for e in data["hooks"]["Stop"]), 1)
        settings.uninstall(self.settings)
        self.assertEqual(json.loads(self.settings.read_text())["hooks"], {"Stop": [other]})
        st = settings.status(self.settings)
        self.assertFalse(any(st[event] for event in settings.EVENTS))

    def test_earlier_baton_hooks_are_replaced(self):
        old_cmd = ('if [ -f ~/.config/baton/secrets.env ]; then set -a; . f; set +a; '
                   'fi; PYTHONPATH="/x/baton" "python3" -m baton.hooks %s')
        tether = {"hooks": [{"type": "command", "command": "~/.tether/hook.sh stop"}]}
        self.settings.write_text(json.dumps({"hooks": {
            "Stop": [tether, {"hooks": [{"type": "command", "command": old_cmd % "stop"}]}],
            "UserPromptSubmit": [{"hooks": [{"type": "command", "command": old_cmd % "prompt"}]}],
        }}))
        settings.install(self.settings)
        hooks = json.loads(self.settings.read_text())["hooks"]
        for event in settings.EVENTS:
            cmds = settings.our_commands({"hooks": hooks}, event)
            self.assertEqual(len(cmds), 1, cmds)
            self.assertIn("-m baton.hooks", cmds[0])
        self.assertIn(tether, hooks["Stop"])
        settings.uninstall(self.settings)
        self.assertEqual(json.loads(self.settings.read_text())["hooks"], {"Stop": [tether]})


class Backups(Home):
    def test_only_the_newest_three_are_kept(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("{}")
        for i in range(5):
            (self.settings.parent / ("settings.json.bak-2020010%d-000000" % i)).write_text("{}")
        (self.settings.parent / "settings.json.bak-mine").write_text("{}")
        settings.install()
        names = [q.name for q in settings.backups(self.settings)]
        self.assertEqual(len(names), settings.KEEP_BACKUPS)
        self.assertNotIn("settings.json.bak-20200100-000000", names)
        self.assertTrue((self.settings.parent / "settings.json.bak-mine").exists())


class InstalledCommands(unittest.TestCase):
    """Hook commands already in settings.json have to keep working after an upgrade."""

    def run_module(self, module):
        return subprocess.run([sys.executable, "-m", module, "--help"], capture_output=True,
                              text=True, cwd=tempfile.mkdtemp(),
                              env={**os.environ, "PYTHONPATH": str(settings.package_root())})

    def test_package_root_holds_the_package(self):
        root = settings.package_root()
        self.assertTrue((root / "baton" / "hooks" / "__main__.py").is_file())

    def test_the_module_runs(self):
        p = self.run_module("baton.hooks")
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("baton.hooks", p.stderr)

if __name__ == "__main__":
    unittest.main()
