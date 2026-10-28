"""The UserPromptSubmit hook."""
import unittest

from baton.state import clear_displayed, get_displayed, last_request, mark_displayed
from tests.helpers import run_hook


class Prompt(unittest.TestCase):
    def test_remembers_the_request_and_adds_nothing(self):
        rc, out = run_hook("prompt", {"session_id": "p-1", "prompt": "please wipe the table"})
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), "")
        self.assertEqual(last_request("p-1"), "please wipe the table")

    def test_a_new_request_forgets_the_last_result(self):
        mark_displayed("p-2", "Deleted /tmp/x.")
        run_hook("prompt", {"session_id": "p-2", "prompt": "next thing"})
        self.assertIsNone(get_displayed("p-2"))
        clear_displayed("p-2")

    def test_block_list_prompt(self):
        run_hook("prompt", {"session_id": "p-3",
                            "prompt": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]})
        self.assertEqual(last_request("p-3"), "a\nb")

if __name__ == "__main__":
    unittest.main()
