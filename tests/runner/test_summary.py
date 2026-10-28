"""runner.summary: what a script did, in plain sentences."""
import unittest
from os.path import join

from baton.runner.summary import clean_prose, describe, failure_line, outcome


class Summary(unittest.TestCase):
    def test_outcome_reads_each_part_of_a_chain(self):
        o = outcome(["rm f && ls dir | grep f || echo ok", "mkdir -p d", "mv a b"], cwd="/w")
        self.assertEqual(o["deleted"], [join("/w", "f")])
        self.assertEqual(o["created"], [join("/w", "d")])
        self.assertEqual(o["moved"], [(join("/w", "a"), join("/w", "b"))])

    def test_prose_shell_lines_dropped(self):
        self.assertEqual(clean_prose("rm -f x && ls y | grep x || echo ok\nAll set."), "All set.")

    def test_no_effects_falls_back_to_prose_then_done(self):
        self.assertEqual(describe(["ls"], prose="The folder has 3 files."), "The folder has 3 files.")
        self.assertEqual(describe(["ls"], prose="Sure, here you go."), "Done.")

    def test_failure_line_has_no_command_text(self):
        self.assertEqual(failure_line("bash: line 2: nope: command not found", 127),
                         "It failed: nope: command not found.")
        self.assertEqual(failure_line("", 3), "It failed (exit 3).")

if __name__ == "__main__":
    unittest.main()
