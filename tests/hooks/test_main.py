"""python3 -m baton.hooks <name>: the entry point installed hooks run."""
import contextlib
import io
import unittest

from baton import hooks


class Main(unittest.TestCase):
    def run_main(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = hooks.main(argv)
        return rc, err.getvalue()

    def test_usage_without_a_name(self):
        for argv in ([], ["-h"], ["--help"]):
            rc, err = self.run_main(argv)
            self.assertEqual(rc, 2)
            self.assertIn("python3 -m baton.hooks {prompt|stop|display}", err)

    def test_unknown_name(self):
        rc, err = self.run_main(["check"])
        self.assertEqual(rc, 2)
        self.assertIn("unknown hook: check", err)

    def test_the_installed_names_exist(self):
        from baton.claude.settings import HOOK_NAMES
        self.assertEqual(set(HOOK_NAMES.values()), set(hooks.HOOKS))

if __name__ == "__main__":
    unittest.main()
