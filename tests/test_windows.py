"""Windows, simulated: baton.system.windows() is patched to True, so every place that
differs (paths, hooks, background processes, updates, uninstall, the script shell) takes
its Windows branch here. Real Windows runs are in the PR notes."""
import contextlib
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from baton import keys, runner, system, update
from baton.claude import settings
from baton.commands import doctor
from baton.config import config_dir, state_dir
from baton.runner.shell import extract_fences
from tests.helpers import Home


class WindowsHome(Home):
    """A home folder laid out like a Windows profile, with no XDG variables."""

    def setUp(self):
        super().setUp()
        for k in ("XDG_CONFIG_HOME", "XDG_STATE_HOME"):
            os.environ.pop(k, None)
        os.environ["APPDATA"] = str(self.home / "AppData" / "Roaming")
        os.environ["LOCALAPPDATA"] = str(self.home / "AppData" / "Local")
        self.cfg_file = self.home / "AppData" / "Roaming" / "baton" / "config.json"
        self.secrets = self.cfg_file.with_name("secrets.env")
        patch = mock.patch("baton.system.windows", return_value=True)
        patch.start()
        self.addCleanup(patch.stop)


class Paths(WindowsHome):
    def test_config_and_keys_in_appdata_state_in_localappdata(self):
        self.assertEqual(config_dir(), self.home / "AppData" / "Roaming" / "baton")
        self.assertEqual(keys.secrets_path(), self.secrets)
        self.assertEqual(state_dir(), self.home / "AppData" / "Local" / "baton")

    def test_xdg_still_wins(self):
        os.environ["XDG_CONFIG_HOME"] = str(self.home / "x")
        self.assertEqual(config_dir(), self.home / "x" / "baton")

    def test_no_permission_bits_to_warn_about(self):
        keys.write("XAI_API_KEY", "k")
        self.assertIsNone(keys.mode())
        self.assertEqual([c for c in doctor.checks() if c[1] == "Secrets file"], [])


class Hooks(WindowsHome):
    def test_hooks_start_python_directly(self):
        rc, out = self.cli("setup", "--provider", "local", "--yes", "--no-test")
        self.assertEqual(rc, 0, out)
        self.assertTrue(self.cfg_file.exists())
        for event, name in settings.HOOK_NAMES.items():
            [entry] = self.ours(event)
            h = entry["hooks"][0]
            self.assertEqual(h["command"], sys.executable)
            self.assertEqual(h["args"], ["-m", "baton.hooks", name])
        self.cli("uninstall")
        self.assertEqual(self.hooks(), {})

    def test_doctor_checks_the_hook_python(self):
        ok = '"%s" -m baton.hooks stop' % sys.executable
        self.assertEqual(doctor._command_problems(ok), [])
        gone = doctor._command_problems('"C:\\gone\\python.exe" -m baton.hooks stop')
        self.assertIn("is gone", gone[0])

    def test_hook_loads_the_secrets_file(self):
        keys.write("XAI_API_KEY", "from-file")
        os.environ.pop("XAI_API_KEY", None)
        from baton import hooks
        with mock.patch.dict(hooks.HOOKS, {"stop": lambda: os.environ.get("XAI_API_KEY") == "from-file"}), \
                mock.patch("baton.update.start_if_due"):
            self.assertTrue(hooks.main(["stop"]))
        os.environ.pop("XAI_API_KEY", None)


