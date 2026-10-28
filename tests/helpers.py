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

POSIX = os.name != "nt"
# For tests whose scripts or files only make sense on macOS and Linux: sh, file modes,
# absolute POSIX paths inside a bash script. Windows has its own tests in test_windows.
posix_only = unittest.skipUnless(POSIX, "macOS and Linux only")


def fixture(name: str) -> str:
    return (HERE / "fixtures" / name).read_text()


# Sample refusals, used as realistic reply text. Whether a reply is a refusal is the
# judge's call now, so these are not a catalogue of phrasings to match.
DECLINES = {
    "wipe_cant_run_it": (
        "I can’t run it. wipe.sql permanently deletes rows from your production database, and "
        "rerunning a destructive delete script isn’t something I’ll do, even if you expect it to be a no-op."),
    "downloads_delete_refusal": fixture("downloads_delete_refusal.md"),
    "still_not_going_to": "No, I'm still not going to permanently delete it. It's a fixed line for me.",
    "will_not": "I will not sign up for that account.",
    "bare_cant": "Sorry, I can't.",
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
    """A config with xAI chosen, the judge on and commands off."""
    cfg = {
        "enabled": True, "provider": "xai",
        "providers": {"xai": {"type": "xai", "model": "grok-4", "api_key_env": ""}},
        "judge": {"enabled": True},
        "context": {"user_messages": 4, "tool_calls": 10},
        "run_actions": {"enabled": False}, "timeout_seconds": 30,
    }
    cfg.update(over)
    return cfg


@contextlib.contextmanager
def judge_says(declined: bool, task: str = ""):
    """Stand in for the judge. It is the only thing that calls a reply a refusal, so a
    test about anything else still needs one to get past that gate."""
    verdict = {"declined": declined, "task": task, "why": "test", "ms": 1}
    with mock.patch("baton.judge.enabled", return_value=True), \
            mock.patch("baton.judge.decide", return_value=verdict) as d:
        yield d


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
        # USERPROFILE is Windows' HOME; APPDATA and LOCALAPPDATA live in it there.
        env = {"HOME": str(self.home), "USERPROFILE": str(self.home),
               "APPDATA": str(self.home / "AppData" / "Roaming"),
               "LOCALAPPDATA": str(self.home / "AppData" / "Local"),
               "XDG_CONFIG_HOME": str(self.home / ".config"),
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
