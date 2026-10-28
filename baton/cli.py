"""Command-line entry: setup, doctor, install, uninstall, status, test, version, and the hook subcommands."""

from __future__ import annotations

import argparse
import json
import sys


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    p = argparse.ArgumentParser(prog="baton",
                                description="A Claude Code hook that adds answers from a second model you configure.")
    from . import __version__
    p.add_argument("--version", action="version", version="baton " + __version__)
    sub = p.add_subparsers(dest="cmd")

    from .wizard import add_arguments
    add_arguments(sub.add_parser("setup", help="Choose a provider, store its key and install the hooks"))
    d = sub.add_parser("doctor", help="Check the config, key and hooks")
    d.add_argument("--online", action="store_true", help="Also send a test prompt")
    d.add_argument("--settings", help="Claude Code settings file")
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
    # Hook entry points (also available as `python3 -m baton.hooks`)
    sub.add_parser("prompt", help="UserPromptSubmit hook (stdin JSON)")
    sub.add_parser("stop", help="Stop hook (stdin JSON)")
    c = sub.add_parser("check", help="Judge a draft file before Claude sends it")
    c.add_argument("file", help="Path to the draft file")
    c.add_argument("--session", default="", help="Override CLAUDE_CODE_SESSION_ID")
    c.add_argument("--cwd", default="", help="Working directory for run_actions")
    c.add_argument("--transcript", default="", help="Transcript JSONL path")

    args = p.parse_args(argv)
    if not args.cmd:
        p.print_help()
        return 2
    if getattr(args, "settings", None) and args.cmd != "setup":
        import os
        os.environ["CLAUDE_SETTINGS"] = args.settings

    if args.cmd == "setup":
        from . import wizard
        return wizard.run(args)

    if args.cmd == "doctor":
        from . import doctor
        return doctor.run(args)

    if args.cmd == "version":
        from . import __version__
        print(__version__)
        return 0

    if args.cmd == "install":
        from . import install as inst
        moved = inst.migrate_old_files()
        path = inst.install()
        for m in moved:
            print("Copied %s from ~/.config/claude-handoff/" % m)
        print("Installed hooks into %s" % path)
        print(json.dumps(inst.status(path), indent=2))
        from .config import load, provider_config
        if provider_config(load()) is None:
            print("No provider is set yet, so nothing will be sent. Run `baton setup`.")
        return 0

    if args.cmd == "uninstall":
        return _uninstall(args)

    if args.cmd == "status":
        from . import install as inst
        from .config import config_path, load
        print(json.dumps({**inst.status(), "config": str(config_path()),
                          "provider": load().get("provider"),
                          "enabled": load().get("enabled")}, indent=2))
        return 0

    if args.cmd == "test":
        from . import keys
        keys.load_into_env()
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

    if args.cmd == "check":
        from .check import check
        return check(args.file, session=args.session, cwd=args.cwd,
                     transcript=args.transcript)

    p.print_help()
    return 2

if __name__ == "__main__":
    raise SystemExit(main())


def _uninstall(args) -> int:
    import os
    import shlex
    import shutil
    from . import install as inst
    from . import ui
    purge = args.purge or args.all
    remove = inst.removal_command()
    remove_text = " ".join(shlex.quote(x) for x in remove)

    path = inst.uninstall()
    print("Removed Baton's hooks from %s" % path)
    if purge:
        targets = [q for q in inst.data_paths() if q.exists()] + inst.backups(path)
        if targets:
            print("This deletes:")
            for q in targets:
                print("  %s" % q)
            if not args.yes:
                if not ui.interactive():
                    print("Pass --yes to delete them without a prompt.", file=sys.stderr)
                    return 1
                if not ui.confirm("Delete these, including any API keys in them?", False):
                    print("Kept them.")
                    return 0
            for q in inst.purge(targets):
                print("Deleted %s" % q)
        else:
            print("No Baton config or logs to delete.")
    else:
        print("Config and keys are still in ~/.config/baton. `baton uninstall --purge` deletes them.")

    if not remove:
        return 0
    if not args.all:
        print("To remove the baton command too: %s" % remove_text)
        print("Or run `baton uninstall --all` to do everything in one step.")
        return 0
    if not shutil.which(remove[0]):
        print("Last step, remove the baton command: %s" % remove_text)
        return 0
    if not args.yes and not ui.confirm("Remove the baton command now (%s)?" % remove_text, True):
        print("Kept it. To remove it later: %s" % remove_text)
        return 0
    print("Running: %s" % remove_text)
    sys.stdout.flush()
    sys.stderr.flush()
    os.execvp(remove[0], remove)  # replaces this process, so baton can remove itself
