"""runner.result_line: a silent script's prose reads as done, not as in progress."""
import tempfile
import unittest
from pathlib import Path

from baton.providers.base import DEFAULT_SYSTEM
from baton.runner import result_line, run_reply
from tests.helpers import posix_only, run_actions


class ResultLine(unittest.TestCase):
    def check(self, prose, want):
        self.assertEqual(result_line("", prose), want)

    def test_the_real_reply_reads_as_done(self):
        self.check("Permanently deleting diaphany-benzoin-c288.txt from Downloads and Trash.",
                   "Permanently deleted diaphany-benzoin-c288.txt from Downloads and Trash.")

    def test_leading_ing_verbs(self):
        self.check("Deleting ~/Downloads/a.txt", "Deleted ~/Downloads/a.txt.")
        self.check("Creating 5 files in ~/Downloads.", "Created 5 files in ~/Downloads.")
        self.check("Moving it to the Trash...", "Moved it to the Trash.")
        self.check("Removing the folder now.", "Removed the folder.")
        self.check("Writing the config to ~/.foo.", "Wrote the config to ~/.foo.")
        self.check("Running the cleanup.", "Ran the cleanup.")

    def test_a_second_ing_verb_is_done_too(self):
        self.check("Deleting ~/Downloads/a.txt and emptying it from the Trash.",
                   "Deleted ~/Downloads/a.txt and emptied it from the Trash.")

    def test_future_openers(self):
        self.check("I'll permanently delete ~/Downloads/x.txt.",
                   "Permanently deleted ~/Downloads/x.txt.")
        self.check("I will remove the file.", "Removed the file.")
        self.check("Let me create the folder.", "Created the folder.")
        self.check("This will delete the file.", "Deleted the file.")

    def test_courtesy_openers_are_dropped(self):
        self.check("Sure, deleting x.txt.", "Deleted x.txt.")
        self.check("Sure.", "Done.")
        self.check("Okay! Done.", "Done.")

    def test_intro_lines_are_not_shown(self):
        self.check("Run this:", "Done.")
        self.check("Here are the commands:", "Done.")
        self.check("Here's the script.", "Done.")
        self.check("Run this:\nDeleted x.txt.", "Deleted x.txt.")

    def test_what_cannot_be_made_finished_says_done(self):
        self.check("Frobnicating the widget.", "Done.")
        self.check("I will frobnicate it.", "Done.")
        self.check("Deleted x.txt. I'll check the rest later.", "Done.")

    def test_past_tense_is_left_alone(self):
        self.check("Deleted ~/Downloads/x.txt and its Trash copy.",
                   "Deleted ~/Downloads/x.txt and its Trash copy.")
        self.check("Created 5 files.\nThey will be overwritten next time.", "Created 5 files.")

    def test_printed_output_still_wins(self):
        self.assertEqual(result_line("removed x\n", "Deleting x."), "removed x")


@posix_only
class SilentScript(unittest.TestCase):
    def test_rm_with_progressive_prose_reads_as_done(self):
        td = tempfile.mkdtemp()
        f = Path(td) / "diaphany-benzoin-c288.txt"
        f.write_text("x")
        answer = ("Permanently deleting diaphany-benzoin-c288.txt from Downloads and Trash."
                  "\n\n```bash\nrm -f %s\n```" % f)
        out = run_reply(answer, run_actions(), "xai", cwd=td)
        self.assertTrue(out["ok"], out["summary"])
        self.assertFalse(f.exists())
        self.assertEqual(out["summary"],
                         "Permanently deleted diaphany-benzoin-c288.txt from Downloads and Trash.")


class SystemPrompt(unittest.TestCase):
    def test_asks_for_past_tense_after_success(self):
        self.assertIn("only after the script has run successfully", DEFAULT_SYSTEM)
        self.assertIn("past tense", DEFAULT_SYSTEM)
        self.assertIn("'Deleting...'", DEFAULT_SYSTEM)


if __name__ == "__main__":
    unittest.main()
