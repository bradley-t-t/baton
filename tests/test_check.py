"""Draft check CLI and prompt-hook check-step injection."""
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

STATE = tempfile.mkdtemp()
os.environ["CLAUDE_HANDOFF_STATE"] = STATE
os.environ["CLAUDE_HANDOFF_JUDGE"] = "0"
CFG = tempfile.mkdtemp()
os.environ["CLAUDE_HANDOFF_CONFIG"] = str(Path(CFG) / "config.json")

from claude_handoff import check as checkmod  # noqa: E402
from claude_handoff import hooks  # noqa: E402
from claude_handoff.check import CLEAR, DO_NOT  # noqa: E402
from claude_handoff.state import is_cleared, get_expectation  # noqa: E402
from tests.test_detect import DECLINES  # noqa: E402
from tests.test_handoff import FakeProvider  # noqa: E402


class DraftCheck(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "enabled": True, "provider": "xai",
            "providers": {"xai": {"type": "xai", "model": "grok-4", "api_key_env": ""}},
            "judge": {"enabled": False}, "never_handoff_patterns": [],
            "context": {"user_messages": 4, "tool_calls": 10},
            "run_actions": {"enabled": False}, "timeout_seconds": 30,
        }
        self.td = tempfile.mkdtemp()

    def _write(self, text):
        path = Path(self.td) / "draft.md"
        path.write_text(text)
        return str(path)

    def test_clear_draft(self):
        path = self._write("The migration ran cleanly and all 42 tests pass.")
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            rc = checkmod.check(path, session="chk-clear", cwd=self.td, cfg=self.cfg)
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip(), CLEAR)
        self.assertTrue(is_cleared("chk-clear",
                                   "The migration ran cleanly and all 42 tests pass."))

    def test_decline_prints_do_not_send(self):
        path = self._write(DECLINES["still_not_going_to"])
        fake = FakeProvider({"name": "xai", "type": "xai"},
                            reply="Deleted /tmp/ch-draft-gate-test.txt.")
        buf = io.StringIO()
        with mock.patch("claude_handoff.providers.get", return_value=fake), \
             mock.patch("sys.stdout", buf):
            rc = checkmod.check(path, session="chk-decl", cwd=self.td, cfg=self.cfg,
                                transcript="")
        out = buf.getvalue()
        self.assertEqual(rc, 0)
        self.assertTrue(out.startswith(DO_NOT), out[:200])
        self.assertIn("Deleted /tmp/ch-draft-gate-test.txt.", out)
        self.assertTrue(out.rstrip().endswith("(via Grok)"))
        self.assertNotIn("Handoff result", out)
        exp = get_expectation("chk-decl")
        self.assertIsNotNone(exp)
        self.assertTrue(exp["expected"].endswith("(via Grok)"))

    def test_holdback_held_draft_is_clear(self):
        draft = (
            "I didn't create any files. Your Holdback hook blocked the write "
            "because it read \"Downloads I mean\" as just a correction. "
            "Holdback says this one must not be handed off."
        )
        path = self._write(draft)
        buf = io.StringIO()
        with mock.patch("claude_handoff.providers.get") as g, \
             mock.patch("sys.stdout", buf):
            rc = checkmod.check(path, session="chk-hb", cwd=self.td, cfg=self.cfg)
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip(), CLEAR)
        g.assert_not_called()

    def test_provider_failure_exits_1(self):
        path = self._write(DECLINES["bare_cant"])

        class Boom(FakeProvider):
            def complete(self, *a, **k):
                raise RuntimeError("no key")

        err = io.StringIO()
        out = io.StringIO()
        with mock.patch("claude_handoff.providers.get",
                        return_value=Boom({"name": "xai", "type": "xai"})), \
             mock.patch("sys.stdout", out), mock.patch("sys.stderr", err):
            rc = checkmod.check(path, session="chk-fail", cwd=self.td, cfg=self.cfg)
        self.assertEqual(rc, 1)
        self.assertIn("handoff failed", (out.getvalue() + err.getvalue()).lower())


class PromptInjectsStep(unittest.TestCase):
    def test_prompt_hook_emits_additional_context(self):
        event = {"session_id": "s-step", "prompt": "please wipe the table"}
        with mock.patch("sys.stdin", io.StringIO(json.dumps(event))), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = hooks.main(["prompt"])
        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        ctx = payload["hookSpecificOutput"]["additionalContext"]
        self.assertIn("claude-handoff first", ctx.lower())
        self.assertIn("600000", ctx)
        self.assertIn("do NOT send your draft", ctx)
        self.assertIn("clear. Send this draft word for word", ctx)
        # Command should be concrete, not the placeholder.
        self.assertNotIn("{check}", ctx)
        self.assertTrue(
            "claude-handoff-check" in ctx or "claude_handoff.cli check" in ctx,
            ctx[:300],
        )


if __name__ == "__main__":
    unittest.main()
