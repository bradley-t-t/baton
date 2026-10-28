"""baton uninstall, --purge and --all."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from baton.claude import settings
from baton.commands import uninstall
from tests.helpers import Home


class Uninstall(Home):
    def setUp(self):
        super().setUp()
        self.settings.parent.mkdir(parents=True)
        self.other = {"hooks": [{"type": "command", "command": "other-tool stop"}]}
        self.settings.write_text(json.dumps({"hooks": {"Stop": [self.other]}}))
        rc, _ = self.cli("setup", "--provider", "openai", "--api-key", "k", "--yes", "--no-test")
        self.assertEqual(rc, 0)

    def test_removes_hooks_keeps_config(self):
        rc, out = self.cli("uninstall")
        self.assertEqual(rc, 0)
        self.assertEqual(self.hooks(), {"Stop": [self.other]})
        self.assertTrue(self.cfg_file.exists() and self.secrets.exists())
        self.assertIn("--purge", out)

    def test_purge_needs_yes_when_not_interactive(self):
        rc, _ = self.cli("uninstall", "--purge")
        self.assertEqual(rc, 1)
        self.assertTrue(self.cfg_file.exists())

    def test_purge_deletes_config_keys_and_logs(self):
        state = self.home / ".local" / "state" / "baton"
        state.mkdir(parents=True)
        (state / "handoff.log").write_text("x")
        old = self.home / ".config" / "claude-handoff"
        old.mkdir()
        rc, out = self.cli("uninstall", "--purge", "--yes")
        self.assertEqual(rc, 0, out)
        for p in (self.cfg_file.parent, state, old):
            self.assertFalse(p.exists(), p)
        self.assertEqual(self.hooks(), {"Stop": [self.other]})
        self.assertTrue(self.settings.exists())


class UninstallEmpty(Home):
    def test_no_empty_hooks_key_is_left(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text('{"model": "opus"}')
        self.cli("setup", "--provider", "local", "--yes", "--no-test")
        self.cli("uninstall")
        self.assertEqual(json.loads(self.settings.read_text()), {"model": "opus"})


class UninstallAll(Home):
    def setUp(self):
        super().setUp()
        self.cli("setup", "--provider", "local", "--yes", "--no-test")
        self.remove = mock.patch("baton.commands.uninstall.removal_command",
                                 return_value=["pipx", "uninstall", "claude-baton"])
        self.remove.start()

    def tearDown(self):
        self.remove.stop()
        super().tearDown()

    def test_plain_uninstall_names_the_last_step(self):
        rc, out = self.cli("uninstall")
        self.assertEqual(rc, 0)
        self.assertIn("To remove the baton command too: pipx uninstall claude-baton", out)
        self.assertIn("baton uninstall --all", out)

    def test_all_removes_everything_then_the_package(self):
        with mock.patch("shutil.which", return_value="/usr/bin/pipx"), \
                mock.patch("os.execvp") as ex:
            rc, out = self.cli("uninstall", "--all", "--yes")
        ex.assert_called_once_with("pipx", ["pipx", "uninstall", "claude-baton"])
        self.assertEqual(self.ours("Stop"), [])
        self.assertFalse(self.cfg_file.parent.exists())
        self.assertEqual(settings.backups(self.settings), [])
        self.assertTrue(self.settings.exists())

    def test_all_prints_the_command_when_the_tool_is_missing(self):
        with mock.patch("shutil.which", return_value=None), mock.patch("os.execvp") as ex:
            rc, out = self.cli("uninstall", "--all", "--yes")
        self.assertEqual(rc, 0)
        ex.assert_not_called()
        self.assertIn("Last step, remove the baton command: pipx uninstall claude-baton", out)

    def test_all_needs_yes_when_not_interactive(self):
        with mock.patch("os.execvp") as ex:
            rc, _ = self.cli("uninstall", "--all")
        self.assertEqual(rc, 1)
        ex.assert_not_called()
        self.assertTrue(self.cfg_file.exists())


class InstallMethod(unittest.TestCase):
    def test_pipx_and_uv_are_recognised(self):
        for marker, want in (("pipx_metadata.json", ["pipx", "uninstall", "claude-baton"]),
                             ("uv-receipt.toml", ["uv", "tool", "uninstall", "claude-baton"])):
            prefix = Path(tempfile.mkdtemp()) / "claude-baton"
            prefix.mkdir()
            (prefix / marker).write_text("")
            with mock.patch("sys.prefix", str(prefix)):
                self.assertEqual(uninstall.removal_command(), want)

    def test_pip_install_uses_pip(self):
        with mock.patch("sys.prefix", tempfile.mkdtemp()), \
                mock.patch("baton.commands.uninstall._installed_name", return_value="claude-baton"):
            cmd = uninstall.removal_command()
        self.assertEqual(cmd[1:], ["-m", "pip", "uninstall", "-y", "claude-baton"])

    def test_not_installed_gives_nothing(self):
        with mock.patch("sys.prefix", tempfile.mkdtemp()), \
                mock.patch("baton.commands.uninstall._installed_name", return_value=""):
            self.assertEqual(uninstall.removal_command(), [])

if __name__ == "__main__":
    unittest.main()
