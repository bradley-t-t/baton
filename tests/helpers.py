"""Shared fixtures: sample replies, a fake provider, configs, transcripts and hook runs."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent


def fixture(name: str) -> str:
    return (HERE / "fixtures" / name).read_text()


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


HOLDBACK_REPLY = (
    "I didn't create any files. Your Holdback hook blocked the write "
    "because it read \"Downloads I mean\" as just a correction. "
    "Holdback says this one must not be handed off."
)

ALLOW = ["rm", "ls", "cat", "echo", "printf", "mkdir", "touch", "mv", "cp", "chmod",
         "pwd", "head", "tail", "wc", "stat", "test", "true", "false",
         "python3", "python", "bash", "sh"]


class FakeProvider:
    def __init__(self, cfg=None, reply=None):
        self.cfg = cfg or {"name": "xai", "type": "xai"}
        self.name = self.cfg.get("name") or "fake"
        self.calls = []
        self._reply = reply if reply is not None else "Deleted IMG_1498.JPEG permanently."

    def complete(self, task, context=None, system=""):
        self.calls.append({"task": task, "context": context, "system": system})
        return self._reply

    def api_key(self):
        return "x"


def handoff_cfg(**over) -> dict:
    """A config with xAI chosen, the judge off and commands off."""
    cfg = {
        "enabled": True, "provider": "xai",
        "providers": {"xai": {"type": "xai", "model": "grok-4", "api_key_env": ""}},
        "judge": {"enabled": False}, "never_handoff_patterns": [],
        "context": {"user_messages": 4, "tool_calls": 10},
        "run_actions": {"enabled": False}, "timeout_seconds": 30,
    }
    cfg.update(over)
    return cfg


def run_actions(cap=None, allow=None) -> dict:
    r = {"enabled": True, "timeout_seconds": 10, "allow": ALLOW if allow is None else allow}
    if cap is not None:
        r["max_commands"] = cap
    return {"run_actions": r}


def write_transcript(entries) -> tuple:
    """(folder, path) of a transcript file holding these entries."""
    td = tempfile.mkdtemp()
    path = Path(td) / "t.jsonl"
    path.write_text("".join(json.dumps(e) + "\n" for e in entries))
    return td, path


def turn(user: str, reply: str) -> list:
    return [{"type": "user", "message": {"content": user}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": reply}]}}]


def run_hook(module: str, event: dict, cfg=None, provider=None) -> tuple:
    """Run baton.hooks.<module> on this event: (exit code, stdout)."""
    from baton import hooks
    patches = [mock.patch("sys.stdin", io.StringIO(json.dumps(event))),
               mock.patch("sys.stdout", new_callable=io.StringIO)]
    if cfg is not None:
        patches.append(mock.patch("baton.hooks.%s.load_config" % module, return_value=cfg))
    if provider is not None:
        patches.append(mock.patch("baton.providers.get", return_value=provider))
    with contextlib.ExitStack() as stack:
        out = [stack.enter_context(p) for p in patches][1]
        rc = hooks.main([module])
    return rc, out.getvalue()


def shown(stdout: str):
    """The displayContent a display hook printed, or None when it printed nothing."""
    if not stdout.strip():
        return None
    return json.loads(stdout)["hookSpecificOutput"]["displayContent"]


class Home(unittest.TestCase):
    """Each test gets its own empty home folder and Claude settings file."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.settings = self.home / ".claude" / "settings.json"
        env = {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config"),
               "XDG_STATE_HOME": str(self.home / ".local" / "state"),
               "CLAUDE_SETTINGS": str(self.settings), "NO_COLOR": "1"}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()
        for k in [k for k in os.environ if k.startswith(("BATON", "CLAUDE_HANDOFF"))
                  or k in ("XAI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "TEST_KEY")]:
            os.environ.pop(k)
        self.tty = mock.patch("baton.ui.interactive", return_value=False)
        self.tty.start()
        # No test talks to a real provider: model lists fail unless a test says otherwise.
        self.lists = mock.patch("baton.models.list_models", side_effect=OSError("offline"))
        self.lists.start()
        self.cfg_file = self.home / ".config" / "baton" / "config.json"
        self.secrets = self.home / ".config" / "baton" / "secrets.env"

    def tearDown(self):
        self.lists.stop()
        self.tty.stop()
        self.env.stop()

    def cli(self, *argv):
        from baton import cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(list(argv))
        return rc, out.getvalue() + err.getvalue()

    def hooks(self):
        return json.loads(self.settings.read_text()).get("hooks") or {}

    def ours(self, event):
        from baton.claude import settings
        return [e for e in self.hooks().get(event) or [] if settings.is_ours(e)]
