"""Turns Anthropic's safeguards stopped are handed off; interrupts and other API errors
are not. The fixture is a real session (redacted): the request, the classifier notice,
the synthetic "Your response above was stopped" message, a refusal, Claude Code's
model_refusal_no_fallback record and the "API Error: ... safeguards flagged" text, then
an ordinary second turn."""
import json
import unittest
from pathlib import Path
from unittest import mock

from baton import handoff
from baton.claude.transcript import current_turn, last_reply, person_messages, turn_end
from baton.state import clear_displayed, get_displayed, remember_request
from tests.helpers import (DECLINES, FakeProvider, call, handoff_cfg, judge_says, run_hook, shown,
                          write_transcript)

FIXTURE = Path(__file__).parent / "fixtures" / "safeguards_refusal.jsonl"
SESSION = [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]
STOPPED = SESSION[:6]          # the request through the API error that ended the turn
PROMPT = SESSION[0]["message"]["content"].strip()
ERROR_TEXT = SESSION[5]["message"]["content"][0]["text"]
ANSWER = "Tether's guard runs before a tool call, after the text has already been drawn."


def person(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def assistant(text="", **message):
    content = [{"type": "text", "text": text}] if text else [{"type": "thinking", "thinking": ""}]
    return {"type": "assistant", "message": dict({"content": content}, **message)}


def api_error(text, error):
    return dict(assistant(text, model="<synthetic>", stop_reason="stop_sequence"),
                isApiErrorMessage=True, error=error)


NOT_SAFEGUARDS = {
    "overloaded": "API Error: Overloaded",
    "rate_limit": "API Error: Rate limit reached",
    "unknown": "API Error: Connection error.",
    "authentication_failed": "API Error: 401 Invalid authentication credentials · Please run /login",
}


class Fixture(unittest.TestCase):
    def setUp(self):
        self.cfg = handoff_cfg()

    def test_the_turn_ends_in_safeguards(self):
        self.assertEqual(turn_end(current_turn(STOPPED), ERROR_TEXT), "safeguards")
        self.assertEqual(turn_end(current_turn(STOPPED)), "safeguards")

    def test_the_request_is_the_persons_message_not_the_stop_notice(self):
        self.assertEqual(person_messages(STOPPED), [PROMPT])
        self.assertEqual(current_turn(STOPPED)[0], SESSION[0])

    def test_reason_is_safeguards(self):
        self.assertEqual(handoff.reason(ERROR_TEXT, STOPPED, cfg=self.cfg), {"kind": "safeguards"})

    def test_respond_sends_the_original_request(self):
        fake = FakeProvider(reply=ANSWER)
        with mock.patch("baton.providers.get", return_value=fake), \
                mock.patch("baton.handoff.log") as log:
            out = handoff.respond(ERROR_TEXT, STOPPED, "", "/tmp", self.cfg)
        self.assertEqual(out, ANSWER)
        self.assertEqual(fake.calls[0]["task"], PROMPT)
        self.assertNotIn("stopped by a safety classifier", fake.calls[0]["task"])
        self.assertIn("safeguards", fake.calls[0]["context"]["stopped"])
        logged = " ".join(str(c) for c in log.call_args_list)
        self.assertRegex(logged, r"sending \w+ kind=safeguards provider=")

    def test_the_turn_after_is_judged_normally(self):
        # Every later request repeats the stop notice in an api-request-blob entry. That
        # used to read as this turn being stopped too.
        turn = current_turn(SESSION)
        self.assertEqual(person_messages(turn), ["What?"])
        self.assertIsNone(turn_end(turn, last_reply(SESSION)))
        with judge_says(False):
            self.assertIsNone(handoff.reason(last_reply(SESSION), SESSION, cfg=self.cfg))
        with judge_says(True):
            self.assertEqual(handoff.reason(DECLINES["bare_cant"], SESSION, cfg=self.cfg),
                             {"kind": "decline", "task": "", "by": "judge"})


class Display(unittest.TestCase):
    """MessageDisplay is what draws the answer: on an API-error turn Claude Code runs
    StopFailure, not Stop, and the API error text is the message display gets."""

    def display(self, session, entries, text, provider, remember=True):
        clear_displayed(session)
        if remember:
            remember_request(session, PROMPT)
        td, path = write_transcript(entries)
        event = {"session_id": session, "transcript_path": str(path), "cwd": td,
                 "delta": text, "final": True, "index": 0}
        rc, out = run_hook("display", event, cfg=handoff_cfg(), provider=provider)
        self.assertEqual(rc, 0)
        return shown(out)

    def test_the_api_error_is_replaced_by_the_answer(self):
        fake = FakeProvider(reply=ANSWER)
        self.assertEqual(self.display("sg-1", STOPPED, ERROR_TEXT, fake), ANSWER)
        self.assertEqual(fake.calls[0]["task"], PROMPT)
        self.assertEqual(get_displayed("sg-1")["result"], ANSWER)

    def test_without_the_prompt_hooks_copy_the_transcript_gives_the_request(self):
        fake = FakeProvider(reply=ANSWER)
        self.assertEqual(self.display("sg-2", STOPPED, ERROR_TEXT, fake, remember=False), ANSWER)
        self.assertEqual(fake.calls[0]["task"], PROMPT)

    def test_the_error_text_is_enough_when_the_transcript_lags(self):
        fake = FakeProvider(reply=ANSWER)
        self.assertEqual(self.display("sg-3", SESSION[:3], ERROR_TEXT, fake), ANSWER)

    def test_other_api_errors_are_drawn_as_is(self):
        for error, text in NOT_SAFEGUARDS.items():
            fake = FakeProvider(reply=ANSWER)
            entries = [person("Run the migration."), api_error(text, error)]
            self.assertIsNone(self.display("sg-err-" + error, entries, text, fake), error)
            self.assertEqual(fake.calls, [], error)


class Stop(unittest.TestCase):
    def test_a_stop_hook_on_the_fixture_hands_off_too(self):
        clear_displayed("sg-stop")
        remember_request("sg-stop", PROMPT)
        td, path = write_transcript(STOPPED)
        fake = FakeProvider(reply=ANSWER)
        rc, out = run_hook("stop", {"session_id": "sg-stop", "transcript_path": str(path), "cwd": td},
                           cfg=handoff_cfg(), provider=fake)
        self.assertEqual((rc, out), (0, ""))
        self.assertEqual(get_displayed("sg-stop")["result"], ANSWER)


class TurnEnd(unittest.TestCase):
    def setUp(self):
        self.cfg = handoff_cfg()

    def reason(self, reply, entries, declined=True, **kw):
        with judge_says(declined):
            return handoff.reason(reply, entries, cfg=self.cfg, **kw)

    def test_refusal_without_an_error_message(self):
        entries = [person("Explain the hook."), assistant(stop_reason="refusal")]
        self.assertEqual(turn_end(entries), "safeguards")
        entries = [person("Explain the hook."),
                   {"type": "system", "subtype": "model_refusal_no_fallback", "content": ""}]
        self.assertEqual(turn_end(entries), "safeguards")

    def test_a_retry_under_way_is_not_a_stop(self):
        entries = SESSION[:3]   # the notice says "continuing once with that noted"
        self.assertIsNone(turn_end(entries, "Here is the explanation."))
        self.assertIsNone(self.reason("Here is the explanation.", entries, declined=False))
        self.assertEqual(self.reason(DECLINES["bare_cant"], entries),
                         {"kind": "decline", "task": "", "by": "judge"})

    def test_a_retry_that_answered_is_not_handed_off(self):
        entries = SESSION[:3] + [assistant("Here is the explanation.", stop_reason="end_turn")]
        self.assertIsNone(self.reason("Here is the explanation.", entries, declined=False))

    def test_a_notice_without_a_retry_is_a_stop(self):
        entries = [person("Explain the hook."),
                   {"type": "system", "subtype": "informational", "level": "notice",
                    "content": "Opus 5's safeguards stopped the response above"}]
        self.assertEqual(turn_end(entries), "safeguards")

    def test_user_interrupts_are_not_handed_off(self):
        for note in ("[Request interrupted by user]", "[Request interrupted by user for tool use]"):
            entries = [person("Delete the old backups."), assistant("I won't"), person(note)]
            self.assertEqual(turn_end(entries), "interrupted")
            with mock.patch("baton.handoff.log") as log:
                self.assertIsNone(self.reason(DECLINES["bare_cant"], entries))
            self.assertIn("interrupted", str(log.call_args_list))

    def test_other_api_errors_are_not_handed_off(self):
        for error, text in NOT_SAFEGUARDS.items():
            entries = [person("Run the migration."), api_error(text, error)]
            self.assertEqual(turn_end(entries, text), "error", error)
            self.assertIsNone(self.reason(text, entries), error)
            self.assertIsNone(self.reason(DECLINES["bare_cant"], entries), error)

    def test_max_tokens_is_not_handed_off(self):
        entries = [person("Write it all."), assistant("I can't fit", stop_reason="max_tokens")]
        self.assertIsNone(self.reason(DECLINES["bare_cant"], entries))

    def test_the_usual_gates_still_apply(self):
        self.assertIsNone(self.reason(ERROR_TEXT, STOPPED, user="Why didn't the handoff fire?"))
        self.assertIsNone(handoff.reason(ERROR_TEXT, STOPPED, cfg=handoff_cfg(provider="")))
        from tests.helpers import NOT_BLOCKED_ERRORS
        said_no = [SESSION[0]] + call(NOT_BLOCKED_ERRORS["user_rejected"]) + SESSION[1:6]
        self.assertIsNone(self.reason(ERROR_TEXT, said_no))
        held = "Holdback: held. This call would write a file. Never hand this off."
        holdback = [SESSION[0]] + call(held, tool="Write", inp={"file_path": "/tmp/x"}) + SESSION[1:6]
        self.assertIsNone(self.reason(ERROR_TEXT, holdback))


if __name__ == "__main__":
    unittest.main()
