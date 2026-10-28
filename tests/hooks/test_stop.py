"""The Stop hook: hands off only what display didn't, and never blocks."""
import unittest
from unittest import mock

from baton.state import clear_displayed, get_displayed, mark_displayed, remember_request
from tests.helpers import (DECLINES, HOLDBACK_REPLY, FakeProvider, handoff_cfg, judge_says,
                          run_hook, turn, write_transcript)


class Stop(unittest.TestCase):
    def setUp(self):
        self.cfg = handoff_cfg()

    def stop(self, session, reply, user="Permanently delete /tmp/x", cfg=None, provider=None,
             declined=True):
        clear_displayed(session)
        remember_request(session, user)
        td, path = write_transcript(turn(user, reply))
        provider = provider or FakeProvider(reply="Deleted /tmp/x.")
        event = {"session_id": session, "transcript_path": str(path), "cwd": td}
        if declined is None:   # the test is watching the real judge call
            rc, out = run_hook("stop", event, cfg=cfg or self.cfg, provider=provider)
        else:
            with judge_says(declined):
                rc, out = run_hook("stop", event, cfg=cfg or self.cfg, provider=provider)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "")  # never a block, so never a "Hook re-prompted Claude" row
        return provider

    def test_decline_is_handed_off_and_recorded(self):
        self.stop("stop-1", DECLINES["still_not_going_to"])
        self.assertEqual(get_displayed("stop-1")["result"], "Deleted /tmp/x.")

    def test_admitted_skip_is_handed_off(self):
        self.stop("stop-2", "I am not allowed to permanently delete files. I skipped baton this time.")
        self.assertEqual(get_displayed("stop-2")["result"], "Deleted /tmp/x.")

    def test_ordinary_reply_is_left_alone(self):
        fake = self.stop("stop-3", "Done. All 42 tests pass.", declined=False)
        self.assertEqual(fake.calls, [])
        self.assertIsNone(get_displayed("stop-3"))

    def test_holdback_held_reply_is_left_alone(self):
        fake = self.stop("stop-hb", HOLDBACK_REPLY, declined=False)
        self.assertEqual(fake.calls, [])
        self.assertIsNone(get_displayed("stop-hb"))

    def test_already_displayed_is_left_alone(self):
        session = "stop-shown"
        mark_displayed(session, "Deleted /tmp/x.")
        td, path = write_transcript(turn("delete it", DECLINES["still_not_going_to"]))
        fake = FakeProvider()
        rc, out = run_hook("stop", {"session_id": session, "transcript_path": str(path)},
                           cfg=self.cfg, provider=fake)
        self.assertEqual((rc, out), (0, ""))
        self.assertEqual(fake.calls, [])

    def test_the_judge_is_asked_once(self):
        verdict = {"declined": False, "task": "", "why": "answered", "ms": 1}
        with mock.patch("baton.judge.enabled", return_value=True), \
                mock.patch("baton.judge.decide", return_value=verdict) as d:
            self.stop("stop-judge", "That sits outside what I do here.",
                      cfg=handoff_cfg(judge={"enabled": True}), declined=None)
        self.assertEqual(d.call_count, 1)

    def test_no_transcript_is_a_no_op(self):
        rc, out = run_hook("stop", {"session_id": "stop-none", "stop_hook_active": True,
                                    "transcript_path": ""})
        self.assertEqual((rc, out), (0, ""))

if __name__ == "__main__":
    unittest.main()
