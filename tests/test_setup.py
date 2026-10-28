"""baton setup (unattended), doctor, uninstall, and the safe defaults."""
import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from baton import actions, cli, handoff, keys
from baton import install as inst
from baton.config import DEFAULTS, load


class Home(unittest.TestCase):
    """Each test gets its own empty home folder and Claude settings file."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.settings = self.home / ".claude" / "settings.json"
        env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config"),
               "XDG_STATE_HOME": str(self.home / ".local" / "state"),
               "CLAUDE_SETTINGS": str(self.settings), "NO_COLOR": "1"}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        for k in [k for k in os.environ if k.startswith(("BATON", "CLAUDE_HANDOFF"))
                  or k in ("XAI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "TEST_KEY")]:
            os.environ.pop(k)
        self.tty = mock.patch("baton.ui.interactive", return_value=False)
        self.tty.start()
        self.cfg_file = self.home / ".config" / "baton" / "config.json"
        self.secrets = self.home / ".config" / "baton" / "secrets.env"

    def tearDown(self):
        self.tty.stop()
        self.env.stop()

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue() + err.getvalue()

    def hooks(self):
        return json.loads(self.settings.read_text()).get("hooks") or {}

    def ours(self, event):
        return [e for e in self.hooks().get(event) or [] if inst._is_ours(e)]


class SafeDefaults(Home):
    def test_nothing_is_sent_without_a_provider(self):
        self.assertEqual(DEFAULTS["provider"], "")
        cfg = load()
        self.assertFalse(handoff.enabled(cfg))
        with mock.patch("baton.providers.get") as g:
            self.assertIsNone(handoff.run("I can't help with that.", entries=[],
                                          user="delete the file", cfg=cfg))
            g.assert_not_called()

    def test_commands_are_off_by_default(self):
        self.assertFalse(DEFAULTS["run_actions"]["enabled"])
        example = json.loads(inst.example_config_path().read_text())
        self.assertFalse(example["run_actions"]["enabled"])
        self.assertEqual(example["provider"], "")
        self.assertEqual(example["run_actions"]["allow"], [])

    def test_plain_install_says_no_provider_yet(self):
        rc, out = self.cli("install")
        self.assertEqual(rc, 0)
        self.assertIn("No provider is set yet", out)


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
        self.assertEqual(cfg["providers"]["xai"]["model"], "grok-4")
        self.assertFalse(cfg["run_actions"]["enabled"])
        self.assertEqual(stat.S_IMODE(self.secrets.stat().st_mode), 0o600)
        self.assertEqual(keys.read()["XAI_API_KEY"], "sk-test-123")
        for event in inst.EVENTS:
            self.assertEqual(len(self.ours(event)), 1)
        # Running it again replaces rather than duplicates.
        rc, _ = self.cli("setup", "--provider", "grok", "--yes", "--no-test")
        self.assertEqual(rc, 0)
        for event in inst.EVENTS:
            self.assertEqual(len(self.ours(event)), 1)

    def test_key_from_the_environment_is_saved_for_the_hooks(self):
        os.environ["XAI_API_KEY"] = "from-env"
        rc, out = self.cli("setup", "--provider", "grok", "--yes", "--no-test")
        self.assertEqual(rc, 0, out)
        self.assertEqual(keys.read()["XAI_API_KEY"], "from-env")
        self.assertEqual(stat.S_IMODE(self.secrets.stat().st_mode), 0o600)

    def test_key_with_shell_characters_round_trips(self):
        keys.write("XAI_API_KEY", "a b'c$d")
        keys.write("OTHER", "x")
        keys.write("XAI_API_KEY", "new$key")
        self.assertEqual(keys.read(), {"XAI_API_KEY": "new$key", "OTHER": "x"})

    def test_connection_test_failure_stops_setup(self):
        with mock.patch("baton.wizard.test_connection", return_value=(False, "HTTP 401")):
            rc, out = self.cli("setup", "--provider", "openai", "--api-key", "k", "--yes")
        self.assertEqual(rc, 2)
        self.assertIn("connection test failed", out)
        self.assertFalse(self.settings.exists())
        self.assertFalse(self.secrets.exists())

    def test_connection_test_success(self):
        with mock.patch("baton.wizard.test_connection", return_value=(True, "pong")) as t:
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


class Doctor(Home):
    def test_fresh_home_fails_with_fixes(self):
        rc, out = self.cli("doctor")
        self.assertEqual(rc, 1)
        self.assertIn("Run `baton setup`", out)
        self.assertIn("Not installed", out)

    def test_after_setup_all_good(self):
        rc, _ = self.cli("setup", "--provider", "openai", "--api-key", "k", "--yes", "--no-test")
        self.assertEqual(rc, 0)
        os.environ.pop("OPENAI_API_KEY", None)
        rc, out = self.cli("doctor")
        self.assertEqual(rc, 0, out)
        self.assertIn("OPENAI_API_KEY is in", out)
        self.assertNotIn("k\n", out.replace("ok", ""))

    def test_warns_on_loose_secrets_and_missing_key(self):
        self.cli("setup", "--provider", "openai", "--api-key", "k", "--yes", "--no-test")
        os.chmod(self.secrets, 0o644)
        _, out = self.cli("doctor")
        self.assertIn("chmod 600", out)
        self.secrets.write_text("")
        os.environ.pop("OPENAI_API_KEY", None)
        rc, out = self.cli("doctor")
        self.assertEqual(rc, 1)
        self.assertIn("OPENAI_API_KEY isn't set", out)

    def test_flags_a_hook_pointing_at_a_missing_python(self):
        self.cli("setup", "--provider", "local", "--yes", "--no-test")
        data = json.loads(self.settings.read_text())
        h = data["hooks"]["Stop"][0]["hooks"][0]
        h["command"] = h["command"].replace('"%s"' % __import__("sys").executable,
                                            '"/nonexistent/python3"')
        self.settings.write_text(json.dumps(data))
        _, out = self.cli("doctor")
        self.assertIn("/nonexistent/python3 is gone", out)


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
        self.remove = mock.patch("baton.install.removal_command",
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
        self.assertEqual(inst.backups(self.settings), [])
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
                self.assertEqual(inst.removal_command(), want)

    def test_pip_install_uses_pip(self):
        with mock.patch("sys.prefix", tempfile.mkdtemp()), \
                mock.patch("baton.install._installed_name", return_value="claude-baton"):
            cmd = inst.removal_command()
        self.assertEqual(cmd[1:], ["-m", "pip", "uninstall", "-y", "claude-baton"])

    def test_not_installed_gives_nothing(self):
        with mock.patch("sys.prefix", tempfile.mkdtemp()), \
                mock.patch("baton.install._installed_name", return_value=""):
            self.assertEqual(inst.removal_command(), [])


class Backups(Home):
    def test_only_the_newest_three_are_kept(self):
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text("{}")
        for i in range(5):
            (self.settings.parent / ("settings.json.bak-2020010%d-000000" % i)).write_text("{}")
        (self.settings.parent / "settings.json.bak-mine").write_text("{}")
        inst.install()
        names = [q.name for q in inst.backups(self.settings)]
        self.assertEqual(len(names), 3)
        self.assertNotIn("settings.json.bak-20200100-000000", names)
        self.assertTrue((self.settings.parent / "settings.json.bak-mine").exists())


class ReadOnly(unittest.TestCase):
    CFG = {"run_actions": {"enabled": True, "read_only": True,
                           "allow": ["ls", "echo", "cat", "printf"]}}

    def test_redirect_to_a_file_is_refused(self):
        d = tempfile.mkdtemp()
        for script in ("echo hi > %s/f" % d, "printf x >>%s/f" % d, "ls &> %s/f" % d):
            out = actions.run_allowed("```bash\n%s\n```" % script, self.CFG, "xai", cwd=d)
            self.assertEqual(out.get("not_run"), "not_allowed", script)
        self.assertEqual(os.listdir(d), [])

    def test_reading_and_dev_null_are_fine(self):
        d = tempfile.mkdtemp()
        out = actions.run_allowed("```bash\nls %s 2>/dev/null\ncat < /dev/null 2>&1\n```" % d,
                                  self.CFG, "xai", cwd=d)
        self.assertTrue(out["ok"], out["summary"])

    def test_fd_dup_is_not_a_separator_but_background_is(self):
        self.assertEqual(actions.simple_commands("ls x 2>&1 | cat"), ["ls x", "cat"])
        self.assertEqual(actions.simple_commands("ls &> /dev/null"), ["ls '>' /dev/null"])
        self.assertEqual(actions.simple_commands("ls & rm -rf x"), ["ls", "rm -rf x"])
        out = actions.run_allowed("```bash\nls & rm -rf x\n```", self.CFG, "xai",
                                  cwd=tempfile.mkdtemp())
        self.assertEqual(out.get("not_run"), "not_allowed")

    def test_scripts_dont_see_api_keys(self):
        d = tempfile.mkdtemp()
        cfg = {"providers": {"x": {"api_key_env": "MY_KEY"}},
               "run_actions": {"enabled": True, "allow": ["printf"]}}
        with mock.patch.dict(os.environ, {"MY_KEY": "s3cret", "XAI_API_KEY": "s3cret",
                                          "SOME_VAR": "visible"}):
            out = actions.run_allowed('```bash\nprintf "%s|%s|%s" "$MY_KEY" "$XAI_API_KEY"'
                                      ' "$SOME_VAR"\n```', cfg, "x", cwd=d)
        self.assertTrue(out["ok"], out["summary"])
        self.assertNotIn("s3cret", json.dumps(out))
        self.assertIn("visible", json.dumps(out))

    def test_limits_note_mentions_read_only(self):
        self.assertIn("Writing to files", actions.limits_note(self.CFG, "xai"))


if __name__ == "__main__":
    unittest.main()
