"""runner: the allow list, the command cap, read-only mode, and running the script."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from baton.runner import allows_all, limits_note, max_commands, run_reply
from tests.helpers import ALLOW, posix_only, run_actions


def eight(d):
    lines = "".join("printf '%%s\\n' 'line %d' > %s/f%d.txt\n" % (i, d, i) for i in range(1, 9))
    return "```bash\n" + lines + "```\n"


@posix_only  # the scripts name absolute POSIX paths
class Run(unittest.TestCase):
    def test_allowlist_rejects(self):
        cfg = {"run_actions": {"enabled": True, "timeout_seconds": 5, "max_commands": 2,
                               "allow": ["ls", "pwd"]}}
        out = run_reply("```bash\nrm -rf /\n```", cfg, "xai")
        self.assertTrue(out["ran"])
        self.assertFalse(out["ok"])
        self.assertIn("not on the allow list", out["summary"].lower())

    def test_flat_allow_list_rm_tmp(self):
        td = tempfile.mkdtemp()
        f = Path(td) / "to-delete.txt"
        f.write_text("bye")
        cfg = {"run_actions": {"enabled": True, "timeout_seconds": 5, "max_commands": 5,
                               "allow": ALLOW}}
        out = run_reply("Deleted it.\n```bash\nrm %s\n```" % f, cfg, "xai", cwd=td)
        self.assertTrue(out["ok"] and out["ran"])
        self.assertFalse(f.exists())
        self.assertEqual(out["summary"], "Deleted it.")


    def test_python_c_quotes_not_mangled(self):
        """Regression: shlex.quote round-trip used to break python3 -c quotes."""
        td = tempfile.mkdtemp()
        target = Path(td) / "out.txt"
        code = "open(r'%s', 'w').write('alpha bravo\\n')" % target
        fence = "```bash\npython3 -c %s\n```\n" % repr(code)
        cfg = {"run_actions": {"enabled": True, "timeout_seconds": 10, "max_commands": 5,
                               "allow": ALLOW}}
        out = run_reply(fence, cfg, "xai", cwd=td)
        self.assertTrue(out["ok"], out["summary"])
        self.assertEqual(target.read_text().strip(), "alpha bravo")

    def test_create_five_files_printf_fence(self):
        td = tempfile.mkdtemp()
        words = ["alpha", "bravo", "charlie", "delta", "echo"]
        lines = ["```bash"]
        for i, w in enumerate(words, 1):
            path = Path(td) / ("ch-test-%d.txt" % i)
            lines.append("printf '%%s\\n' '%s' > %s" % (w, path))
        lines.append("```")
        fence = "\n".join(lines) + "\n"
        cfg = {"run_actions": {"enabled": True, "timeout_seconds": 10, "max_commands": 20,
                               "allow": ALLOW}}
        out = run_reply(fence, cfg, "xai", cwd=td)
        self.assertTrue(out["ok"], out["summary"])
        for i, w in enumerate(words, 1):
            f = Path(td) / ("ch-test-%d.txt" % i)
            self.assertTrue(f.exists())
            self.assertEqual(f.read_text().strip(), w)
        self.assertEqual(out["summary"], "Done.")

    def test_for_loop_fence_runs_verbatim(self):
        td = tempfile.mkdtemp()
        fence = (
            "```bash\n"
            "for i in 1 2 3; do\n"
            "  printf '%%s\\n' \"word$i\" > %s/ch-loop-$i.txt\n"
            "done\n"
            "```\n"
        ) % td
        cfg = {"run_actions": {"enabled": True, "timeout_seconds": 10, "max_commands": 20,
                               "allow": ALLOW}}
        out = run_reply(fence, cfg, "xai", cwd=td)
        self.assertTrue(out["ok"], out["summary"])
        for i in (1, 2, 3):
            self.assertEqual((Path(td) / ("ch-loop-%d.txt" % i)).read_text().strip(),
                             "word%d" % i)

    def test_disabled_is_noop(self):
        out = run_reply("```bash\nls\n```", {"run_actions": {"enabled": False}}, "xai")
        self.assertFalse(out["ran"])
        self.assertEqual(out["summary"], "")


@posix_only
class ShownResult(unittest.TestCase):
    """What the person sees: the script's own output, else the provider's sentence."""

    CFG = {"run_actions": {"enabled": True, "timeout_seconds": 10, "max_commands": 20,
                           "allow": ALLOW + ["grep"]}}

    def test_what_the_script_printed_is_the_result(self):
        td = tempfile.mkdtemp()
        f = Path(td) / "a.txt"
        f.write_text("x")
        fence = "```bash\nrm -f %s && echo permanently deleted\n```" % f
        out = run_reply("Sure.\n" + fence, self.CFG, "xai", cwd=td)
        self.assertTrue(out["ok"], out["summary"])
        self.assertFalse(f.exists())
        self.assertEqual(out["summary"], "permanently deleted")

    def test_a_silent_script_falls_back_to_the_prose(self):
        td = tempfile.mkdtemp()
        f = Path(td) / "a.txt"
        f.write_text("x")
        out = run_reply("Deleted %s.\n```bash\nrm -f %s\n```" % (f, f),
                        self.CFG, "xai", cwd=td)
        self.assertEqual(out["summary"], "Deleted %s." % f)

    def test_a_silent_script_with_no_prose_says_done(self):
        td = tempfile.mkdtemp()
        out = run_reply("```bash\ntouch %s/a.txt\n```" % td, self.CFG, "xai", cwd=td)
        self.assertEqual(out["summary"], "Done.")

    def test_a_failing_script_has_no_command_text(self):
        td = tempfile.mkdtemp()
        out = run_reply("```bash\nrm %s/missing.txt && echo gone\n```" % td,
                        self.CFG, "xai", cwd=td)
        self.assertFalse(out["ok"])
        self.assertTrue(out["summary"].startswith("It failed: "), out["summary"])
        self.assertNotIn("&&", out["summary"])
        self.assertNotIn("echo", out["summary"])
        self.assertEqual(out["summary"].count("\n"), 0)


@posix_only
class Cap(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()

    def test_eight_commands_run_under_default(self):
        out = run_reply(eight(self.td), run_actions(), "xai", cwd=self.td)
        self.assertTrue(out["ran"] and out["ok"], out["summary"])
        self.assertEqual(len(list(Path(self.td).glob("f*.txt"))), 8)
        self.assertEqual(out["summary"], "Done.")

    def test_over_cap_runs_nothing_and_says_so_plainly(self):
        out = run_reply(eight(self.td), run_actions(cap=5), "xai", cwd=self.td)
        self.assertTrue(out["ran"])
        self.assertFalse(out["ok"])
        self.assertEqual(out["not_run"], "too_many")
        self.assertEqual(list(Path(self.td).glob("f*.txt")), [])
        self.assertEqual(out["summary"], "Nothing was run: the script had 8 commands and "
                                         "the limit is 5 (run_actions.max_commands).")
        self.assertNotIn("fence", out["summary"])

    def test_loop_counts_its_body_once(self):
        loop = ("```bash\nfor i in 1 2 3 4 5 6 7 8; do printf 'x\\n' > %s/f$i.txt; done\n```"
                % self.td)
        out = run_reply(loop, run_actions(cap=5), "xai", cwd=self.td)
        self.assertTrue(out["ok"], out["summary"])
        self.assertEqual(len(list(Path(self.td).glob("f*.txt"))), 8)


class AllowEveryCommand(unittest.TestCase):
    def check(self, script):
        out = run_reply("```bash\n%s\n```" % script, run_actions(), "xai",
                                  cwd=tempfile.mkdtemp())
        return out

    def test_chain_after_allowed_command_is_checked(self):
        out = self.check("ls /tmp && curl -s https://example.com")
        self.assertEqual(out.get("not_run"), "not_allowed")
        self.assertIn("curl", out["summary"])

    def test_pipe_and_subshell_are_checked(self):
        self.assertEqual(self.check("ls | nc example.com 80").get("not_run"), "not_allowed")
        self.assertEqual(self.check("ls $(curl -s x)").get("not_run"), "not_allowed")
        self.assertEqual(self.check("ls `curl -s x`").get("not_run"), "not_allowed")

    def test_loop_and_if_bodies_are_checked(self):
        self.assertEqual(self.check("for f in a b; do curl $f; done").get("not_run"),
                         "not_allowed")
        self.assertEqual(self.check("if [ -f /nope ]; then curl x; fi").get("not_run"),
                         "not_allowed")

    def test_test_bracket_counts_as_test(self):
        out = self.check("if [ -d /tmp ]; then true; fi")
        self.assertTrue(out["ok"], out["summary"])


class ReadOnly(unittest.TestCase):
    CFG = {"run_actions": {"enabled": True, "read_only": True,
                           "allow": ["ls", "echo", "cat", "printf"]}}

    def test_redirect_to_a_file_is_refused(self):
        d = tempfile.mkdtemp()
        for script in ("echo hi > %s/f" % d, "printf x >>%s/f" % d, "ls &> %s/f" % d):
            out = run_reply("```bash\n%s\n```" % script, self.CFG, "xai", cwd=d)
            self.assertEqual(out.get("not_run"), "not_allowed", script)
        self.assertEqual(os.listdir(d), [])

    def test_reading_and_dev_null_are_fine(self):
        d = tempfile.mkdtemp()
        out = run_reply("```bash\nls . 2>/dev/null\ncat < /dev/null 2>&1\n```",
                                  self.CFG, "xai", cwd=d)
        self.assertTrue(out["ok"], out["summary"])

    def test_scripts_dont_see_api_keys(self):
        d = tempfile.mkdtemp()
        cfg = {"providers": {"x": {"api_key_env": "MY_KEY"}},
               "run_actions": {"enabled": True, "allow": ["printf"]}}
        with mock.patch.dict(os.environ, {"MY_KEY": "s3cret", "XAI_API_KEY": "s3cret",
                                          "SOME_VAR": "visible"}):
            out = run_reply('```bash\nprintf "%s|%s|%s" "$MY_KEY" "$XAI_API_KEY"'
                                      ' "$SOME_VAR"\n```', cfg, "x", cwd=d)
        self.assertTrue(out["ok"], out["summary"])
        self.assertNotIn("s3cret", json.dumps(out))
        self.assertIn("visible", json.dumps(out))

    def test_background_command_is_checked(self):
        out = run_reply("```bash\nls & rm -rf x\n```", self.CFG, "xai", cwd=tempfile.mkdtemp())
        self.assertEqual(out.get("not_run"), "not_allowed")

    def test_limits_note_mentions_read_only(self):
        self.assertIn("Writing to files", limits_note(self.CFG, "xai"))


class AllCommands(unittest.TestCase):
    CFG = {"run_actions": {"enabled": True, "allow": "all", "max_commands": 3}}

    def test_any_command_runs(self):
        d = tempfile.mkdtemp()
        out = run_reply("```bash\ntouch a && mkdir b\n```", self.CFG, "xai", cwd=d)
        self.assertTrue(out["ok"], out["summary"])
        self.assertEqual(sorted(os.listdir(d)), ["a", "b"])

    def test_cap_still_applies(self):
        d = tempfile.mkdtemp()
        out = run_reply("```bash\ntouch a; touch b; touch c; touch d\n```", self.CFG, "xai", cwd=d)
        self.assertEqual(out.get("not_run"), "too_many")
        self.assertEqual(os.listdir(d), [])

    def test_all_spellings(self):
        for allow in ("all", "ALL", "*", ["all"], ["*"], {"xai": "all"}, {"*": "all"}):
            self.assertTrue(allows_all({"run_actions": {"allow": allow}}, "xai"), allow)
        for allow in ([], ["ls"], "", None, {"openai": "all"}):
            self.assertFalse(allows_all({"run_actions": {"allow": allow}}, "xai"), allow)

    def test_limits_note_has_no_list(self):
        note = limits_note(self.CFG, "xai")
        self.assertIn("at most 3 commands", note)
        self.assertNotIn("has to be one of", note)

    def test_off_still_means_off(self):
        cfg = {"run_actions": {"enabled": False, "allow": "all"}}
        out = run_reply("```bash\ntouch a\n```", cfg, "xai", cwd=tempfile.mkdtemp())
        self.assertFalse(out["ran"])

    def test_max_commands_defaults(self):
        self.assertEqual(max_commands({}), 20)
        self.assertEqual(max_commands({"run_actions": {"max_commands": "x"}}), 20)
        self.assertEqual(max_commands({"run_actions": {"max_commands": 0}}), 20)
        self.assertEqual(max_commands({"run_actions": {"max_commands": -3}}), 1)

if __name__ == "__main__":
    unittest.main()
