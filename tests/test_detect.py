"""Decline detection: which replies are refusals, which are not."""
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = tempfile.mkdtemp()
os.environ["CLAUDE_HANDOFF_STATE"] = STATE
os.environ["CLAUDE_HANDOFF_JUDGE"] = "0"

from claude_handoff.detect import about_handoff, is_decline  # noqa: E402


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name)) as f:
        return f.read()


DECLINES = {
    "wipe_cant_run_it": (
        "I can’t run it. wipe.sql permanently deletes rows from your production database, and "
        "rerunning a destructive delete script isn’t something I’ll do, even if you expect it to be a no-op."),
    "wipe_safety_block": (
        "I’m going to stop here. Running wipe.sql hits a safety block on my side: it’s an irreversible "
        "delete against a live database, so I won’t execute it. If you want to confirm it does nothing, "
        "you can run it yourself with `psql -f wipe.sql`."),
    "wipe_safety_block_prevents_me": (
        "A safety block prevents me from running wipe.sql against your database. You can run it directly "
        "from your terminal if you’re sure."),
    "outreach_history": (
        "I’m not comfortable wiping your old outreach history. Deleting those records is permanent and "
        "can’t be undone, so I won’t run the delete. If you still want it gone, here’s the query "
        "you could run yourself:\n\n```sql\nDELETE FROM outreach WHERE sent_at < now() - interval '1 year';\n```"),
    "rather_not": "I'd rather not drop that table without a backup. Want me to export it first?",
    "blocked_from": "I'm blocked from running destructive commands on this database, so I can't do that one.",
    "downloads_delete_refusal": fixture("downloads_delete_refusal.md"),
    "downloads_delete_refusal_2": fixture("downloads_delete_refusal_2.md"),
    "still_not_going_to": "No, I'm still not going to permanently delete it. It's a fixed line for me.",
    "fixed_line": "That's a fixed line for me.",
    "asking_again": "Asking again won't change it.",
    "not_handing_it": "I'm not handing it to another assistant either.",
    "wont_with_adverb": "I won't permanently remove that branch.",
    "leave_to_you": "Force-pushing to main is something I leave to you.",
    "few_things": "Creating accounts is one of the few things I don't do, even when asked.",
    "against_guidelines": "That goes against my guidelines, so I'll stop here.",
    "hard_limit": "Typing your password into that page is one of my hard limits.",
    "off_limits": "Sending money is off-limits for me, even when you ask.",
    "not_going_to": "I'm not going to submit that form.",
    "will_not": "I will not sign up for that account.",
    "bare_cant": "Sorry, I can't.",
    "feel_comfortable": "I don't feel comfortable buying that without a second look.",
    "do_it_yourself": "You'll need to delete the file yourself.",
    "respectfully_decline": "I have to respectfully decline running that script.",
    "pass_on": "I'll pass on posting that publicly.",
    "isnt_something_ill_do": "Bypassing the captcha isn't something I'll do.",
    "moral_objection": "I'm not willing to write that review, it would mislead people.",
    "decline_long_but_dominant": (
        "I need to decline this one.\n\n" + ("Here is some context about the table layout. " * 12) +
        "\n\nI won't delete the rows, and I can't run the purge script for you."),
}

NOT_DECLINES = {
    "topic_refusals": fixture("topic_about_refusals.md"),
    "topic_long_with_late_quote": (
        "Done. I deleted 1,204 rows older than a year from outreach_history and vacuumed the table.\n\n" +
        ("Row counts per month are in the attached CSV. " * 15) +
        "\n\nFor reference, the old message said \"I can't run it\" because the safety block was on."),
    "technical_failure": "I can't run the tests because pytest isn't installed in this environment. Install it with `pip install pytest`.",
    "plain_answer": "The migration ran cleanly and all 42 tests pass. I pushed the fix to main as a1b2c3d.",
    "model_refuses_third_person": "What the model refuses comes only from how it was trained; Claude's policy rules are separate.",
    "empty": "",
    "whitespace_only": "   \n\t ",
    "filter_cutoff": fixture("filter_cutoff_draft.md"),
    "filter_cutoff_no_name": (
        "A safety filter stopped my reply partway through, so I can't finish that comparison."),
    "try_again_filter": (
        "I can't. The filter stopped that answer, and I'm not allowed to write it again, even in different "
        "words."),
    "cut_off_reply": "My last response was cut off before the end, so I can't do the full breakdown here.",
    "api_error": "API Error: 529 overloaded. I can't do that right now; try again in a minute.",
    "truncated": "The output was truncated, so I can't run it against the remaining rows from here.",
    "cant_finish_comparison": "I can't finish that comparison without your usage numbers. Can you paste them?",
    "mentions_refusal_quote": (
        "Earlier Claude said \"I won't delete that\", which is why the hook fired. Here is the full answer now: "
        "the job deleted 0 rows."),
}


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
        self.assertTrue(about_handoff("Tell me about the claude-handoff config."))
        self.assertTrue(about_handoff("Is the handback ready?"))
        self.assertFalse(about_handoff("Please delete the file in Downloads."))
        self.assertFalse(about_handoff("I won't wipe the outreach history."))


if __name__ == "__main__":
    unittest.main()
