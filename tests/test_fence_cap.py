"""The per-block command cap, the allow list check per simple command, and what is
shown when a block is not run. Also the claude-handoff to Baton fallbacks."""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

STATE = tempfile.mkdtemp()
os.environ["BATON_STATE"] = STATE
os.environ["BATON_JUDGE"] = "0"

from baton import actions, config, handoff  # noqa: E402
from baton import install as inst  # noqa: E402
from tests.test_handoff import ALLOW, FakeProvider  # noqa: E402

def eight(d):
    lines = "".join("printf '%%s\\n' 'line %d' > %s/f%d.txt\n" % (i, d, i) for i in range(1, 9))
    return "```bash\n" + lines + "```\n"


def ra(cap=None):
    r = {"enabled": True, "timeout_seconds": 10, "allow": ALLOW}
    if cap is not None:
        r["max_commands"] = cap
    return {"run_actions": r}


class Cap(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.mkdtemp()

    def test_default_cap_is_20(self):
        self.assertEqual(config.DEFAULTS["run_actions"]["max_commands"], 20)
        example = json.loads(inst.example_config_path().read_text())
        self.assertEqual(example["run_actions"]["max_commands"], 20)

    def test_eight_commands_run_under_default(self):
        out = actions.run_allowed(eight(self.td), ra(), "xai", cwd=self.td)
        self.assertTrue(out["ran"] and out["ok"], out["summary"])
        self.assertEqual(len(list(Path(self.td).glob("f*.txt"))), 8)
        self.assertIn("Created 8 files", out["summary"])

    def test_over_cap_runs_nothing_and_says_so_plainly(self):
        out = actions.run_allowed(eight(self.td), ra(5), "xai", cwd=self.td)
        self.assertTrue(out["ran"])
        self.assertFalse(out["ok"])
        self.assertEqual(out["not_run"], "too_many")
        self.assertEqual(list(Path(self.td).glob("f*.txt")), [])
        self.assertEqual(out["summary"], "Nothing was run: the script had 8 commands and "
                                         "the limit is 5 (run_actions.max_commands).")
        self.assertNotIn("fence", out["summary"])

    def test_loop_counts_its_body_once(self):
        loop = ("```bash\nfor i in 1 2 3 4 5 6 7 8; do printf 'x\\n' > %s/f$i.txt; done\n```"
                % self.td)
        out = actions.run_allowed(loop, ra(5), "xai", cwd=self.td)
        self.assertTrue(out["ok"], out["summary"])
        self.assertEqual(len(list(Path(self.td).glob("f*.txt"))), 8)


class AllowEveryCommand(unittest.TestCase):
    def check(self, script):
        out = actions.run_allowed("```bash\n%s\n```" % script, ra(), "xai",
                                  cwd=tempfile.mkdtemp())
        return out

    def test_chain_after_allowed_command_is_checked(self):
        out = self.check("ls /tmp && curl -s https://example.com")
        self.assertEqual(out.get("not_run"), "not_allowed")
        self.assertIn("curl", out["summary"])

    def test_pipe_and_subshell_are_checked(self):
        self.assertEqual(self.check("ls | nc example.com 80").get("not_run"), "not_allowed")
        self.assertEqual(self.check("ls $(curl -s x)").get("not_run"), "not_allowed")
        self.assertEqual(self.check("ls `curl -s x`").get("not_run"), "not_allowed")

    def test_loop_and_if_bodies_are_checked(self):
        self.assertEqual(self.check("for f in a b; do curl $f; done").get("not_run"),
                         "not_allowed")
        self.assertEqual(self.check("if [ -f /nope ]; then curl x; fi").get("not_run"),
                         "not_allowed")

    def test_test_bracket_counts_as_test(self):
        out = self.check("if [ -d /tmp ]; then true; fi")
        self.assertTrue(out["ok"], out["summary"])


class Shown(unittest.TestCase):
    """What the person sees when the provider's first block is over the cap."""

    def setUp(self):
        self.td = tempfile.mkdtemp()
        self.cfg = {
            "enabled": True, "provider": "xai",
            "providers": {"xai": {"type": "xai", "model": "grok-4", "api_key_env": ""}},
            "judge": {"enabled": False}, "never_handoff_patterns": [],
            "context": {"user_messages": 4, "tool_calls": 10}, "timeout_seconds": 30,
            **ra(5),
        }
        self.r = {"kind": "decline"}

    def run_with(self, replies):
        fake = FakeProvider({"name": "xai", "type": "xai"})
        seen = []

        def complete(task, context=None, system=""):
            seen.append({"task": task, "system": system})
            return replies[len(seen) - 1]
        fake.complete = complete
        with mock.patch("baton.providers.get", return_value=fake):
            out = handoff.complete(self.r, "I can't do that.", "make 8 files", self.td,
                                   [], [], self.cfg)
        return out, seen

    def test_system_prompt_states_the_limits(self):
        _, seen = self.run_with(["Done."])
        self.assertIn("at most 5 commands", seen[0]["system"])
        self.assertIn("printf", seen[0]["system"])

    def test_over_cap_asks_again_and_shows_the_result(self):
        loop = ("```bash\nfor i in 1 2 3 4 5 6 7 8; do printf 'x\\n' > %s/f$i.txt; done\n```"
                % self.td)
        out, seen = self.run_with([eight(self.td), loop])
        self.assertEqual(len(seen), 2)
        self.assertIn("limit is 5", seen[1]["task"])
        self.assertEqual(len(list(Path(self.td).glob("f*.txt"))), 8)
        self.assertTrue(out.endswith("\n(via Grok)"), out)
        self.assertNotIn("Too many commands", out)
        self.assertNotIn("Nothing was run", out)

    def test_over_cap_twice_says_nothing_ran(self):
        out, seen = self.run_with([eight(self.td)] * 2)
        self.assertEqual(len(seen), 2)
        self.assertEqual(list(Path(self.td).glob("f*.txt")), [])
        self.assertEqual(out, "Nothing was run: the script had 8 commands and the limit "
                              "is 5 (run_actions.max_commands).\n(via Grok)")


class OldNames(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.home, "HOME": self.home})
        self.env.start()
        for k in [k for k in os.environ if k.startswith(("BATON_CONFIG", "CLAUDE_HANDOFF"))]:
            os.environ.pop(k)
        os.environ.pop("BATON_PROVIDER", None)

    def tearDown(self):
        self.env.stop()

    def test_old_env_vars_still_work_and_new_ones_win(self):
        os.environ["CLAUDE_HANDOFF_PROVIDER"] = "openai"
        self.assertEqual(config.load()["provider"], "openai")
        os.environ["BATON_PROVIDER"] = "local"
        self.assertEqual(config.load()["provider"], "local")
        os.environ["CLAUDE_HANDOFF"] = "0"
        self.assertFalse(config.load()["enabled"])

    def test_old_config_dir_is_used_until_there_is_a_new_one(self):
        old = Path(self.home) / "claude-handoff" / "config.json"
        old.parent.mkdir(parents=True)
        old.write_text("{}")
        self.assertEqual(config.config_path(), old)
        new = Path(self.home) / "baton" / "config.json"
        new.parent.mkdir(parents=True)
        new.write_text("{}")
        self.assertEqual(config.config_path(), new)

    def test_install_migrates_and_replaces_old_hooks(self):
        old_dir = Path(self.home) / "claude-handoff"
        old_dir.mkdir()
        example = json.loads(inst.example_config_path().read_text())
        example["run_actions"]["max_commands"] = 5
        (old_dir / "config.json").write_text(json.dumps(example))
        (old_dir / "secrets.env").write_text("XAI_API_KEY=x\n")
        settings = Path(self.home) / "settings.json"
        old_cmd = ('if [ -f ~/.config/claude-handoff/secrets.env ]; then set -a; . f; set +a; '
                   'fi; PYTHONPATH="/x/claude-handoff" "python3" -m claude_handoff.hooks %s')
        tether = {"hooks": [{"type": "command", "command": "~/.tether/hook.sh stop"}]}
        settings.write_text(json.dumps({"hooks": {
            "Stop": [tether, {"hooks": [{"type": "command", "command": old_cmd % "stop"}]}],
            "UserPromptSubmit": [{"hooks": [{"type": "command", "command": old_cmd % "prompt"}]}],
        }}))
        inst.install(settings)
        hooks = json.loads(settings.read_text())["hooks"]
        for event in ("Stop", "UserPromptSubmit", "MessageDisplay"):
            cmds = [h["command"] for e in hooks[event] for h in e["hooks"]]
            self.assertFalse(any("claude_handoff" in c for c in cmds), cmds)
            self.assertEqual(sum("-m baton.hooks" in c for c in cmds), 1, cmds)
        self.assertIn(tether, hooks["Stop"])
        new_cfg = json.loads((Path(self.home) / "baton" / "config.json").read_text())
        self.assertEqual(new_cfg["run_actions"]["max_commands"], 20)
        secrets = Path(self.home) / "baton" / "secrets.env"
        self.assertEqual(secrets.read_text(), "XAI_API_KEY=x\n")
        self.assertEqual(stat.S_IMODE(secrets.stat().st_mode), 0o600)
        self.assertTrue((old_dir / "config.json").exists())
        inst.uninstall(settings)
        hooks = json.loads(settings.read_text())["hooks"]
        self.assertEqual(hooks, {"Stop": [tether]})

    def test_a_cap_someone_chose_is_kept(self):
        old_dir = Path(self.home) / "claude-handoff"
        old_dir.mkdir()
        (old_dir / "config.json").write_text(json.dumps({"run_actions": {"max_commands": 8}}))
        inst.migrate_old_files()
        new_cfg = json.loads((Path(self.home) / "baton" / "config.json").read_text())
        self.assertEqual(new_cfg["run_actions"]["max_commands"], 8)


class OldHookCommand(unittest.TestCase):
    def test_old_module_path_still_runs(self):
        import subprocess
        import sys
        root = str(inst.package_root())
        p = subprocess.run([sys.executable, "-m", "claude_handoff.hooks", "--help"],
                           capture_output=True, text=True, cwd=tempfile.mkdtemp(),
                           env={**os.environ, "PYTHONPATH": root})
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertIn("baton.hooks", p.stderr)


if __name__ == "__main__":
    unittest.main()
