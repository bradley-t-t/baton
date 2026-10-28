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



class PromptRemembersOnly(unittest.TestCase):
    def test_prompt_hook_records_without_instructions(self):
        from claude_handoff.state import last_request
        event = {"session_id": "s-step", "prompt": "please wipe the table"}
        with mock.patch("sys.stdin", io.StringIO(json.dumps(event))), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = hooks.main(["prompt"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().strip(), "")
        self.assertEqual(last_request("s-step"), "please wipe the table")


class DisplayHides(unittest.TestCase):
    def test_hides_decline(self):
        from claude_handoff.display import on_display, HIDDEN
        from claude_handoff.state import clear_expectation
        clear_expectation("disp-1")
        event = {
            "session_id": "disp-1",
            "delta": DECLINES["still_not_going_to"],
            "final": True,
            "index": 0,
        }
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = on_display(event)
        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(
            payload["hookSpecificOutput"]["displayContent"], HIDDEN)

    def test_shows_expected_exact(self):
        from claude_handoff.display import on_display
        from claude_handoff.state import clear_expectation, set_expectation
        clear_expectation("disp-2")
        expected = "Deleted /tmp/x.\n(via Grok)"
        set_expectation("disp-2", expected)
        event = {"session_id": "disp-2", "delta": expected, "final": True, "index": 0}
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = on_display(event)
        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["hookSpecificOutput"]["displayContent"], expected.rstrip())

    def test_hides_narrating_mismatch(self):
        from claude_handoff.display import on_display, HIDDEN
        from claude_handoff.state import clear_expectation, set_expectation
        clear_expectation("disp-3")
        expected = "Deleted /tmp/x.\n(via Grok)"
        set_expectation("disp-3", expected)
        narr = "I skipped the handoff. Grok deleted it.\n" + expected
        event = {"session_id": "disp-3", "delta": narr, "final": True, "index": 0}
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = on_display(event)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["hookSpecificOutput"]["displayContent"], HIDDEN)

    def test_passes_ordinary_reply(self):
        from claude_handoff.display import on_display
        from claude_handoff.state import clear_expectation
        clear_expectation("disp-4")
        event = {
            "session_id": "disp-4",
            "delta": "List comprehensions build a new list from an iterable.",
            "final": True,
            "index": 0,
        }
        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            rc = on_display(event)
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue().strip(), "")


class StopRefusalChance(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "enabled": True, "provider": "xai",
            "providers": {"xai": {"type": "xai", "model": "grok-4", "api_key_env": ""}},
            "judge": {"enabled": False}, "never_handoff_patterns": [],
            "context": {"user_messages": 4, "tool_calls": 10},
            "run_actions": {"enabled": False}, "timeout_seconds": 30,
        }

    def _write_transcript(self, entries):
        td = tempfile.mkdtemp()
        path = Path(td) / "t.jsonl"
        with open(path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        return td, path

    def _stop(self, event, provider_reply="Deleted /tmp/x."):
        with mock.patch("claude_handoff.providers.get",
                        return_value=FakeProvider({"name": "xai", "type": "xai"},
                                                  reply=provider_reply)), \
             mock.patch("claude_handoff.hooks.load_config", return_value=self.cfg), \
             mock.patch("sys.stdin", io.StringIO(json.dumps(event))), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = hooks.main(["stop"])
        raw = out.getvalue()
        return rc, (json.loads(raw) if raw.strip() else None)

    def test_first_refusal_is_chance_not_handoff(self):
        from claude_handoff.state import (
            clear_expectation, clear_refusal_chance, clear_said, remember_request,
            get_expectation,
        )
        from claude_handoff.refusal import REFUSAL_CHANCE
        session = "chance-1"
        clear_expectation(session)
        clear_refusal_chance(session)
        clear_said(session)
        remember_request(session, "Permanently delete /tmp/x")
        reply = DECLINES["still_not_going_to"]
        td, path = self._write_transcript([
            {"type": "user", "message": {"content": "Permanently delete /tmp/x"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": reply}]}},
        ])
        event = {"session_id": session, "transcript_path": str(path), "cwd": td}
        rc, payload = self._stop(event)
        self.assertEqual(payload["decision"], "block")
        self.assertEqual(payload["reason"], REFUSAL_CHANCE)
        self.assertIsNone(get_expectation(session))

    def test_second_refusal_hands_off(self):
        from claude_handoff.state import (
            clear_expectation, clear_refusal_chance, clear_said, remember_request,
            bump_refusal_chance, get_expectation,
        )
        session = "chance-2"
        clear_expectation(session)
        clear_refusal_chance(session)
        clear_said(session)
        bump_refusal_chance(session)  # already used first chance
        remember_request(session, "Permanently delete /tmp/x")
        reply = (
            "I am not allowed to permanently delete files. "
            "I skipped claude-handoff this time."
        )
        td, path = self._write_transcript([
            {"type": "user", "message": {"content": "Permanently delete /tmp/x"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": reply}]}},
        ])
        event = {"session_id": session, "transcript_path": str(path), "cwd": td}
        rc, payload = self._stop(event)
        self.assertEqual(payload["decision"], "block")
        self.assertIn("Send ONLY this text, nothing else:", payload["reason"])
        self.assertTrue(payload["reason"].rstrip().endswith("(via Grok)"))
        self.assertIsNotNone(get_expectation(session))

    def test_cleared_allows_stop(self):
        from claude_handoff.state import (
            clear_expectation, clear_refusal_chance, mark_cleared, get_expectation,
        )
        session = "chance-ok"
        clear_expectation(session)
        clear_refusal_chance(session)
        reply = "ping"
        mark_cleared(session, reply)
        td, path = self._write_transcript([
            {"type": "user", "message": {"content": "say ping"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": reply}]}},
        ])
        event = {"session_id": session, "transcript_path": str(path), "cwd": td}
        rc, payload = self._stop(event)
        self.assertEqual(rc, 0)
        self.assertIsNone(payload)
        self.assertIsNone(get_expectation(session))

    def test_said_once_skips_repeat_chance(self):
        """If the chance text was already said this turn, second stop hands off."""
        from claude_handoff.state import (
            clear_expectation, clear_refusal_chance, clear_said, remember_request,
            said_once, get_expectation,
        )
        from claude_handoff.refusal import REFUSAL_CHANCE
        session = "chance-said"
        clear_expectation(session)
        clear_refusal_chance(session)
        clear_said(session)
        remember_request(session, "Permanently delete /tmp/x")
        # Pre-mark the chance as said for this turn fingerprint
        import hashlib
        turn = hashlib.sha1((session + "\0" + "Permanently delete /tmp/x").encode()).hexdigest()[:16]
        self.assertTrue(said_once(session, turn, "refusal-chance", REFUSAL_CHANCE))
        reply = DECLINES["still_not_going_to"]
        td, path = self._write_transcript([
            {"type": "user", "message": {"content": "Permanently delete /tmp/x"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": reply}]}},
        ])
        event = {"session_id": session, "transcript_path": str(path), "cwd": td}
        rc, payload = self._stop(event)
        # First chance bumps n to 1 but said_once is False → falls through to handoff
        self.assertIn("Send ONLY this text", payload["reason"])
        self.assertIsNotNone(get_expectation(session))
