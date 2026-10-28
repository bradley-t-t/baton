"""Baton's hooks in Claude Code's ~/.claude/settings.json: add, remove, inspect.

Each hook runs `python3 -m baton.hooks <event>` with this package on PYTHONPATH, after
sourcing ~/.config/baton/secrets.env when it exists. On Windows the hook starts Python
directly (exec form: "command" plus "args"), with no shell, because Claude Code would
otherwise run it through Git Bash or PowerShell depending on what is installed; the
hook loads the secrets file itself. Other hooks in the file are kept, and the file is
backed up before each change.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from .. import keys, system

# Strings that mark our own entries in the hooks lists. Baton used to be called
# claude-handoff, so entries from an old install count as ours too: install replaces
# them instead of running both, and uninstall removes them.
MARKERS = ("-m baton.hooks", "-m claude_handoff.hooks")

EVENTS = ("UserPromptSubmit", "Stop", "MessageDisplay")
HOOK_NAMES = {"UserPromptSubmit": "prompt", "Stop": "stop", "MessageDisplay": "display"}
DISPLAY_TIMEOUT = 120  # seconds: a provider call plus its commands

KEEP_BACKUPS = 3
BACKUP_NAME = re.compile(r"\.bak-\d{8}-\d{6}$")


def package_root() -> Path:
    """The folder that holds the baton package, put on the hooks' PYTHONPATH."""
    return Path(__file__).resolve().parent.parent.parent


def settings_path() -> Path:
    p = os.environ.get("CLAUDE_SETTINGS")
    if p:
        return Path(os.path.expanduser(p))
    return Path.home() / ".claude" / "settings.json"


def hook_command(event: str) -> str:
    """The shell command Claude Code runs for this event. Paths are absolute so it
    works from any cwd. API keys come from secrets.env and are never printed. It
    always exits 0, like the hooks themselves, so a hook that runs while an update is
    reinstalling Baton is skipped instead of flagged as an error."""
    run = 'PYTHONPATH="%s" "%s" -m baton.hooks %s || true' % (
        package_root(), sys.executable or "python3", HOOK_NAMES.get(event, "stop"))
    new, old = str(keys.secrets_path()), str(keys.old_secrets_path())
    return ('if [ -f "%s" ]; then set -a; . "%s"; set +a; '
            'elif [ -f "%s" ]; then set -a; . "%s"; set +a; fi; %s'
            % (new, new, old, old, run))


def hook_args(event: str) -> list:
    """The arguments of the Windows hook, whose command is this Python."""
    return ["-m", "baton.hooks", HOOK_NAMES.get(event, "stop")]


def hook_entry(event: str) -> dict:
    if system.windows():
        h = {"type": "command", "command": sys.executable, "args": hook_args(event)}
    else:
        h = {"type": "command", "command": hook_command(event)}
    if event == "MessageDisplay":
        h["timeout"] = DISPLAY_TIMEOUT
    return {"hooks": [h]}


def has_marker(cmd) -> bool:
    return any(m in (cmd or "") for m in MARKERS)


def command_text(h: dict) -> str:
    """A hook's command as one line: the shell command, or an exec-form program and its
    arguments with the program quoted."""
    args = h.get("args")
    if isinstance(args, list):
        return " ".join(['"%s"' % h.get("command", "")] + [str(a) for a in args])
    return str(h.get("command") or "")


def is_ours(entry) -> bool:
    """True for a hooks-list entry that runs Baton (or claude-handoff)."""
    if not isinstance(entry, dict):
        return False
    for h in entry.get("hooks") or []:
        if isinstance(h, dict) and has_marker(command_text(h)):
            return True
    return has_marker(command_text(entry))


def our_commands(data: dict, event: str) -> list:
    """The commands of our hooks for this event in loaded settings."""
    out = []
    for e in ((data.get("hooks") or {}).get(event) or []):
        if not isinstance(e, dict):
            continue
        for h in (e.get("hooks") or [e]):
            if isinstance(h, dict) and has_marker(command_text(h)):
                out.append(command_text(h))
    return out


def read(path: Path | None = None) -> dict:
    try:
        data = json.loads((path or settings_path()).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def backups(path: Path | None = None) -> list:
    """The settings backups Baton made, oldest first."""
    path = path or settings_path()
    if not path.parent.is_dir():
        return []
    return sorted(q for q in path.parent.glob(path.name + ".bak-*")
                  if BACKUP_NAME.search(q.name) and q.is_file())


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak-%s" % time.strftime("%Y%m%d-%H%M%S")))
        for old in backups(path)[:-KEEP_BACKUPS]:
            old.unlink()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def install(path: Path | None = None) -> Path:
    """Add our hooks, replacing any earlier Baton or claude-handoff ones. The file is
    left alone when they're already there as they'd be written."""
    path = path or settings_path()
    data = read(path)
    before = json.dumps(data, sort_keys=True)
    hooks = data.setdefault("hooks", {})
    for event in EVENTS:
        hooks[event] = [e for e in (hooks.get(event) or []) if not is_ours(e)] + [hook_entry(event)]
    if not path.exists() or json.dumps(data, sort_keys=True) != before:
        _write(path, data)
    return path


def uninstall(path: Path | None = None) -> Path:
    """Remove our hooks and leave everything else, dropping lists that end up empty."""
    path = path or settings_path()
    data = read(path)
    hooks = data.get("hooks") or {}
    changed = False
    for event in list(hooks):
        before = hooks[event] or []
        after = [e for e in before if not is_ours(e)]
        changed = changed or after != before
        if after:
            hooks[event] = after
        else:
            hooks.pop(event, None)
    if changed:
        if hooks:
            data["hooks"] = hooks
        else:
            data.pop("hooks", None)
        _write(path, data)
    return path


def status(path: Path | None = None) -> dict:
    path = path or settings_path()
    hooks = read(path).get("hooks") or {}
    out = {"settings": str(path)}
    for event in EVENTS:
        out[event] = any(is_ours(e) for e in hooks.get(event) or [])
    return out
