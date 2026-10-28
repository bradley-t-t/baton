"""handoff: the reason() gate, complete() with a provider, and respond()."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from baton import handoff
from baton.detect import admits_block
from baton.claude.transcript import current_turn, person_messages
from tests.helpers import (
    DECLINES, NOT_BLOCKED_ERRORS, SUPABASE_TIMEOUT, FakeProvider, call, handoff_cfg, posix_only,
    run_actions,
)


def eight(d):
    lines = "".join("printf '%%s\\n' 'line %d' > %s/f%d.txt\n" % (i, d, i) for i in range(1, 9))
    return "```bash\n" + lines + "```\n"


class Reason(unittest.TestCase):
    def setUp(self):
        self.cfg = handoff_cfg()

    def test_decline_is_handed_off(self):
        self.assertEqual(handoff.reason(DECLINES["wipe_cant_run_it"], [], cfg=self.cfg),
                         {"kind": "decline"})

    def test_topic_is_not(self):
        self.assertIsNone(handoff.reason(
            "Done. The migration ran. For reference, Claude once said \"I can't run it\".", [],
            cfg=self.cfg))

    def test_nothing_without_a_provider(self):
        self.assertIsNone(handoff.reason(DECLINES["bare_cant"], [], cfg=handoff_cfg(provider="")))

    def test_about_handoff_skips(self):
        self.assertIsNone(handoff.reason(
            DECLINES["bare_cant"], user="Why didn't the handoff fire?", entries=[], cfg=self.cfg))

    def test_api_error_turn_skips(self):
        entries = [{"type": "assistant", "isApiErrorMessage": True, "error": "overloaded", "message": {
            "content": [{"type": "text", "text": "API Error: Overloaded"}]}}]
        self.assertIsNone(handoff.reason(DECLINES["bare_cant"], entries, cfg=self.cfg))

    def test_holdback_denial_is_not_handed_off(self):
        held = ("Holdback: held. This call would write a file, and the person's message doesn't "
                "tell you to do any work. Never hand this off.")
        entries = call(held, tool="Write", inp={"file_path": "/tmp/x", "content": "a"})
        reply = ("I didn't create any files in Downloads. Your Holdback hook blocked the write "
                 "because it read \"Downloads I mean\" as just a correction. Holdback says this "
                 "one must not be handed off.")
        self.assertIsNone(handoff.reason(reply, entries, user="Downloads I mean", cfg=self.cfg))

    def test_user_said_no_skips(self):
        entries = call(NOT_BLOCKED_ERRORS["user_rejected"])
        self.assertIsNone(handoff.reason("I stopped because you said no.", entries, cfg=self.cfg))

    def test_never_patterns_skip(self):
        cfg = handoff_cfg(never_handoff_patterns=["wipe"])
        self.assertIsNone(handoff.reason(DECLINES["bare_cant"], [], user="wipe the table", cfg=cfg))

    def test_blocked_call_with_admitting_reply(self):
        r = handoff.reason("It didn't run. The approval prompt timed out.", call(SUPABASE_TIMEOUT),
                           cfg=self.cfg)
        self.assertEqual(r["kind"], "blocked")
        self.assertEqual(len(r["blocked"]), 1)

    def test_blocked_but_reply_says_done_is_not(self):
        self.assertFalse(admits_block("Done. The script ran and deleted 0 rows."))
        self.assertIsNone(handoff.reason("Done. The script ran and deleted 0 rows.",
                                         call(SUPABASE_TIMEOUT), cfg=self.cfg))

    def test_admitted_skip_is_handed_off(self):
        r = handoff.reason("Done. I skipped baton this time.", [], cfg=self.cfg)
        self.assertEqual(r, {"kind": "decline", "by": "skipped-check"})

    def test_admitted_skip_still_respects_the_gates(self):
        self.assertIsNone(handoff.reason("I skipped baton this time.", [],
                                         user="why didn't the baton hook fire?", cfg=self.cfg))

    def with_judge(self, verdict, reply, use_judge=True):
        with mock.patch("baton.judge.enabled", return_value=True), \
                mock.patch("baton.judge.decide", return_value=verdict) as d:
            r = handoff.reason(reply, [], cfg=handoff_cfg(judge={"enabled": True}),
                               use_judge=use_judge)
        return r, d

    def test_judge_yes(self):
        r, _ = self.with_judge({"declined": True, "task": "delete the file", "why": "refused",
                                "ms": 1}, "That request sits outside what I'm set up to do here.")
        self.assertEqual(r, {"kind": "decline", "task": "delete the file", "by": "judge"})

    def test_judge_no(self):
        r, _ = self.with_judge({"declined": False, "task": "", "why": "answered", "ms": 1},
                               "All set, the report is in your inbox.")
        self.assertIsNone(r)

    def test_judge_error_falls_back(self):
        r, _ = self.with_judge({"declined": False, "task": "", "why": "", "ms": 1, "error": "boom"},
                               "All set.")
        self.assertIsNone(r)

    def test_judge_can_be_skipped(self):
        r, d = self.with_judge({"declined": True, "task": "", "why": "", "ms": 1},
                               "Looking at the file first.", use_judge=False)
        self.assertIsNone(r)
        d.assert_not_called()


class Respond(unittest.TestCase):
    def setUp(self):
        self.cfg = handoff_cfg()

    def test_hands_off_and_shows_only_the_answer(self):
        fake = FakeProvider()
        with mock.patch("baton.providers.get", return_value=fake):
            out = handoff.respond(DECLINES["downloads_delete_refusal"], [],
                                  "Permanently delete the image in Downloads", "", self.cfg)
        self.assertIn("Deleted IMG_1498.JPEG", out)
        self.assertNotIn("(via", out)
        self.assertNotIn("Handoff result", out)
        self.assertEqual(len(fake.calls), 1)

    def test_skips_non_decline(self):
        with mock.patch("baton.providers.get") as g:
            self.assertIsNone(handoff.respond("All 42 tests pass.", [], "run tests", "", self.cfg))
            g.assert_not_called()

    def test_provider_failure_surfaces(self):
        class Boom(FakeProvider):
            def complete(self, *a, **k):
                raise RuntimeError("no key")
        with mock.patch("baton.providers.get", return_value=Boom()):
            out = handoff.respond(DECLINES["will_not"], [], "sign up", "", self.cfg)
        self.assertEqual(out, "Handoff failed: RuntimeError: no key")

    @posix_only
    def test_commands_run_and_the_result_is_plain(self):
        td = tempfile.mkdtemp()
        target = Path(td) / "handoff-action-delete-me.txt"
        target.write_text("x")
        fake = FakeProvider(reply="Sure.\n```bash\nrm %s\n```\n" % target)
        cfg = handoff_cfg(**run_actions(cap=5))
        with mock.patch("baton.providers.get", return_value=fake):
            out = handoff.respond(DECLINES["still_not_going_to"], [],
                                  "Permanently delete %s" % target, td, cfg)
        self.assertFalse(target.exists())
        self.assertEqual(out, "Deleted %s." % target)


@posix_only
class OverTheCap(unittest.TestCase):
    """What the person sees when the provider's first block is over the cap."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.cfg = handoff_cfg(**run_actions(cap=5))

    def run_with(self, replies):
        fake = FakeProvider()
        seen = []

        def complete(task, context=None, system=""):
            seen.append({"task": task, "system": system})
            return replies[len(seen) - 1]
        fake.complete = complete
        with mock.patch("baton.providers.get", return_value=fake):
            out = handoff.complete({"kind": "decline"}, "I can't do that.", "make 8 files",
                                   self.td, [], self.cfg)
        return out, seen

    def test_system_prompt_states_the_limits(self):
        _, seen = self.run_with(["Done."])
        self.assertIn("at most 5 commands", seen[0]["system"])
        self.assertIn("printf", seen[0]["system"])

    def test_asks_again_and_shows_the_result(self):
        loop = ("```bash\nfor i in 1 2 3 4 5 6 7 8; do printf 'x\\n' > %s/f$i.txt; done\n```"
                % self.td)
        out, seen = self.run_with([eight(self.td), loop])
        self.assertEqual(len(seen), 2)
        self.assertIn("limit is 5", seen[1]["task"])
        self.assertEqual(len(list(Path(self.td).glob("f*.txt"))), 8)
        self.assertEqual(out, "Done.")
        self.assertNotIn("Nothing was run", out)

    def test_twice_says_nothing_ran(self):
        out, seen = self.run_with([eight(self.td)] * 2)
        self.assertEqual(len(seen), 2)
        self.assertEqual(list(Path(self.td).glob("f*.txt")), [])
        self.assertEqual(out, "Nothing was run: the script had 8 commands and the limit "
                              "is 5 (run_actions.max_commands).")


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