class Updates(WindowsHome):
    def test_background_check_is_detached_without_a_console(self):
        with mock.patch("baton.package.installer", return_value="uv"), \
                mock.patch("subprocess.Popen") as popen:
            self.assertTrue(update.start_if_due())
        kw = popen.call_args[1]
        self.assertEqual(kw["creationflags"], 0x1000208)
        self.assertNotIn("start_new_session", kw)

    def test_reinstall_waits_for_baton_to_exit(self):
        upgrade = ["C:\\uv.exe", "tool", "install", "--force", "--reinstall", "git+x@main"]
        with mock.patch("baton.update.latest_version", return_value="99.0.0"), \
                mock.patch("baton.package.upgrade_command", return_value=upgrade), \
                mock.patch("baton.update.hooks_installed", return_value=True), \
                mock.patch("baton.system.exclusive_lock", return_value=contextlib.nullcontext()), \
                mock.patch("baton.system.run_after_exit") as later, \
                mock.patch("subprocess.run") as run:
            r = update.update()
        run.assert_not_called()
        steps = later.call_args[0][0]
        self.assertEqual(steps[0], upgrade)
        self.assertEqual(steps[1][1:], ["-m", "baton", "install"])
        self.assertEqual(steps[2][1:], ["-m", "baton", "update", "--check"])
        self.assertTrue(r["upgraded"])
        self.assertIn("once Baton exits", update.last()["outcome"])

    def test_run_after_exit_waits_then_chains(self):
        with mock.patch("subprocess.Popen") as popen:
            system.run_after_exit([["C:\\Program Files\\uv.exe", "tool", "x"], ["b"]])
        line = popen.call_args[0][0]
        self.assertEqual(line, 'cmd.exe /d /s /c "ping -n 4 127.0.0.1 >nul & '
                               '"C:\\Program Files\\uv.exe" tool x && b"')
        self.assertEqual(popen.call_args[1]["creationflags"], 0x1000208)

    def test_stays_in_a_job_that_wont_let_it_leave(self):
        with mock.patch("subprocess.Popen", side_effect=[OSError("access denied"), "p"]) as popen:
            self.assertEqual(system.start_detached(["x"]), "p")
        self.assertEqual([c[1]["creationflags"] for c in popen.call_args_list], [0x1000208, 0x208])


class Uninstall(WindowsHome):
    def test_all_removes_the_path_entry_then_the_command_after_exit(self):
        self.cli("setup", "--provider", "local", "--yes", "--no-test")
        with mock.patch("baton.package.removal_command", return_value=["uv", "tool", "uninstall", "claude-baton"]), \
                mock.patch("shutil.which", return_value="C:\\bin\\uv.exe"), \
                mock.patch("baton.system.remove_user_path_entry", return_value="C:\\bin"), \
                mock.patch("baton.system.run_after_exit") as later, \
                mock.patch("os.execvp") as ex:
            rc, out = self.cli("uninstall", "--all", "--yes")
        self.assertEqual(rc, 0, out)
        ex.assert_not_called()
        later.assert_called_once_with([["C:\\bin\\uv.exe", "tool", "uninstall", "claude-baton"]])
        self.assertIn("Removed the PATH entry Baton added in your Windows user PATH", out)
        self.assertFalse(self.cfg_file.parent.exists())


class FakeRegistry:
    """Just enough of winreg for remove_user_path_entry."""
    HKEY_CURRENT_USER, KEY_READ, KEY_WRITE, REG_EXPAND_SZ = "HKCU", 1, 2, 2

    def __init__(self, path, added):
        self.keys = {"Environment": {"Path": (path, 2)}}
        if added:
            self.keys[system.REGISTRY_KEY] = {system.PATH_VALUE: (added, 1)}

    def OpenKey(self, root, name, *rest):
        if name not in self.keys:
            raise OSError(name)
        key = mock.MagicMock()
        key.__enter__.return_value = name
        return key

    def QueryValueEx(self, key, name):
        try:
            return self.keys[key][name]
        except KeyError:
            raise OSError(name) from None

    def SetValueEx(self, key, name, _, kind, value):
        self.keys[key][name] = (value, kind)

    def DeleteKey(self, root, name):
        self.keys.pop(name)


