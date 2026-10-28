"""Blocked-action detection and the reason() gate."""
import os
import tempfile
import unittest
from unittest import mock

STATE = tempfile.mkdtemp()
os.environ["BATON_STATE"] = STATE
os.environ["BATON_JUDGE"] = "0"

from baton import handoff  # noqa: E402
from baton.detect import admits_block  # noqa: E402
from baton.transcript import blocked_calls, current_turn, person_messages  # noqa: E402
from tests.test_detect import DECLINES  # noqa: E402

SUPABASE_TIMEOUT = ('MCP server "Supabase" tool "execute_sql" timed out after 180s. The server asked the user a '
                    'question, and no answer came before the time limit.')
BLOCKED_ERRORS = {
    "supabase_elicitation_timeout": SUPABASE_TIMEOUT,
    "approval_prompt_timed_out": "The approval prompt timed out before anyone answered it.",
    "permission_denied_action": "Permission for this action has been denied. Reason: destructive database write.",
    "hook_blocked": "PreToolUse:Bash hook error: blocked by the PreToolUse hook: no DELETE against production.",
    "auto_mode": "This command was denied by auto mode: it deletes data.",
    "policy": "Blocked by your organization policy.",
}
NOT_BLOCKED_ERRORS = {
    "user_rejected": ("The user doesn't want to proceed with this tool use. The tool use was rejected (eg. if it "
                      "was a file edit, the new_string was NOT written to the file). STOP what you are doing."),
    "user_declined_prompt": "The user declined the confirmation prompt.",
    "network_timeout": "Request timed out after 30s: connect ETIMEDOUT 10.0.0.1:5432",
    "ssh_permission": "git@github.com: Permission denied (publickey).",
    "sql_error": 'ERROR: 42P01: relation "outreach_history" does not exist',
    "empty": "",
}


def call(error, tool="mcp__supabase__execute_sql", inp=None):
    return [{"type": "assistant", "message": {"content": [
                {"type": "tool_use", "id": "t1", "name": tool,
                 "input": inp or {"project_id": "p1", "query": "delete from x;"}}]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                 "content": [{"type": "text", "text": error}]}]}}]


