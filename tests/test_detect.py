"""Decline detection: which replies are refusals, which are not."""
import unittest

from baton.detect import about_handoff, is_decline
from tests.helpers import DECLINES, NOT_DECLINES


class DeclineDetection(unittest.TestCase):
    def test_declines_match(self):
        for name, text in DECLINES.items():
            with self.subTest(name=name):
                self.assertTrue(is_decline(text), name)

    def test_topics_do_not_match(self):
        for name, text in NOT_DECLINES.items():
            with self.subTest(name=name):
                self.assertFalse(is_decline(text), name)

    def test_curly_quotes_normalized(self):
        self.assertTrue(is_decline("I can’t run that."))
        self.assertTrue(is_decline("I won’t do that."))

    def test_about_handoff(self):
        self.assertTrue(about_handoff("Why didn't the handoff fire?"))
        self.assertTrue(about_handoff("Tell me about the baton config."))
        self.assertTrue(about_handoff("Why didn't baton fire on that one?"))
        self.assertTrue(about_handoff("Tell me about the claude-handoff config."))
        self.assertTrue(about_handoff("Is the handback ready?"))
        self.assertFalse(about_handoff("Please delete the file in Downloads."))
        self.assertFalse(about_handoff("Write a scene where she passes the baton."))
        self.assertFalse(about_handoff(
            "Permanently delete /tmp/claude-handoff-test-delete-me.txt"))
        self.assertFalse(about_handoff(
            "Permanently delete /tmp/baton-test-delete-me.txt"))
        self.assertFalse(about_handoff(
            "rm ~/Development/claude-handoff/README.md"))
        self.assertFalse(about_handoff(
            "rm ~/Development/baton/README.md"))
        self.assertFalse(about_handoff("I won't wipe the outreach history."))

if __name__ == "__main__":
    unittest.main()
