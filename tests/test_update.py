"""Auto-update: version checks, when a check is due, the background start, the lock,
and what an update runs and records."""
import io
import json
import subprocess
import time
import unittest
from unittest import mock

from baton import update
from baton.config import load, state_dir
from tests.helpers import Home

ON = {"auto_update": {"enabled": True, "interval_hours": 24}}
UPGRADE = ["/bin/uv", "tool", "install", "--force", "--reinstall", "git+x@main"]


def page(text: str):
    r = mock.MagicMock()
    r.__enter__.return_value.read.return_value = text.encode()
    return r


def done(code=0, err=""):
    return subprocess.CompletedProcess([], code, stdout="", stderr=err)


class Versions(unittest.TestCase):
    def test_compares_numerically(self):
        self.assertTrue(update.is_newer("0.10.0", "0.9.9"))
        self.assertTrue(update.is_newer("0.5.0", "0.4.1"))
        self.assertTrue(update.is_newer("1", "0.9"))
        self.assertFalse(update.is_newer("0.5.0", "0.5"))
        self.assertFalse(update.is_newer("0.4.1", "0.5.0"))
        self.assertEqual(update.parse("v1.2.3rc1"), (1, 2, 3))

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            update.parse("main")

    def test_reads_the_version_on_main(self):
        with mock.patch("urllib.request.urlopen", return_value=page('__version__ = "9.1.0"\n')):
            self.assertEqual(update.latest_version(), "9.1.0")
        with mock.patch("urllib.request.urlopen", return_value=page("<html>404</html>")):
            with self.assertRaises(ValueError):
                update.latest_version()


class Options(unittest.TestCase):
    def test_defaults_and_bad_values(self):
        self.assertEqual(update.options({}), {"enabled": True, "interval_hours": 24.0})
        self.assertFalse(update.options({"auto_update": {"enabled": False}})["enabled"])
        self.assertEqual(update.options({"auto_update": {"interval_hours": "x"}})["interval_hours"], 24.0)
        self.assertEqual(update.options({"auto_update": {"interval_hours": 0}})["interval_hours"], 1.0)


class Due(Home):
    def setUp(self):
        super().setUp()
        self.how = mock.patch("baton.package.installer", return_value="uv")
        self.how.start()
        self.addCleanup(self.how.stop)

    def test_due_once_per_interval(self):
        self.assertTrue(update.due(ON))
        update._record(checked_at=time.time())
        self.assertFalse(update.due(ON))
        self.assertTrue(update.due(ON, now=time.time() + 24 * 3600 + 1))

    def test_never_when_off_or_not_pipx_or_uv(self):
        self.assertFalse(update.due({"auto_update": {"enabled": False}}))
        with mock.patch("baton.package.installer", return_value=""):
            self.assertFalse(update.due(ON))

    def test_env_turns_it_off(self):
        with mock.patch.dict("os.environ", {"BATON_AUTO_UPDATE": "0"}):
            self.assertFalse(update.due(load()))

    def test_start_spawns_detached_once(self):
        with mock.patch("subprocess.Popen") as popen:
            self.assertTrue(update.start_if_due(ON))
            self.assertFalse(update.start_if_due(ON))
        popen.assert_called_once()
        args, kw = popen.call_args
        self.assertEqual(args[0][1:], ["-m", "baton", "update", "--auto"])
        self.assertTrue(kw["start_new_session"])
        self.assertEqual(kw["stdout"], subprocess.DEVNULL)
        self.assertEqual(update.last()["outcome"], "checking")

    def test_start_never_raises(self):
        with mock.patch("subprocess.Popen", side_effect=OSError("no fork")):
            self.assertFalse(update.start_if_due(ON))
        self.assertIn("couldn't start", (state_dir() / "handoff.log").read_text())

    def test_disabled_never_spawns(self):
        with mock.patch("subprocess.Popen") as popen:
            self.assertFalse(update.start_if_due({"auto_update": {"enabled": False}}))
        popen.assert_not_called()

    def test_hooks_start_a_check(self):
        from baton import hooks
        with mock.patch("baton.update.start_if_due") as start, \
                mock.patch("sys.stdin", io.StringIO("{}")):
            hooks.main(["prompt"])
        start.assert_called_once()