class UserPath(unittest.TestCase):
    def remove(self, path, added):
        reg = FakeRegistry(path, added)
        with mock.patch.dict(sys.modules, {"winreg": types.SimpleNamespace(**{
                n: getattr(reg, n) for n in dir(reg) if not n.startswith("_")})}), \
                mock.patch("baton.system._announce_environment_change") as told:
            removed = system.remove_user_path_entry()
        return removed, reg, told

    def test_removes_only_the_folder_it_added(self):
        removed, reg, told = self.remove(
            "C:\\Python\\;C:\\Users\\t\\.local\\bin\\;%USERPROFILE%\\x", "c:\\users\\t\\.local\\bin")
        self.assertEqual(removed, "c:\\users\\t\\.local\\bin")
        self.assertEqual(reg.keys["Environment"]["Path"], ("C:\\Python\\;%USERPROFILE%\\x", 2))
        self.assertNotIn(system.REGISTRY_KEY, reg.keys)
        told.assert_called_once()

    def test_leaves_path_alone_when_baton_added_nothing(self):
        removed, reg, told = self.remove("C:\\Users\\t\\.local\\bin", "")
        self.assertEqual(removed, "")
        self.assertEqual(reg.keys["Environment"]["Path"][0], "C:\\Users\\t\\.local\\bin")
        told.assert_not_called()


class ScriptShell(unittest.TestCase):
    def setUp(self):
        patch = mock.patch("baton.system.windows", return_value=True)
        patch.start()
        self.addCleanup(patch.stop)

    def test_git_bash_when_there(self):
        with mock.patch("baton.system.git_bash", return_value="C:\\Git\\bin\\bash.exe"):
            self.assertEqual(system.script_command("s.sh"), ["C:\\Git\\bin\\bash.exe", "s.sh"])
            self.assertIn("Git Bash", runner.shell_note())

    def test_claude_codes_git_bash_setting_is_used(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_GIT_BASH_PATH": __file__}):
            self.assertEqual(system.git_bash(), __file__)

    def test_powershell_without_git_bash(self):
        with mock.patch("baton.system.git_bash", return_value=""), \
                mock.patch("baton.system.powershell", return_value="pwsh"):
            self.assertEqual(system.script_command("s.ps1"),
                             ["pwsh", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                              "Bypass", "-File", "s.ps1"])
            cfg = {"run_actions": {"enabled": True, "allow": "all"}}
            self.assertIn("Tag the block powershell", runner.limits_note(cfg, "xai"))

    def test_python_is_not_the_store_placeholder(self):
        found = {"python3": "C:\\Users\\t\\AppData\\Local\\Microsoft\\WindowsApps\\python3.exe",
                 "python": "C:\\Python312\\python.exe"}
        with mock.patch("shutil.which", side_effect=found.get), \
                mock.patch("baton.system.git_bash", return_value="C:\\Git\\bin\\bash.exe"):
            self.assertEqual(system.python_command(), "python")
            self.assertIn("Run Python as python, not python3.", runner.shell_note())

    def test_powershell_fences_are_read(self):
        self.assertEqual(extract_fences("```powershell\nGet-ChildItem\n```"), ["Get-ChildItem\n"])

    @unittest.skipUnless(system.shutil.which("pwsh"), "needs pwsh")
    def test_a_powershell_block_really_runs(self):
        import tempfile
        work = tempfile.mkdtemp()
        cfg = {"run_actions": {"enabled": True, "allow": "all", "timeout_seconds": 60}}
        text = "```powershell\nSet-Content -Path made.txt -Value 'hi'\n```"
        with mock.patch("baton.system.git_bash", return_value=""):
            r = runner.run_reply(text, cfg, "xai", cwd=work)
        self.assertTrue(r["ok"], r)
        self.assertEqual(Path(work, "made.txt").read_text().strip(), "hi")


class Lock(unittest.TestCase):
    def test_second_holder_is_turned_away(self):
        import tempfile
        path = Path(tempfile.mkdtemp()) / "x.lock"
        with system.exclusive_lock(path):
            with self.assertRaises(system.Locked):
                with system.exclusive_lock(path):
                    pass
        with system.exclusive_lock(path):
            pass


if __name__ == "__main__":
    unittest.main()
