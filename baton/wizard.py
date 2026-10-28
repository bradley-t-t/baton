"""`baton setup`: pick a provider, store its key, test it, choose whether the provider's
commands may run, show the Claude Code hook entries, and install them.

Every prompt has a flag, so the same steps run unattended with --yes.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

from . import install as inst
from . import keys, ui
from .config import config_dir, config_path, env

PROVIDERS = {
    "xai": {"label": "Grok (xAI)", "short": "Grok", "type": "xai", "model": "grok-4",
            "api_key_env": "XAI_API_KEY"},
    "openai": {"label": "OpenAI", "short": "OpenAI", "type": "openai", "model": "gpt-5",
               "api_key_env": "OPENAI_API_KEY"},
    "anthropic": {"label": "Anthropic", "short": "Anthropic", "type": "anthropic", "model": "claude-sonnet-4-5",
                  "api_key_env": "ANTHROPIC_API_KEY"},
    "local": {"label": "Local (Ollama, LM Studio, any OpenAI-compatible server)", "short": "Local server",
              "type": "openai_compatible", "model": "llama3.1",
              "base_url": "http://localhost:11434/v1", "api_key_env": ""},
}
ALIASES = {"grok": "xai", "xai": "xai", "openai": "openai", "anthropic": "anthropic",
           "claude": "anthropic", "local": "local", "ollama": "local", "lmstudio": "local"}
ORDER = ["xai", "openai", "anthropic", "local"]

READ_ONLY = ["cat", "du", "echo", "false", "file", "grep", "head", "ls", "printf", "pwd",
             "stat", "tail", "test", "true", "wc"]
COMMAND_MODES = ["off", "read-only", "custom"]


class SetupError(Exception):
    pass


def add_arguments(p):
    p.add_argument("--provider", help="grok, openai, anthropic or local")
    p.add_argument("--model", help="Model name (defaults to the provider's usual one)")
    p.add_argument("--base-url", help="Server URL for --provider local")
    key = p.add_mutually_exclusive_group()
    key.add_argument("--api-key", help="API key to store (shows up in shell history; "
                                       "--api-key-env is safer)")
    key.add_argument("--api-key-env", metavar="VAR",
                     help="Read the API key from this environment variable")
    p.add_argument("--commands", choices=COMMAND_MODES,
                   help="Whether the provider's shell commands may run (default: off)")
    p.add_argument("--allow", help="Comma-separated commands for --commands custom")
    p.add_argument("--no-test", action="store_true", help="Skip the connection test")
    p.add_argument("--settings", help="Claude Code settings file "
                                      "(default ~/.claude/settings.json)")
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be written and change nothing")
    p.add_argument("-y", "--yes", action="store_true",
                   help="Don't ask; use the flags and defaults")


def _norm_provider(name: str) -> str:
    key = ALIASES.get((name or "").strip().lower())
    if not key:
        raise SetupError("unknown provider %r (use grok, openai, anthropic or local)" % name)
    return key


def _load_raw() -> dict:
    """The config file as written (no defaults merged), or the example."""
    path = config_path()
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict):
            return data
    except (OSError, ValueError):
        pass
    return json.loads(inst.example_config_path().read_text())


def _target_config() -> Path:
    return Path(os.path.expanduser(env("CONFIG"))) if env("CONFIG") else config_dir() / "config.json"


def commands_block(mode: str, allow=None, current: dict = None) -> dict:
    ra = copy.deepcopy(current or {})
    ra.setdefault("timeout_seconds", 60)
    ra.setdefault("max_commands", 20)
    if mode == "off":
        ra["enabled"] = False
        ra.pop("read_only", None)
    elif mode == "read-only":
        ra.update({"enabled": True, "read_only": True, "allow": list(READ_ONLY)})
    elif mode == "custom":
        items = [a.strip() for a in (allow or []) if a.strip()]
        if not items:
            raise SetupError("--commands custom needs --allow with at least one command")
        ra.update({"enabled": True, "read_only": False, "allow": items})
    else:
        raise SetupError("unknown --commands %r" % mode)
    return ra


def current_mode(ra: dict) -> str:
    if not (ra or {}).get("enabled"):
        return "off"
    if ra.get("read_only"):
        return "read-only"
    return "custom"


def test_connection(pcfg: dict, key_env: str, key: str):
    """(ok, message). Sends one short prompt to the chosen provider."""
    from .providers import get
    old = os.environ.get(key_env) if key_env else None
    try:
        if key_env and key:
            os.environ[key_env] = key
        out = get(dict(pcfg, timeout_seconds=30)).complete(
            "Reply with exactly: pong", system="Reply with exactly the word pong.")
        return True, (out or "").strip()[:80] or "(empty reply)"
    except Exception as e:  # noqa: BLE001
        return False, "%s: %s" % (type(e).__name__, str(e)[:300])
    finally:
        if key_env and key:
            if old is None:
                os.environ.pop(key_env, None)
            else:
                os.environ[key_env] = old


def hook_preview(settings: Path) -> str:
    entries = {e: [inst._ours_entry(e)] for e in inst.EVENTS}
    return "%s\n%s" % (settings, json.dumps({"hooks": entries}, indent=2))


def run(args) -> int:
    try:
        return _run(args)
    except SetupError as e:
        print(ui.red("setup: %s" % e), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nSetup cancelled. Nothing more was changed.")
        return 130


def _run(args) -> int:
    asking = ui.interactive() and not args.yes
    if not asking and not args.provider:
        raise SetupError("--provider is required when not running interactively")
    if args.settings:
        os.environ["CLAUDE_SETTINGS"] = args.settings
    settings = inst.settings_path()

    if asking:
        ui.heading("Baton setup")
        ui.say("A Claude Code hook that adds answers from a second model you configure.")
        ui.say(ui.dim("Press Enter to take the default in brackets. Ctrl-C quits."))

    moved = [] if args.dry_run else inst.migrate_old_files()
    for m in moved:
        ui.say("Copied %s from ~/.config/claude-handoff/" % m)
    raw = _load_raw()

    # Provider
    if args.provider:
        name = _norm_provider(args.provider)
    else:
        cur = ALIASES.get(str(raw.get("provider") or "").lower(), "xai")
        ui.heading("1. Provider")
        name = ORDER[ui.choose("Which model should Baton ask?",
                               [(PROVIDERS[k]["label"], "") for k in ORDER],
                               ORDER.index(cur))]
    spec = PROVIDERS[name]
    pcfg = copy.deepcopy((raw.get("providers") or {}).get(name) or {})
    pcfg["type"] = spec["type"]
    pcfg.setdefault("api_key_env", spec["api_key_env"])
    pcfg["model"] = args.model or (ui.ask("Model", pcfg.get("model") or spec["model"])
                                   if asking else pcfg.get("model") or spec["model"])
    if name == "local":
        default_url = pcfg.get("base_url") or spec["base_url"]
        pcfg["base_url"] = args.base_url or (ui.ask("Server URL", default_url)
                                             if asking else default_url)
    key_env = pcfg.get("api_key_env") or ""

    # Key
    key = ""
    if args.api_key:
        key = args.api_key
    elif args.api_key_env:
        key = os.environ.get(args.api_key_env, "")
        if not key:
            raise SetupError("%s is not set" % args.api_key_env)
    if key_env and not key:
        in_file = bool(keys.read().get(key_env))
        in_env = os.environ.get(key_env, "")
        if asking:
            ui.heading("2. API key")
            ui.say("Stored in %s, readable only by you." % keys.secrets_path())
            if in_file and ui.confirm("A %s is already saved. Keep it?" % key_env, True):
                pass
            elif in_env and ui.confirm("%s is set in this shell. Save it?" % key_env, True):
                key = in_env
            else:
                key = ui.ask_secret("%s (input hidden)" % key_env)
                if not key and not in_file:
                    raise SetupError("no API key given for %s" % spec["label"])
        elif not in_file:
            # Claude Code may not see this shell's variables, so the hooks get their own copy.
            key = in_env
            if not key:
                raise SetupError("%s needs an API key: set %s, or pass --api-key-env VAR"
                                 " or --api-key" % (spec["label"], key_env))

    # Test
    do_test = not args.no_test
    if asking and do_test:
        ui.heading("3. Connection test")
        do_test = ui.confirm("Send one short test prompt to %s?" % spec["short"], True)
    if do_test:
        test_key = key or os.environ.get(key_env, "") or keys.read().get(key_env, "")
        ok, msg = test_connection(dict(pcfg, name=name), key_env, test_key)
        if ok:
            ui.say(ui.green("ok") + "  %s answered: %s" % (spec["short"], msg))
        else:
            ui.say(ui.red("failed") + "  %s" % msg)
            if not (asking and ui.confirm("Continue anyway?", False)):
                raise SetupError("connection test failed (use --no-test to skip it)")

    # Commands
    ra_now = raw.get("run_actions") or {}
    if args.commands:
        mode = args.commands
    elif asking:
        ui.heading("4. Commands")
        ui.say("The provider can reply with a shell script. Baton can run it on this "
               "machine, in Claude's working directory, if you allow it.")
        opts = [("Off", "never run commands"),
                ("Read-only", ", ".join(READ_ONLY) + "; no writing to files"),
                ("Custom", "you list the commands that may run")]
        mode = COMMAND_MODES[ui.choose("Run the provider's commands?", opts,
                                       COMMAND_MODES.index(current_mode(ra_now)))]
    else:
        mode = current_mode(ra_now)  # the example config has it off
    allow = (args.allow or "").split(",") if args.allow else None
    if mode == "custom" and allow is None:
        if asking:
            start = ",".join(ra_now.get("allow") or []) if isinstance(ra_now.get("allow"), list) else ""
            allow = ui.ask("Commands that may run, comma-separated", start).split(",")
        elif current_mode(ra_now) == "custom" and isinstance(ra_now.get("allow"), list):
            allow = ra_now["allow"]  # already custom: keep the list in use
    ra = commands_block(mode, allow, ra_now)

    # Review
    cfg_file = _target_config()
    new_raw = copy.deepcopy(raw)
    new_raw["provider"] = name
    new_raw.setdefault("enabled", True)
    new_raw.setdefault("providers", {})[name] = pcfg
    new_raw["run_actions"] = ra

    ui.heading("Review")
    ui.say("Provider:  %s, model %s%s" % (spec["short"], pcfg["model"],
                                          (" at %s" % pcfg["base_url"]) if name == "local" else ""))
    ui.say("Commands:  %s" % {"off": "off",
                              "read-only": "read-only (%s)" % ", ".join(ra["allow"]),
                              "custom": "custom (%s)" % ", ".join(ra.get("allow") or [])}[mode])
    ui.say("Config:    %s" % cfg_file)
    if key:
        ui.say("API key:   %s in %s (mode 600)" % (key_env, keys.secrets_path()))
    ui.say("Hooks to write into Claude Code settings:")
    ui.say(ui.dim(hook_preview(settings)))
    ui.say(ui.dim("Any older Baton or claude-handoff entries are replaced. Other hooks are kept."))

    if args.dry_run:
        ui.say("\nDry run: nothing was written.")
        return 0
    if asking and not ui.confirm("\nWrite these and install?", True):
        ui.say("Nothing more was changed.")
        return 1

    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cfg_file.with_name(cfg_file.name + ".tmp")
    tmp.write_text(json.dumps(new_raw, indent=2) + "\n")
    os.replace(tmp, cfg_file)
    if key and key_env:
        keys.write(key_env, key)
    path = inst.install()
    ui.say(ui.green("Done.") + " Hooks are in %s. Start a new Claude Code session to use them."
           % path)
    ui.say("Check everything with `baton doctor`.")
    return 0
