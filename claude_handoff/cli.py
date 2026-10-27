"""Command-line entry: install, uninstall, status, test, version, and the hook subcommands."""

from __future__ import annotations

import argparse
import json
import sys


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    p = argparse.ArgumentParser(prog="claude-handoff",
                                description="Hand a declined Claude Code request to another AI provider.")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("install", help="Add hooks to ~/.claude/settings.json")
    sub.add_parser("uninstall", help="Remove our hooks from ~/.claude/settings.json")
    sub.add_parser("status", help="Show whether hooks are installed")
    t = sub.add_parser("test", help="Check provider connectivity with a tiny prompt")
    t.add_argument("--provider", help="Override the configured provider name")
    t.add_argument("--prompt", default="Reply with exactly: pong", help="Prompt to send")
    sub.add_parser("version", help="Print version")
    # Hook entry points (also available as `python3 -m claude_handoff.hooks`)
    sub.add_parser("prompt", help="UserPromptSubmit hook (stdin JSON)")
    sub.add_parser("stop", help="Stop hook (stdin JSON)")

    args = p.parse_args(argv)
    if not args.cmd:
        p.print_help()
        return 2

    if args.cmd == "version":
        from . import __version__
        print(__version__)
        return 0

    if args.cmd == "install":
        from . import install as inst
        path = inst.install()
        print("Installed hooks into %s" % path)
        print(json.dumps(inst.status(path), indent=2))
        return 0

    if args.cmd == "uninstall":
        from . import install as inst
        path = inst.uninstall()
        print("Removed hooks from %s" % path)
        print(json.dumps(inst.status(path), indent=2))
        return 0

    if args.cmd == "status":
        from . import install as inst
        from .config import config_path, load
        print(json.dumps({**inst.status(), "config": str(config_path()),
                          "provider": load().get("provider"),
                          "enabled": load().get("enabled")}, indent=2))
        return 0

    if args.cmd == "test":
        from .config import load, provider_config
        from .providers import get
        cfg = load()
        if args.provider:
            cfg["provider"] = args.provider
        pcfg = provider_config(cfg)
        if pcfg is None:
            print("No provider configured.", file=sys.stderr)
            return 1
        try:
            provider = get(pcfg)
            out = provider.complete(args.prompt)
        except Exception as e:  # noqa: BLE001
            print("FAIL: %s: %s" % (type(e).__name__, e), file=sys.stderr)
            return 1
        print("OK (%s / %s):\n%s" % (pcfg.get("name"), pcfg.get("type"), (out or "")[:500]))
        return 0

    if args.cmd in ("prompt", "stop"):
        from . import hooks
        return hooks.main([args.cmd])

    p.print_help()
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
