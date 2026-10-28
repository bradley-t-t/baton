"""`baton setup`: pick a provider, store its key, test it, and install the Claude Code
hooks.

It only asks for the provider, the server URL (local only) and the API key. The model,
commands and the rest come from ~/.config/baton/config.json: the defaults on a first
run, and whatever is in the file after that, so hand edits are kept. --model,
--commands, --allow and --reset change them. Every prompt has a flag, so the same steps
run unattended with --yes.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

from .. import keys, models, ui
from ..claude import settings
from ..config import config_dir, config_path, env, example_config_path, migrate_old_files
from ..providers import ping
from ..runner import allows_all

PROVIDERS = {
    "xai": {"label": "Grok (xAI)", "short": "Grok", "type": "xai", "model": "auto",
            "api_key_env": "XAI_API_KEY"},
    "openai": {"label": "OpenAI", "short": "OpenAI", "type": "openai", "model": "auto",
               "api_key_env": "OPENAI_API_KEY"},
    "anthropic": {"label": "Anthropic", "short": "Anthropic", "type": "anthropic", "model": "auto",
                  "api_key_env": "ANTHROPIC_API_KEY"},
    "local": {"label": "Local (Ollama, LM Studio, any OpenAI-compatible server)", "short": "Local server",
              "type": "openai_compatible", "model": "auto",
              "base_url": "http://localhost:11434/v1", "api_key_env": ""},
}
ALIASES = {"grok": "xai", "xai": "xai", "openai": "openai", "anthropic": "anthropic",
           "claude": "anthropic", "local": "local", "ollama": "local", "lmstudio": "local"}
ORDER = ["xai", "openai", "anthropic", "local"]

READ_ONLY = ["cat", "du", "echo", "false", "file", "grep", "head", "ls", "printf", "pwd",
             "stat", "tail", "test", "true", "wc"]
COMMAND_MODES = ["on", "off", "read-only", "custom"]


class SetupError(Exception):
    pass


def add_arguments(p) -> None:
    p.add_argument("--provider", help="grok, openai, anthropic or local")
    p.add_argument("--model", help="Model name, or auto for the provider's newest flagship "
                                   "(the default)")
    p.add_argument("--base-url", help="Server URL for --provider local")
    key = p.add_mutually_exclusive_group()
    key.add_argument("--api-key", help="API key to store (shows up in shell history; "
                                       "--api-key-env is safer)")
    key.add_argument("--api-key-env", metavar="VAR",
                     help="Read the API key from this environment variable")
    p.add_argument("--commands", choices=COMMAND_MODES,
                   help="on: any command (the default); off; read-only; or custom with --allow")
    p.add_argument("--allow", help="Comma-separated commands for --commands custom")
    p.add_argument("--reset", action="store_true",
                   help="Put the model and commands back to the defaults")
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
    return json.loads(example_config_path().read_text())


def _target_config() -> Path:
    return Path(os.path.expanduser(env("CONFIG"))) if env("CONFIG") else config_dir() / "config.json"


def commands_block(mode: str, allow: list | None = None, current: dict | None = None) -> dict:
    ra = copy.deepcopy(current or {})
    ra.setdefault("timeout_seconds", 60)
    ra.setdefault("max_commands", 20)
    if mode == "on":
        ra.update({"enabled": True, "allow": "all"})
        ra.pop("read_only", None)
    elif mode == "off":
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
    if allows_all({"run_actions": ra}, ""):
        return "on"
    return "custom"


def _is_old_default(ra: dict) -> bool:
    """The run_actions block Baton shipped before commands defaulted to on, untouched."""
    return (not ra.get("enabled") and ra.get("allow") in (None, [])
            and not ra.get("read_only"))


def hook_preview(path: Path) -> str:
    entries = {e: [settings.hook_entry(e)] for e in settings.EVENTS}
    return "%s\n%s" % (path, json.dumps({"hooks": entries}, indent=2))


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
    settings_file = settings.settings_path()

    if asking:
        ui.heading("Baton setup")
        ui.say("When Claude Code turns down a request, ask another AI and bring its answer back.")
        ui.say(ui.dim("Press Enter to take the default in brackets. Ctrl-C quits."))

    for m in ([] if args.dry_run else migrate_old_files()):
        ui.say("Copied %s from ~/.config/claude-handoff/" % m)
    raw = _load_raw()

    name = _choose_provider(args, raw, asking)
    spec = PROVIDERS[name]
    pcfg = _provider_block(args, raw, name, asking)
    key_env = pcfg.get("api_key_env") or ""
    key = _api_key(args, spec, key_env, asking)
    resolved = _connection_test(args, spec, name, pcfg, key_env, key, asking)
    ra = _commands(args, raw.get("run_actions") or {})
    mode = current_mode(ra)

    cfg_file = _target_config()
    new_raw = copy.deepcopy(raw)
    new_raw["provider"] = name
    new_raw.setdefault("enabled", True)
    new_raw.setdefault("providers", {})[name] = pcfg
    new_raw["run_actions"] = ra
    _review(spec, name, pcfg, resolved, ra, mode, cfg_file, key and key_env, settings_file)

    if args.dry_run:
        ui.say("Hooks that would be written:")
        ui.say(ui.dim(hook_preview(settings_file)))
        ui.say("\nDry run: nothing was written.")
        return 0

    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cfg_file.with_name(cfg_file.name + ".tmp")
    tmp.write_text(json.dumps(new_raw, indent=2) + "\n")
    os.replace(tmp, cfg_file)
    if key and key_env:
        keys.write(key_env, key)
    path = settings.install()
    ui.say(ui.green("Done.") + " Hooks are in %s. Start a new Claude Code session to use them."
           % path)
    if mode == "on":
        ui.say("Commands from %s run with full permissions. To change that, edit "
               "run_actions in %s." % (spec["short"], cfg_file))
    ui.say("Check everything with `baton doctor`.")
    return 0


def _choose_provider(args, raw: dict, asking: bool) -> str:
    if args.provider:
        return _norm_provider(args.provider)
    cur = ALIASES.get(str(raw.get("provider") or "").lower(), "xai")
    ui.heading("1. Provider")
    return ORDER[ui.choose("Which model should Baton ask?",
                           [(PROVIDERS[k]["label"], "") for k in ORDER], ORDER.index(cur))]


def _provider_block(args, raw: dict, name: str, asking: bool) -> dict:
    """The provider's config: the file's copy, with the model and server URL settled."""
    spec = PROVIDERS[name]
    pcfg = copy.deepcopy((raw.get("providers") or {}).get(name) or {})
    pcfg["type"] = spec["type"]
    pcfg.setdefault("api_key_env", spec["api_key_env"])
    model_was = pcfg.get("model") or ""
    if args.model:
        pcfg["model"] = args.model
    elif args.reset or not model_was or model_was in models.OLD_DEFAULTS.get(name, ()):
        pcfg["model"] = models.AUTO
    if name == "local":
        default_url = pcfg.get("base_url") or spec["base_url"]
        pcfg["base_url"] = args.base_url or (ui.ask("Server URL", default_url)
                                             if asking else default_url)
    return pcfg


def _api_key(args, spec: dict, key_env: str, asking: bool) -> str:
    """A key to save, or "" when none is needed or the saved one is kept."""
    if args.api_key:
        return args.api_key
    if args.api_key_env:
        key = os.environ.get(args.api_key_env, "")
        if not key:
            raise SetupError("%s is not set" % args.api_key_env)
        return key
    if not key_env:
        return ""
    in_file = bool(keys.read().get(key_env))
    in_env = os.environ.get(key_env, "")
    if not asking:
        if in_file:
            return ""
        # Claude Code may not see this shell's variables, so the hooks get their own copy.
        if not in_env:
            raise SetupError("%s needs an API key: set %s, or pass --api-key-env VAR"
                             " or --api-key" % (spec["label"], key_env))
        return in_env
    ui.heading("2. API key")
    ui.say("Stored in %s, readable only by you." % keys.secrets_path())
    if in_file and ui.confirm("A %s is already saved. Keep it?" % key_env, True):
        return ""
    if in_env and ui.confirm("%s is set in this shell. Save it?" % key_env, True):
        return in_env
    key = ui.ask_secret("%s (input hidden)" % key_env)
    if not key and not in_file:
        raise SetupError("no API key given for %s" % spec["label"])
    return key


def _connection_test(args, spec: dict, name: str, pcfg: dict, key_env: str, key: str,
                     asking: bool) -> str:
    """Send one prompt unless --no-test. Returns the model "auto" picks right now."""
    if args.no_test:
        return ""
    test_key = key or (os.environ.get(key_env, "") or keys.read().get(key_env, "")
                       if key_env else "")
    resolved = ""
    if models.is_auto(pcfg.get("model")):
        # Read the provider's model list to see what "auto" picks.
        resolved, _ = models.resolve(pcfg, test_key, refresh=True)
    if asking:
        ui.heading("3. Connection test")
    ok, msg = ping(dict(pcfg, name=name, model=resolved or pcfg["model"]), key_env, test_key)
    if ok:
        ui.say(ui.green("ok") + "  %s answered: %s" % (spec["short"], msg))
    else:
        ui.say(ui.red("failed") + "  %s" % msg)
        if not (asking and ui.confirm("Continue anyway?", False)):
            raise SetupError("connection test failed (use --no-test to skip it)")
    return resolved


def _commands(args, ra_now: dict) -> dict:
    """run_actions from the flags, else as the config file has it. Only Baton's old
    untouched default of "off" is moved to the new default of on, any command."""
    allow = args.allow.split(",") if args.allow else None
    if args.commands:
        if args.commands == "custom" and allow is None and isinstance(ra_now.get("allow"), list):
            allow = ra_now["allow"]
        return commands_block(args.commands, allow, ra_now)
    if args.allow:
        return commands_block("custom", allow, ra_now)
    if args.reset or not ra_now or _is_old_default(ra_now):
        return commands_block("on", None, ra_now)
    return copy.deepcopy(ra_now)


def _review(spec: dict, name: str, pcfg: dict, resolved: str, ra: dict, mode: str,
            cfg_file: Path, key_env: str, settings_file: Path) -> None:
    ui.heading("Review")
    shown = pcfg["model"]
    if models.is_auto(shown) and resolved:
        shown = "auto (%s right now)" % resolved
    ui.say("Provider:  %s, model %s%s" % (spec["short"], shown,
                                          (" at %s" % pcfg["base_url"]) if name == "local" else ""))
    allow = ", ".join(ra.get("allow") or []) if isinstance(ra.get("allow"), list) else ""
    ui.say("Commands:  %s" % {"on": "on, any command", "off": "off",
                              "read-only": "read-only (%s)" % allow,
                              "custom": "custom (%s)" % allow}[mode])
    ui.say("Config:    %s" % cfg_file)
    if key_env:
        ui.say("API key:   %s in %s (mode 600)" % (key_env, keys.secrets_path()))
    ui.say("Hooks:     %s" % settings_file)
    ui.say(ui.dim("Any older Baton or claude-handoff entries are replaced. Other hooks are kept."))
