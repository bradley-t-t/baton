"""Auto-update: version checks, when a check is due, the background start, the lock,
and what an update runs and records."""
import email.message
import io
import os
import subprocess
import sys
import time
import unittest
import urllib.error
from unittest import mock

from baton import update
from baton.config import state_dir
from tests.helpers import Home, posix_only

UPGRADE = ["/bin/uv", "tool", "install", "--force", "--reinstall", "git+x@main"]


def page(text: str, etag: str = ""):
    r = mock.MagicMock()
    r.__enter__.return_value.read.return_value = text.encode()
    r.__enter__.return_value.headers = {"ETag": etag} if etag else {}
    return r


def http_error(code: int):
    return urllib.error.HTTPError("https://x", code, "status %d" % code, email.message.Message(), None)


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
        with mock.patch("urllib.request.urlopen", return_value=page('__version__ = "9.1.0"\n', '"e1"')) as get:
            self.assertEqual(update.latest_version(), ("9.1.0", '"e1"'))
        req = get.call_args[0][0]
        self.assertEqual(req.full_url, update.API_URL)
        self.assertIsNone(req.get_header("If-none-match"))
        with mock.patch("urllib.request.urlopen", return_value=page("<html>404</html>")):
            with self.assertRaises(ValueError):
                update.latest_version()

    def test_unchanged_main_is_a_304_that_keeps_the_last_version(self):
        cached = {"latest": "9.1.0", "etag": '"e1"'}
        with mock.patch("urllib.request.urlopen", side_effect=http_error(304)) as get:
            self.assertEqual(update.latest_version(cached), ("9.1.0", '"e1"'))
        get.assert_called_once()
        self.assertEqual(get.call_args[0][0].get_header("If-none-match"), '"e1"')

    def test_rate_limited_api_falls_back_to_the_raw_file(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=[http_error(403), page('__version__ = "9.2.0"\n')]) as get:
            self.assertEqual(update.latest_version({"latest": "9.1.0", "etag": '"e1"'}), ("9.2.0", ""))
        self.assertEqual(get.call_args[0][0].full_url, update.RAW_URL)

    def test_other_http_errors_fail_the_check(self):
        with mock.patch("urllib.request.urlopen", side_effect=http_error(500)):
            with self.assertRaises(urllib.error.HTTPError):
                update.latest_version()

    def test_urls_point_at_main(self):
        self.assertEqual(update.API_URL, "https://api.github.com/repos/bradley-t-t/baton/"
                                         "contents/src/baton/__init__.py?ref=main")
        self.assertEqual(update.RAW_URL, "https://raw.githubusercontent.com/bradley-t-t/baton/"
                                         "main/src/baton/__init__.py")


class Due(Home):
    def setUp(self):
        super().setUp()
        self.how = mock.patch("baton.package.installer", return_value="uv")
        self.how.start()
        self.addCleanup(self.how.stop)

    def test_due_at_most_once_a_minute(self):
        self.assertTrue(update.due())
        now = time.time()
        update._record(checked_at=now, outcome="up to date")
        self.assertFalse(update.due(now=now + 59))
        self.assertTrue(update.due(now=now + 60))

    def test_failures_back_off_minutes_doubling_to_an_hour(self):
        now = time.time()
        for failures, wait in ((1, 300), (2, 600), (3, 1200), (5, 3600), (9, 3600)):
            update._record(checked_at=now, outcome="failed: OSError: offline", failures=failures)
            self.assertFalse(update.due(now=now + wait - 1), failures)
            self.assertTrue(update.due(now=now + wait), failures)

    def test_waits_while_a_windows_reinstall_finishes(self):
        now = time.time()
        update._record(checked_at=now, outcome="updating 0.6.0 -> 0.6.1 once Baton exits")
        self.assertFalse(update.due(now=now + 300))
        self.assertTrue(update.due(now=now + update.UPGRADE_TIMEOUT))

    def test_no_check_starts_while_an_update_holds_the_lock(self):
        with mock.patch("subprocess.Popen") as popen:
            with update._lock():
                self.assertTrue(update.running())
                self.assertFalse(update.start_if_due())
            self.assertFalse(update.running())
        popen.assert_not_called()
        self.assertNotIn("checked_at", update.last())

    def test_never_when_not_installed_with_pipx_or_uv(self):
        with mock.patch("baton.package.installer", return_value=""):
            self.assertFalse(update.due())

    @posix_only
    def test_start_spawns_detached_once(self):
        with mock.patch("subprocess.Popen") as popen:
            self.assertTrue(update.start_if_due())
            self.assertFalse(update.start_if_due())
        popen.assert_called_once()
        args, kw = popen.call_args
        self.assertEqual(args[0][1:], ["-m", "baton", "update", "--auto"])
        self.assertTrue(kw["start_new_session"])
        self.assertEqual(kw["stdout"], subprocess.DEVNULL)
        self.assertEqual(update.last()["outcome"], "checking")

    def test_start_never_raises(self):
        with mock.patch("subprocess.Popen", side_effect=OSError("no fork")):
            self.assertFalse(update.start_if_due())
        self.assertIn("couldn't start", (state_dir() / "handoff.log").read_text())

    def test_hooks_start_a_check(self):
        from baton import hooks
        with mock.patch("baton.update.start_if_due") as start, \
                mock.patch("sys.stdin", io.StringIO("{}")):
            hooks.main(["prompt"])
        start.assert_called_once()