class OnlyThisTurn(unittest.TestCase):
    def setUp(self):
        self.cfg = handoff_cfg()

    def reason(self, reply, entries):
        return handoff.reason(reply, entries, cfg=self.cfg, use_judge=False)

    def test_earlier_classifier_stop_does_not_block_a_later_decline(self):
        entries = classifier_turn() + [person("Delete the old backups."), assistant("I'm still not doing it.")]
        self.assertEqual(self.reason(DECLINES["bare_cant"], entries), {"kind": "decline"})

    def test_decline_after_a_classifier_stop_this_turn_is_handed_off(self):
        entries = [person("Delete the old backups.")] + classifier_turn()[1:]
        self.assertEqual(self.reason(DECLINES["bare_cant"], entries), {"kind": "decline"})

    def test_answer_after_a_classifier_stop_this_turn_is_left_alone(self):
        entries = [person("Delete the old backups.")] + classifier_turn()[1:]
        self.assertIsNone(self.reason("Understood.", entries))

    def test_classifier_notice_is_not_the_persons_message(self):
        entries = classifier_turn()
        self.assertEqual(person_messages(entries), ["Wipe the staging database."])
        self.assertEqual(current_turn(entries), entries)

    def test_earlier_no_does_not_block_a_later_decline(self):
        entries = ([person("Run the migration.")] + call(NOT_BLOCKED_ERRORS["user_rejected"])
                   + [person("Fine, delete the temp files then.")])
        self.assertEqual(self.reason(DECLINES["bare_cant"], entries), {"kind": "decline"})

    def test_no_this_turn_still_skips(self):
        entries = [person("Run the migration.")] + call(NOT_BLOCKED_ERRORS["user_rejected"])
        self.assertIsNone(self.reason(DECLINES["bare_cant"], entries))

    def test_earlier_holdback_does_not_block_a_later_decline(self):
        held = "Holdback: held. This call would write a file. Never hand this off."
        entries = ([person("Downloads I mean")] + call(held, tool="Write", inp={"file_path": "/tmp/x"})
                   + [person("Now delete the temp files.")])
        self.assertEqual(self.reason(DECLINES["bare_cant"], entries), {"kind": "decline"})

    def test_earlier_blocked_call_is_not_this_turns(self):
        entries = [person("Clean the table.")] + call(SUPABASE_TIMEOUT) + [person("What's the weather?")]
        self.assertIsNone(self.reason("It didn't run. The approval prompt timed out.", entries))

    def test_early_exit_is_logged(self):
        entries = [person("Delete the old backups."), assistant("I won't"),
                   person("[Request interrupted by user]")]
        with mock.patch("baton.handoff.log") as log:
            self.reason(DECLINES["bare_cant"], entries)
        self.assertIn("interrupted this turn", " ".join(str(c) for c in log.call_args_list))


if __name__ == "__main__":
    unittest.main()