class Update(Home):
    def setUp(self):
        super().setUp()
        for p in (mock.patch("baton.update.latest_version", return_value="99.0.0"),
                  mock.patch("baton.package.upgrade_command", return_value=UPGRADE),
                  mock.patch("baton.update.hooks_installed", return_value=True)):
            p.start()
            self.addCleanup(p.stop)

    def test_up_to_date_runs_nothing(self):
        with mock.patch("baton.update.latest_version", return_value=update.__version__), \
                mock.patch("subprocess.run") as run:
            r = update.update()
        run.assert_not_called()
        self.assertEqual(r["outcome"], "up to date")
        self.assertEqual(update.last()["outcome"], "up to date")

    def test_newer_reinstalls_then_rehooks_without_the_old_pythonpath(self):
        with mock.patch.dict("os.environ", {"PYTHONPATH": "/old"}), \
                mock.patch("subprocess.run", return_value=done()) as run:
            r = update.update()
        self.assertTrue(r["upgraded"])
        self.assertEqual(run.call_args_list[0][0][0], UPGRADE)
        self.assertEqual(run.call_args_list[1][0][0][1:], ["-m", "baton", "install"])
        for c in run.call_args_list:
            self.assertNotIn("PYTHONPATH", c[1]["env"])
        self.assertIn("-> 99.0.0", update.last()["outcome"])
        self.assertIn("update: updated", (state_dir() / "handoff.log").read_text())

    def test_hooks_left_alone_when_not_installed(self):
        with mock.patch("baton.update.hooks_installed", return_value=False), \
                mock.patch("subprocess.run", return_value=done()) as run:
            update.update()
        self.assertEqual(run.call_count, 1)

    def test_check_only_installs_nothing(self):
        with mock.patch("subprocess.run") as run:
            r = update.update(check_only=True)
        run.assert_not_called()
        self.assertEqual(r["outcome"], "99.0.0 is available")

    def test_failed_install_is_recorded_and_not_retried(self):
        with mock.patch("subprocess.run", return_value=done(1, "boom")), \
                mock.patch("baton.package.installer", return_value="uv"):
            with self.assertRaises(RuntimeError):
                update.update()
            self.assertIn("uv exited 1: boom", update.last()["outcome"])
            self.assertFalse(update.due(ON))

    def test_offline_is_recorded(self):
        with mock.patch("baton.update.latest_version", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                update.update()
        self.assertTrue(update.last()["outcome"].startswith("failed: OSError"))

    def test_lock_keeps_a_second_run_out(self):
        with update._lock():
            with self.assertRaises(update.Busy):
                update.update()
        with mock.patch("subprocess.run", return_value=done()):
            update.update()


class Command(Home):
    def test_check(self):
        with mock.patch("baton.update.latest_version", return_value="99.0.0"):
            rc, out = self.cli("update", "--check")
        self.assertEqual(rc, 0)
        self.assertIn("99.0.0 is available", out)
        with mock.patch("baton.update.latest_version", return_value=update.__version__):
            rc, out = self.cli("update", "--check")
        self.assertIn("up to date", out)

    def test_refuses_without_pipx_or_uv(self):
        with mock.patch("baton.package.upgrade_command", return_value=[]):
            rc, out = self.cli("update")
        self.assertEqual(rc, 1)
        self.assertIn("taylorurl.com/baton", out)

    def test_failure_is_reported(self):
        with mock.patch("baton.package.upgrade_command", return_value=UPGRADE), \
                mock.patch("baton.update.latest_version", side_effect=OSError("offline")):
            rc, out = self.cli("update")
        self.assertEqual(rc, 1)
        self.assertIn("Update failed", out)

    def test_auto_is_silent(self):
        with mock.patch("baton.update.latest_version", side_effect=OSError("offline")):
            rc, out = self.cli("update", "--auto")
        self.assertEqual((rc, out), (0, ""))

    def test_doctor_shows_status(self):
        _, out = self.cli("doctor")
        self.assertIn("Auto-update", out)
        self.cfg_file.parent.mkdir(parents=True, exist_ok=True)
        self.cfg_file.write_text(json.dumps({"auto_update": {"enabled": False}}))
        _, out = self.cli("doctor")
        self.assertIn("Off (auto_update.enabled is false)", out)
        update._record(checked_at=time.time(), outcome="failed: OSError: offline")
        with mock.patch("baton.package.upgrade_command", return_value=UPGRADE):
            self.cfg_file.write_text("{}")
            _, out = self.cli("doctor")
        self.assertIn("failed: OSError: offline", out)


class Setup(Home):
    def test_setup_writes_it_and_reset_restores_it(self):
        self.cli("setup", "--provider", "local", "--yes", "--no-test")
        cfg = json.loads(self.cfg_file.read_text())
        self.assertEqual(cfg["auto_update"], {"enabled": True, "interval_hours": 24})
        cfg["auto_update"] = {"enabled": False, "interval_hours": 6}
        self.cfg_file.write_text(json.dumps(cfg))
        self.cli("setup", "--provider", "local", "--yes", "--no-test")
        self.assertFalse(json.loads(self.cfg_file.read_text())["auto_update"]["enabled"])
        self.cli("setup", "--provider", "local", "--yes", "--no-test", "--reset")
        self.assertTrue(json.loads(self.cfg_file.read_text())["auto_update"]["enabled"])

    def test_install_twice_leaves_settings_alone(self):
        from baton.claude import settings
        self.cli("install")
        before = self.settings.stat().st_mtime_ns
        self.cli("install")
        self.assertEqual(self.settings.stat().st_mtime_ns, before)
        self.assertEqual(settings.backups(self.settings), [])


if __name__ == "__main__":
    unittest.main()
