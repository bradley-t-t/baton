"""baton doctor."""
import json
import os
import sys
import unittest

from tests.helpers import Home, posix_only


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

    @posix_only
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
        if "args" in h:  # Windows' exec form
            h["command"] = "/nonexistent/python3"
        else:
            h["command"] = h["command"].replace('"%s"' % sys.executable,
                                                '"/nonexistent/python3"')
        self.settings.write_text(json.dumps(data))
        _, out = self.cli("doctor")
        self.assertIn("/nonexistent/python3 is gone", out)

if __name__ == "__main__":
    unittest.main()