class Update(Home):
    def setUp(self):
        super().setUp()
        for p in (mock.patch("baton.update.latest_version", return_value=("99.0.0", "")),
                  mock.patch("baton.package.upgrade_command", return_value=UPGRADE),
                  mock.patch("baton.update.hooks_installed", return_value=True)):
            p.start()
            self.addCleanup(p.stop)

    def test_up_to_date_runs_nothing(self):
        with mock.patch("baton.update.latest_version", return_value=(update.__version__, "")), \
                mock.patch("subprocess.run") as run:
            r = update.update()
        run.assert_not_called()
        self.assertEqual(r["outcome"], "up to date")
        self.assertEqual(update.last()["outcome"], "up to date")

    @posix_only
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

    @posix_only
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
        with mock.patch("baton.package.installer", return_value="uv"):
            self.assertTrue(update.due())

    @posix_only
    def test_failed_install_is_recorded_and_retried_after_a_backoff(self):
        with mock.patch("subprocess.run", return_value=done(1, "boom")), \
                mock.patch("baton.package.installer", return_value="uv"):
            for n in (1, 2):
                with self.assertRaises(RuntimeError):
                    update.update()
                rec = update.last()
                self.assertIn("uv exited 1: boom", rec["outcome"])
                self.assertEqual(rec["failures"], n)
            self.assertFalse(update.due())
            self.assertTrue(update.due(now=rec["checked_at"] + 600))
        with mock.patch("subprocess.run", return_value=done()):
            update.update()
        self.assertEqual(update.last()["failures"], 0)

    @posix_only
    def test_an_update_in_progress_is_recorded(self):
        seen = []
        with mock.patch("subprocess.run", side_effect=lambda *a, **k: seen.append(
                update.last()["outcome"]) or done()):
            update.update()
        self.assertEqual(seen[0], "updating %s -> 99.0.0" % update.__version__)

    def test_etag_is_kept_for_the_next_check(self):
        with mock.patch("baton.update.latest_version",
                        return_value=(update.__version__, '"e2"')) as latest:
            update.update()
            update.update()
        self.assertEqual(update.last()["etag"], '"e2"')
        self.assertEqual(latest.call_args[0][0]["etag"], '"e2"')

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
        with mock.patch("baton.update.latest_version", return_value=("99.0.0", "")):
            rc, out = self.cli("update", "--check")
        self.assertEqual(rc, 0)
        self.assertIn("99.0.0 is available", out)
        with mock.patch("baton.update.latest_version", return_value=(update.__version__, "")):
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

    def test_doctor_shows_versions_and_the_last_check(self):
        _, out = self.cli("doctor")
        self.assertIn("Version %s" % update.__version__, out)
        self.assertIn("can't update itself", out)
        with mock.patch("baton.package.upgrade_command", return_value=UPGRADE):
            _, out = self.cli("doctor")
            self.assertIn("Not checked for updates yet", out)
            self.assertIn("once a minute", out)
            update._record(checked_at=time.time(), latest="99.0.0",
                           outcome="failed: OSError: offline")
            _, out = self.cli("doctor")
        self.assertIn("latest 99.0.0", out)
        self.assertIn("failed: OSError: offline", out)
        self.assertIn("next try after", out)


class Settings(Home):
    def test_install_twice_leaves_settings_alone(self):
        from baton.claude import settings
        self.cli("install")
        before = self.settings.stat().st_mtime_ns
        self.cli("install")
        self.assertEqual(self.settings.stat().st_mtime_ns, before)
        self.assertEqual(settings.backups(self.settings), [])

    @posix_only
    def test_a_hook_during_a_reinstall_exits_0(self):
        """pipx and uv remove the old copy before the new one is in place. A hook that
        starts then finds no Python or no package, and must still exit 0 with nothing on
        stdout, so Claude Code goes on as if there were no hook."""
        from baton.claude import settings
        for missing in ("python", "package"):
            with mock.patch("sys.executable", "/gone/bin/python3" if missing == "python"
                            else sys.executable), \
                    mock.patch("baton.claude.settings.package_root", return_value="/gone"):
                cmd = settings.hook_command("Stop")
            env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
            p = subprocess.run(["sh", "-c", cmd], input="{}", capture_output=True, text=True,
                               cwd="/", env=env, timeout=30)
            self.assertEqual((p.returncode, p.stdout), (0, ""), missing)


if __name__ == "__main__":
    unittest.main()
