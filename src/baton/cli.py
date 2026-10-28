"""The `baton` command: setup, doctor, update, install, uninstall, status, test, version."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__, keys, providers
from .claude import settings
from .commands import doctor, setup, uninstall, update
from .config import config_path, ensure_file, load, provider_config


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="baton",
        description="When Claude Code turns down a request, ask another AI and bring its answer back.")
    p.add_argument("--version", action="version", version="baton " + __version__)
    sub = p.add_subparsers(dest="cmd")

    setup.add_arguments(sub.add_parser(
        "setup", help="Choose a provider, store its key and install the hooks"))
    d = sub.add_parser("doctor", help="Check the config, key and hooks")
    d.add_argument("--online", action="store_true", help="Also send a test prompt")
    d.add_argument("--settings", help="Claude Code settings file")
    sub.add_parser("update", help="Reinstall Baton from main")
    i = sub.add_parser("install", help="Add hooks to ~/.claude/settings.json")
    i.add_argument("--settings", help="Claude Code settings file")
    u = sub.add_parser("uninstall", help="Remove Baton's hooks from ~/.claude/settings.json")
    u.add_argument("--purge", action="store_true",
                   help="Also delete Baton's config, API keys, logs and settings backups")
    u.add_argument("--all", action="store_true",
                   help="Everything --purge does, then remove the baton command itself")
    u.add_argument("-y", "--yes", action="store_true", help="Don't ask before deleting")
    u.add_argument("--settings", help="Claude Code settings file")
    sub.add_parser("status", help="Show whether hooks are installed")
    t = sub.add_parser("test", help="Check provider connectivity with a tiny prompt")
    t.add_argument("--provider", help="Override the configured provider name")
    t.add_argument("--prompt", default="Reply with exactly: pong", help="Prompt to send")
    sub.add_parser("version", help="Print version")
    return p


def main(argv: list | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if getattr(args, "settings", None) and args.cmd != "setup":
        os.environ["CLAUDE_SETTINGS"] = args.settings
    commands = {
        "setup": setup.run,
        "doctor": doctor.run,
        "update": update.run,
        "install": _install,
        "uninstall": uninstall.run,
        "status": _status,
        "test": _test,
        "version": _version,
    }
    if args.cmd not in commands:
        parser.print_help()
        return 2
    return commands[args.cmd](args)


def _install(args) -> int:
    path = settings.install()
    ensure_file()
    print("Installed hooks into %s" % path)
    print(json.dumps(settings.status(path), indent=2))
    if provider_config(load()) is None:
        print("No provider is set yet, so nothing will be sent. Run `baton setup`.")
    return 0


def _status(args) -> int:
    cfg = load()
    print(json.dumps({**settings.status(), "config": str(config_path()),
                      "provider": cfg.get("provider"), "enabled": cfg.get("enabled")}, indent=2))
    return 0


def _test(args) -> int:
    keys.load_into_env()
    cfg = load()
    if args.provider:
        cfg["provider"] = args.provider
    pcfg = provider_config(cfg)
    if pcfg is None:
        print("No provider configured.", file=sys.stderr)
        return 1
    try:
        out = providers.get(pcfg).complete(args.prompt)
    except Exception as e:  # noqa: BLE001
        print("FAIL: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 1
    print("OK (%s / %s):\n%s" % (pcfg.get("name"), pcfg.get("type"), (out or "")[:500]))
    return 0


def _version(args) -> int:
    print(__version__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