def person(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def assistant(text, **message):
    return {"type": "assistant", "message": dict({"content": [{"type": "text", "text": text}]}, **message)}


def classifier_turn():
    """A turn cut off by the safety classifier, with the notice Claude Code adds after it."""
    return [person("Wipe the staging database."),
            assistant("I", stop_reason="refusal"),
            person("Your response above was stopped by a safety classifier. Please try rephrasing."),
            assistant("Understood.")]


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


class ReasonGate(unittest.TestCase):
    def setUp(self):
        self.cfg = {"enabled": True, "provider": "xai", "providers": {"xai": {"type": "xai"}},
                    "judge": {"enabled": False}, "never_handoff_patterns": [],
                    "context": {"user_messages": 4, "tool_calls": 10}, "run_actions": {"enabled": False}}

    def test_decline_is_handed_off(self):
        self.assertEqual(handoff.reason(DECLINES["wipe_cant_run_it"], [], cfg=self.cfg), {"kind": "decline"})

    def test_topic_is_not(self):
        self.assertIsNone(handoff.reason(
            "Done. The migration ran. For reference, Claude once said \"I can't run it\".", [], cfg=self.cfg))

    def test_about_handoff_skips(self):
        self.assertIsNone(handoff.reason(
            DECLINES["bare_cant"], user="Why didn't the handoff fire?", entries=[], cfg=self.cfg))

    def test_interrupted_turn_skips(self):
        entries = [{"type": "assistant", "message": {"stop_reason": "refusal", "content": [
            {"type": "text", "text": "partial"}]}, "error": "classifier"}]
        self.assertIsNone(handoff.reason(DECLINES["bare_cant"], entries, cfg=self.cfg))


    def test_earlier_classifier_stop_does_not_block_a_later_decline(self):
        entries = (classifier_turn() + [person("Delete the old backups."),
                                        assistant("I'm still not doing it.")])
        self.assertEqual(handoff.reason(DECLINES["bare_cant"], entries, cfg=self.cfg), {"kind": "decline"})

    def test_classifier_stop_this_turn_is_not_handed_off(self):
        entries = [person("Delete the old backups.")] + classifier_turn()[1:]
        self.assertIsNone(handoff.reason(DECLINES["bare_cant"], entries, cfg=self.cfg))

    def test_classifier_notice_is_not_the_persons_message(self):
        entries = classifier_turn()
        self.assertEqual(person_messages(entries), ["Wipe the staging database."])
        self.assertEqual(current_turn(entries), entries)

    def test_earlier_no_does_not_block_a_later_decline(self):
        entries = ([person("Run the migration.")] + call(NOT_BLOCKED_ERRORS["user_rejected"])
                   + [person("Fine, delete the temp files then.")])
        self.assertEqual(handoff.reason(DECLINES["bare_cant"], entries, cfg=self.cfg), {"kind": "decline"})

    def test_no_this_turn_still_skips(self):
        entries = [person("Run the migration.")] + call(NOT_BLOCKED_ERRORS["user_rejected"])
        self.assertIsNone(handoff.reason(DECLINES["bare_cant"], entries, cfg=self.cfg))

    def test_earlier_holdback_does_not_block_a_later_decline(self):
        held = "Holdback: held. This call would write a file. Never hand this off."
        entries = ([person("Downloads I mean")] + call(held, tool="Write", inp={"file_path": "/tmp/x"})
                   + [person("Now delete the temp files.")])
        self.assertEqual(handoff.reason(DECLINES["bare_cant"], entries, cfg=self.cfg), {"kind": "decline"})

    def test_earlier_blocked_call_is_not_this_turns(self):
        entries = [person("Clean the table.")] + call(SUPABASE_TIMEOUT) + [person("What's the weather?")]
        self.assertIsNone(handoff.reason("It didn't run. The approval prompt timed out.", entries, cfg=self.cfg))

    def test_early_exit_is_logged(self):
        entries = [person("Delete the old backups.")] + classifier_turn()[1:]
        with mock.patch("baton.handoff.log") as log:
            handoff.reason(DECLINES["bare_cant"], entries, cfg=self.cfg)
        self.assertIn("stopped by a classifier", " ".join(str(c) for c in log.call_args_list))

    def test_holdback_denial_is_not_handed_off(self):
        held = (
            "Holdback: held. This call would write a file, and the person's message doesn't "
            "tell you to do any work. Never hand this off."
        )
        entries = call(held, tool="Write", inp={"file_path": "/tmp/x", "content": "a"})
        self.assertEqual(blocked_calls(entries), [])
        reply = (
            "I didn't create any files in Downloads. Your Holdback hook blocked the write "
            "because it read \"Downloads I mean\" as just a correction. Holdback says this "
            "one must not be handed off."
        )
        self.assertIsNone(handoff.reason(reply, entries, user="Downloads I mean", cfg=self.cfg))

    def test_user_said_no_skips(self):
        entries = call(NOT_BLOCKED_ERRORS["user_rejected"])
        self.assertIsNone(handoff.reason("I stopped because you said no.", entries, cfg=self.cfg))

    def test_blocked_call_with_admitting_reply(self):
        entries = call(SUPABASE_TIMEOUT)
        r = handoff.reason("It didn't run. The approval prompt timed out.", entries, cfg=self.cfg)
        self.assertIsNotNone(r)
        self.assertEqual(r["kind"], "blocked")
        self.assertEqual(len(r["blocked"]), 1)

    def test_blocked_but_reply_says_done_is_not(self):
        entries = call(SUPABASE_TIMEOUT)
        # admits_block is true for "blocked" words, but if reply claims success without those...
        self.assertFalse(admits_block("Done. The script ran and deleted 0 rows."))
        self.assertIsNone(handoff.reason("Done. The script ran and deleted 0 rows.", entries, cfg=self.cfg))

    def test_judge_yes(self):
        cfg = dict(self.cfg)
        cfg["judge"] = {"enabled": True}
        with mock.patch("baton.judge.judge_enabled", return_value=True), \
             mock.patch("baton.judge.judge_decline",
                        return_value={"declined": True, "task": "delete the file", "why": "refused", "ms": 1}):
            # A reply the word scan misses.
            r = handoff.reason("That request sits outside what I'm set up to do here.", [], cfg=cfg)
            self.assertEqual(r, {"kind": "decline", "task": "delete the file", "by": "judge"})

    def test_judge_no(self):
        cfg = dict(self.cfg)
        cfg["judge"] = {"enabled": True}
        with mock.patch("baton.judge.judge_enabled", return_value=True), \
             mock.patch("baton.judge.judge_decline",
                        return_value={"declined": False, "task": "", "why": "answered", "ms": 1}):
            self.assertIsNone(handoff.reason("All set, the report is in your inbox.", [], cfg=cfg))

    def test_judge_error_falls_back(self):
        cfg = dict(self.cfg)
        cfg["judge"] = {"enabled": True}
        with mock.patch("baton.judge.judge_enabled", return_value=True), \
             mock.patch("baton.judge.judge_decline",
                        return_value={"declined": False, "task": "", "why": "", "ms": 1, "error": "boom"}):
            self.assertIsNone(handoff.reason("All set.", [], cfg=cfg))


if __name__ == "__main__":
    unittest.main()
