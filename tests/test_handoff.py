"""End-to-end: run() talks to a mocked provider and returns a labeled answer."""
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

from claude_handoff import handoff, hooks  # noqa: E402
from claude_handoff.handoff import label  # noqa: E402
from tests.test_detect import DECLINES  # noqa: E402


class FakeProvider:
    def __init__(self, cfg):
        self.cfg = cfg
        self.name = cfg.get("name") or "fake"
        self.calls = []

    def complete(self, task, context=None, system=""):
        self.calls.append({"task": task, "context": context})
        return "Deleted IMG_1498.JPEG permanently."

    def api_key(self):
        return "x"


class RunHandoff(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "enabled": True, "provider": "fake",
            "providers": {"fake": {"type": "openai_compatible", "base_url": "http://example.invalid",
                                   "model": "x", "api_key_env": ""}},
            "judge": {"enabled": False}, "never_handoff_patterns": [],
            "context": {"user_messages": 4, "tool_calls": 10},
            "run_actions": {"enabled": False}, "timeout_seconds": 30,
        }

    def test_label_names_provider(self):
        t = label("xai", "done")
        self.assertIn("Handoff result from xai:", t)
        self.assertIn("done", t)
        self.assertNotIn("no choice", t.lower())
        self.assertNotIn("bypass", t.lower())
        self.assertNotIn("without stating", t.lower())

    def test_run_hands_off_and_labels(self):
        fake = FakeProvider({"name": "xai", "type": "xai"})
        with mock.patch("claude_handoff.providers.get", return_value=fake):
            out = handoff.run(DECLINES["downloads_delete_refusal"], entries=[],
                              user="Permanently delete the image in Downloads",
                              session="s1", cfg=self.cfg)
        self.assertIsNotNone(out)
        self.assertIn("Handoff result from xai:", out)
        self.assertIn("Deleted IMG_1498.JPEG", out)
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("Permanently delete", fake.calls[0]["task"])

    def test_run_skips_non_decline(self):
        with mock.patch("claude_handoff.providers.get") as g:
            out = handoff.run("All 42 tests pass.", entries=[], user="run tests",
                              session="s2", cfg=self.cfg)
            self.assertIsNone(out)
            g.assert_not_called()

    def test_run_does_not_rehandoff_same_reply(self):
        fake = FakeProvider({"name": "xai", "type": "xai"})
        with mock.patch("claude_handoff.providers.get", return_value=fake):
            a = handoff.run(DECLINES["bare_cant"], entries=[], user="do it", session="s3", cfg=self.cfg)
            b = handoff.run(DECLINES["bare_cant"], entries=[], user="do it", session="s3", cfg=self.cfg)
        self.assertIsNotNone(a)
        self.assertIsNone(b)
        self.assertEqual(len(fake.calls), 1)

    def test_provider_failure_surfaces(self):
        class Boom(FakeProvider):
            def complete(self, *a, **k):
                raise RuntimeError("no key")
        with mock.patch("claude_handoff.providers.get", return_value=Boom({"name": "xai", "type": "xai"})):
            out = handoff.run(DECLINES["will_not"], entries=[], user="sign up", session="s4", cfg=self.cfg)
        self.assertIn("Handoff to fake failed", out)
        self.assertIn("no key", out)

    def test_stop_hook_blocks_with_reason(self):
        entries = [
            {"type": "user", "message": {"content": "Permanently delete the image"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": DECLINES["still_not_going_to"]}]}},
        ]
        # Write a transcript file the hook will read.
        td = tempfile.mkdtemp()
        path = Path(td) / "t.jsonl"
        with open(path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        fake = FakeProvider({"name": "local", "type": "openai_compatible"})
        event = {"session_id": "s5", "transcript_path": str(path), "cwd": td}
        with mock.patch("claude_handoff.providers.get", return_value=fake), \
             mock.patch("claude_handoff.hooks.load_config", return_value=self.cfg), \
             mock.patch("sys.stdin", io.StringIO(json.dumps(event))), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = hooks.main(["stop"])
        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["decision"], "block")
        self.assertIn("Handoff result from local:", payload["reason"])

    def test_stop_hook_active_is_noop(self):
        event = {"session_id": "s6", "stop_hook_active": True, "transcript_path": ""}
        with mock.patch("sys.stdin", io.StringIO(json.dumps(event))), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as out:
            rc = hooks.main(["stop"])
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue(), "")

    def test_prompt_hook_remembers(self):
        event = {"session_id": "s7", "prompt": "please wipe the table"}
        with mock.patch("sys.stdin", io.StringIO(json.dumps(event))), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            rc = hooks.main(["prompt"])
        self.assertEqual(rc, 0)
        from claude_handoff.state import last_request
        self.assertEqual(last_request("s7"), "please wipe the table")


class Actions(unittest.TestCase):
    def test_extract_and_allow(self):
        from claude_handoff.actions import extract_commands, first_word, run_allowed
        text = "Sure.\n```bash\nls -la\nrm -rf /\n```\n"
        self.assertEqual(extract_commands(text), ["ls -la", "rm -rf /"])
        self.assertEqual(first_word("ls -la"), "ls")
        cfg = {"run_actions": {"enabled": True, "timeout_seconds": 5, "max_commands": 2,
                               "allow": {"xai": ["ls", "pwd"]}}}
        out = run_allowed(text, cfg, "xai")
        self.assertIn("$ ls -la", out)
        self.assertNotIn("rm -rf", out)

    def test_disabled_is_noop(self):
        from claude_handoff.actions import run_allowed
        self.assertEqual(run_allowed("```bash\nls\n```", {"run_actions": {"enabled": False}}, "xai"), "")


if __name__ == "__main__":
    unittest.main()
