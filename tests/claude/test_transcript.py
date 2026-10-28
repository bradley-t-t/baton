"""claude.transcript: tool calls that were blocked or timed out."""
import unittest

from baton.claude.transcript import blocked_calls
from tests.helpers import BLOCKED_ERRORS, NOT_BLOCKED_ERRORS, SUPABASE_TIMEOUT, call


class BlockedDetection(unittest.TestCase):
    def test_blocked_errors(self):
        for name, err in BLOCKED_ERRORS.items():
            with self.subTest(name=name):
                self.assertEqual(len(blocked_calls(call(err))), 1, name)

    def test_not_blocked_errors(self):
        for name, err in NOT_BLOCKED_ERRORS.items():
            with self.subTest(name=name):
                self.assertEqual(blocked_calls(call(err)), [], name)

    def test_blocked_then_ran_is_not_blocked(self):
        entries = call(SUPABASE_TIMEOUT) + [
            {"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "t2", "name": "mcp__supabase__execute_sql",
                 "input": {"project_id": "p1", "query": "delete from x;"}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t2", "content": "[]"}]}}]
        self.assertEqual(blocked_calls(entries), [])

    def test_never_handoff_patterns(self):
        held = call(BLOCKED_ERRORS["hook_blocked"], tool="Edit",
                    inp={"file_path": "/home/x/.claude/CLAUDE.md", "old_string": "a", "new_string": "b"})
        self.assertEqual(blocked_calls(held, never_patterns=[r"CLAUDE\.md"]), [])

    def test_holdback_denial_is_not_blocked(self):
        held = ("Holdback: held. This call would write a file, and the person's message doesn't "
                "tell you to do any work. Never hand this off.")
        entries = call(held, tool="Write", inp={"file_path": "/tmp/x", "content": "a"})
        self.assertEqual(blocked_calls(entries), [])

if __name__ == "__main__":
    unittest.main()
