"""Plain sentences about what a script did ("Deleted ~/x.txt."), never its text."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .shell import plain_args, redirect_targets, segments, verb_args

SHELL_SYNTAX = re.compile(r"&&|\|\||(?<![\w.])\|(?![\w.])|;\s|\$\(|`[^`]*\s[^`]*`|\s2?>\s*/dev/null|^\s*(?:rm|ls|grep|echo|printf|mv|cp|mkdir|touch|cat|sudo)\s")
GLOB = re.compile(r"[*?\[]")


def nice_path(path: str) -> str:
    """~-relative path; Trash items read as a name plus where they were."""
    home = os.path.expanduser("~")
    p = os.path.normpath(path)
    if p == home or p.startswith(home + os.sep):
        p = "~" + p[len(home):]
    return p


def _in_trash(path: str) -> bool:
    p = os.path.normpath(os.path.expanduser(path))
    return "/.Trash/" in p + "/" or "/.Trashes/" in p + "/" or "/.local/share/Trash/" in p + "/"


def still_there(paths: list) -> list:
    """Paths that still exist after the run (globs and $vars are not checked)."""
    return [p for p in paths if not GLOB.search(p) and "$" not in p and os.path.lexists(p)]


def _absolute(path: str, cwd: str) -> str:
    return path if (os.path.isabs(path) or not cwd or "$" in path) else os.path.join(cwd, path)


def outcome(cmds: list, cwd: str = "") -> dict:
    """What the commands did to which paths, read from parsed simple commands."""
    deleted, created, moved, copied = [], [], [], []
    for cmd in cmds:
        for seg in segments(cmd):
            verb, args = verb_args(seg)
            paths = [_absolute(q, cwd) for q in plain_args(args)]
            redir = [_absolute(os.path.expanduser(r), cwd) for r in redirect_targets(args)]
            if verb in ("rm", "unlink", "rmdir", "trash") and paths:
                deleted.extend(paths)
            elif verb in ("mkdir", "touch") and paths:
                created.extend(paths)
            elif verb in ("printf", "echo", "cat", "tee") and (redir or (verb == "tee" and paths)):
                created.extend(redir or paths)
            elif verb == "mv" and len(paths) >= 2:
                moved.append((paths[0], paths[-1]))
            elif verb == "cp" and len(paths) >= 2:
                copied.append((paths[0], paths[-1]))
    return {"deleted": deleted, "created": created, "moved": moved, "copied": copied}


def clean_prose(prose: str) -> str:
    """Provider prose with any shell-looking line dropped."""
    keep = []
    for line in (prose or "").splitlines():
        s = line.strip()
        if not s or SHELL_SYNTAX.search(s):
            continue
        if re.search(r"\b(?:here(?:'s| is)|runn?ing|execut)", s, re.I) and len(s) < 40:
            continue
        keep.append(s)
    return "\n".join(keep)


def describe(cmds: list, prose: str = "", cwd: str = "", before: dict | None = None) -> str:
    """One plain sentence per effect, built from what ran and checked on disk.
    Never echoes command text."""
    o = outcome(cmds, cwd)
    lines = []
    if o["deleted"]:
        before = before or {}
        missing = [p for p in o["deleted"] if before.get(p) is False]
        still = still_there(o["deleted"])
        gone = [p for p in o["deleted"] if p not in still and p not in missing]
        if missing:
            lines.append("Nothing to delete: %s %s already gone." % (
                ", ".join(nice_path(p) for p in missing), "was" if len(missing) == 1 else "were"))
        trash = [p for p in gone if _in_trash(p)]
        plain = [p for p in gone if not _in_trash(p)]
        if plain:
            lines.append("Deleted %s." % ", ".join(nice_path(p) for p in plain))
        if trash:
            names = ", ".join(Path(p).name for p in trash)
            lines.append("Permanently deleted %s (it was in the Trash)." % names
                         if len(trash) == 1 else
                         "Permanently deleted %s (they were in the Trash)." % names)
        if still:
            lines.append("Couldn't delete %s; it's still there." % ", ".join(nice_path(p) for p in still))
    created = [p for p in o["created"] if "$" not in p]
    if created:
        if len(created) >= 3:
            parent = nice_path(str(Path(created[0]).parent))
            names = [Path(p).name for p in created]
            lines.append("Created %d files in %s: %s." % (len(created), parent, ", ".join(names)))
        else:
            lines.append("Created %s." % ", ".join(nice_path(p) for p in created))
    for a, b in o["moved"]:
        lines.append("Moved %s to %s." % (nice_path(a), nice_path(b)))
    for a, b in o["copied"]:
        lines.append("Copied %s to %s." % (nice_path(a), nice_path(b)))
    if lines:
        return "\n".join(lines)
    clean = clean_prose(prose)
    if clean and len(clean) < 500 and not clean.lower().startswith("sure"):
        return clean
    return "Done."


def failure_line(stderr: str, code) -> str:
    """One plain line for a failed run, without the command text."""
    msg = ""
    for line in (stderr or "").splitlines():
        s = re.sub(r"^(?:bash|sh|/[^:]*\.sh)(?::\s*line\s*\d+)?:\s*", "", line.strip())
        if s:
            msg = s
            break
    msg = msg[:200].rstrip(".")
    return ("It failed: %s." % msg) if msg else ("It failed (exit %s)." % code)
