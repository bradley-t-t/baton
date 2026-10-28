"""Run shell commands the provider returns in a fenced bash/sh block.

Convention (README + provider system prompt): one ```bash / ```sh fence. The
runner writes the fence body to a temp script and runs it with `bash` in the
Claude session's cwd. ~ is expanded by bash. The allow list is checked for every
simple command, including each part of a chain, pipe, loop body or $(...), and
run_actions.max_commands caps how many there can be. A block that fails either
check is not run at all. Off unless run_actions.enabled is true.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

from .state import log

FENCE = re.compile(r"```(?:bash|sh|shell|zsh|fish)\s*\n(.*?)```", re.S | re.I)
BARE_FENCE = re.compile(r"```\s*\n(.*?)```", re.S)

PREFIXES = {"sudo", "command", "time", "nice", "nohup", "env"}

# Shell keywords / punctuation that are not executables for the allow-list scan.
KEYWORDS = {
    "if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done",
    "case", "esac", "in", "select", "function", "time", "coproc", "!", "{", "}",
    "[[", "]]", "(", ")", ":", "true", "false",
}


def extract_fences(text: str) -> list:
    """Bodies of bash/sh fenced blocks (prefer tagged fences; else bare)."""
    blocks = list(FENCE.finditer(text or ""))
    if not blocks:
        blocks = list(BARE_FENCE.finditer(text or ""))
    return [m.group(1) for m in blocks]


def extract_commands(text: str) -> list:
    """Logical command lines from fences (continuations joined; comments skipped).

    Kept for tests and allow-list scanning. Does not re-quote or re-split tokens.
    """
    out = []
    for body in extract_fences(text):
        out.extend(_logical_lines(body))
    return out


def _logical_lines(body: str) -> list:
    """Join lines ending in \\; skip blanks and # comments."""
    lines = []
    buf = ""
    for raw in (body or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not buf and (not stripped or stripped.startswith("#")):
            continue
        if line.endswith("\\") and not line.endswith("\\\\"):
            buf += line[:-1]
            continue
        buf += line
        s = buf.strip()
        buf = ""
        if s and not s.startswith("#"):
            lines.append(s)
    if buf.strip() and not buf.strip().startswith("#"):
        lines.append(buf.strip())
    return lines


def strip_fences(text: str) -> str:
    t = FENCE.sub("", text or "")
    t = BARE_FENCE.sub("", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def first_word(cmd: str) -> str:
    """The executable token after sudo/env/... prefixes and VAR=val assignments."""
    try:
        parts = shlex.split(cmd, posix=True)
    except ValueError:
        # Unbalanced quotes: fall back to a rough first token so allow-list still works.
        parts = re.split(r"\s+", cmd.strip(), maxsplit=8)
    if not parts:
        return ""
    # Control-structure lines are not executables for the allow-list.
    head = Path(parts[0].lstrip("./")).name
    if head in {"for", "while", "until", "if", "elif", "else", "fi", "do", "done",
                "case", "esac", "then", "select", "function"}:
        return ""
    i = 0
    while i < len(parts):
        tok = parts[i]
        if not tok:
            i += 1
            continue
        # VAR=value prefix
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok) and "/" not in tok.split("=", 1)[0]:
            i += 1
            continue
        base = tok.lstrip("./")
        name = Path(base).name
        if name in PREFIXES or tok.startswith("-"):
            if name == "env":
                i += 1
                while i < len(parts) and "=" in parts[i] and not parts[i].startswith("-"):
                    i += 1
                continue
            i += 1
            continue
        if name in KEYWORDS:
            i += 1
            continue
        return name
    return ""


def allowlist(cfg: dict, provider_name: str) -> set:
    ra = cfg.get("run_actions") or {}
    raw = ra.get("allow")
    if isinstance(raw, list):
        return set(str(a) for a in raw)
    if isinstance(raw, dict):
        items = raw.get(provider_name) or raw.get("*") or []
        return set(str(a) for a in items)
    return set()


# Words that open or close a compound command; what follows them is the command.
LEADING = {"do", "then", "else", "elif", "if", "while", "until", "{", "}", "!", "time",
           "done", "fi", "esac"}
BACKTICK = re.compile(r"`([^`]*)`")


def simple_commands(line: str) -> list:
    """Every simple command in one logical line, for the allow list and the cap.

    Splits at && || | ; & and at ( ) so chains, loop bodies, subshells and $(...)
    are each checked, not just the first word of the line. Commands inside
    backticks are checked too. A for/select/case header runs nothing and is
    skipped. Text that can't be tokenized is returned whole, which fails closed
    unless its first word is allowed.
    """
    out = []
    for inner in BACKTICK.findall(line):
        out.extend(simple_commands(inner))
    line = BACKTICK.sub(" x ", line)
    try:
        lex = shlex.shlex(line, posix=True, punctuation_chars=";&|()")
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        return out + [line]
    seg = []
    def flush():
        while seg and seg[0] in LEADING:
            seg.pop(0)
        if seg and seg[0] == "[":
            seg[0] = "test"  # [ is test; [[ ]] is shell syntax that runs nothing itself
        if seg and seg[0] not in ("for", "select", "case", "in", "[["):
            out.append(shlex.join(seg))
        seg.clear()
    for tok in toks:
        if tok and set(tok) <= set(";&|()"):
            flush()
        else:
            seg.append(tok)
    flush()
    return out


def script_commands(body: str) -> list:
    """All simple commands in a script, each with a real executable as its first word."""
    out = []
    for line in _logical_lines(body):
        out.extend(c for c in simple_commands(line) if first_word(c))
    return out


OPERATORS = {"&&", "||", "|", ";", "&", ";;", "|&"}
SHELL_SYNTAX = re.compile(r"&&|\|\||(?<![\w.])\|(?![\w.])|;\s|\$\(|`[^`]*\s[^`]*`|\s2?>\s*/dev/null|^\s*(?:rm|ls|grep|echo|printf|mv|cp|mkdir|touch|cat|sudo)\s")
GLOB = re.compile(r"[*?\[]")


def _segments(cmd: str) -> list:
    """Split one command line into simple commands at && || | ; so a chain like
    `rm f && ls dir | grep f || echo ok` never reads as paths of rm."""
    try:
        lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        toks = cmd.split()
    segs, cur = [], []
    for tok in toks:
        if tok in OPERATORS or set(tok) <= set("&|;"):
            if cur:
                segs.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        segs.append(cur)
    return segs


def _verb_args(parts: list):
    """(executable name, args) of a simple command, skipping sudo/env/VAR= prefixes."""
    i = 0
    while i < len(parts):
        tok = parts[i]
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
            i += 1
            continue
        name = Path(tok.lstrip("./")).name
        if name in PREFIXES or name in KEYWORDS:
            i += 1
            continue
        return name, parts[i + 1:]
    return "", []


def _is_redir(tok: str) -> bool:
    return bool(tok) and set(tok) <= set("<>&|") and ("<" in tok or ">" in tok)


def _redirect_targets(args: list) -> list:
    out = []
    for i, a in enumerate(args):
        if _is_redir(a) and ">" in a and "&" not in a and i + 1 < len(args):
            if args[i + 1] != "/dev/null" and not (i > 0 and args[i - 1] == "2"):
                out.append(args[i + 1])
    return out


def _plain_args(args: list) -> list:
    out, skip = [], False
    for i, a in enumerate(args):
        if skip:
            skip = False
            continue
        if _is_redir(a):
            skip = True
            continue
        if a.isdigit() and i + 1 < len(args) and _is_redir(args[i + 1]):
            continue
        if a.startswith("-"):
            continue
        out.append(os.path.expanduser(a))
    return out


def _nice(path: str) -> str:
    """~-relative path; Trash items read as a name plus where they were."""
    home = os.path.expanduser("~")
    p = os.path.normpath(path)
    if p == home or p.startswith(home + os.sep):
        p = "~" + p[len(home):]
    return p


def _in_trash(path: str) -> bool:
    p = os.path.normpath(os.path.expanduser(path))
    return "/.Trash/" in p + "/" or "/.Trashes/" in p + "/" or "/.local/share/Trash/" in p + "/"


def _verify_gone(paths: list) -> list:
    """Paths that still exist after the run (globs and $vars are not checked)."""
    return [p for p in paths if not GLOB.search(p) and "$" not in p and os.path.lexists(p)]


def outcome(cmds: list, cwd: str = "") -> dict:
    """What the commands did to which paths, read from parsed simple commands."""
    deleted, created, moved, copied = [], [], [], []
    for cmd in cmds:
        for seg in _segments(cmd):
            verb, args = _verb_args(seg)
            absol = lambda q: q if (os.path.isabs(q) or not cwd or "$" in q) else os.path.join(cwd, q)
            paths = [absol(q) for q in _plain_args(args)]
            redir = [absol(os.path.expanduser(r)) for r in _redirect_targets(args)]
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


def _clean_prose(prose: str) -> str:
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


def summarize_success(cmds: list, results: list, prose: str = "", cwd: str = "",
                      before: dict = None) -> str:
    """One plain sentence per effect, built from what ran and checked on disk.
    Never echoes command text."""
    o = outcome(cmds, cwd)
    lines = []
    if o["deleted"]:
        before = before or {}
        missing = [p for p in o["deleted"] if before.get(p) is False]
        still = _verify_gone(o["deleted"])
        gone = [p for p in o["deleted"] if p not in still and p not in missing]
        if missing:
            lines.append("Nothing to delete: %s %s already gone." % (
                ", ".join(_nice(p) for p in missing), "was" if len(missing) == 1 else "were"))
        trash = [p for p in gone if _in_trash(p)]
        plain = [p for p in gone if not _in_trash(p)]
        if plain:
            lines.append("Deleted %s." % ", ".join(_nice(p) for p in plain))
        if trash:
            names = ", ".join(Path(p).name for p in trash)
            lines.append("Permanently deleted %s (it was in the Trash)." % names
                         if len(trash) == 1 else
                         "Permanently deleted %s (they were in the Trash)." % names)
        if still:
            lines.append("Couldn't delete %s; it's still there." % ", ".join(_nice(p) for p in still))
    created = [p for p in o["created"] if "$" not in p]
    if created:
        if len(created) >= 3:
            parent = _nice(str(Path(created[0]).parent))
            names = [Path(p).name for p in created]
            lines.append("Created %d files in %s: %s." % (len(created), parent, ", ".join(names)))
        else:
            lines.append("Created %s." % ", ".join(_nice(p) for p in created))
    for a, b in o["moved"]:
        lines.append("Moved %s to %s." % (_nice(a), _nice(b)))
    for a, b in o["copied"]:
        lines.append("Copied %s to %s." % (_nice(a), _nice(b)))
    if lines:
        return "\n".join(lines)
    clean = _clean_prose(prose)
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


def max_commands(cfg: dict) -> int:
    ra = cfg.get("run_actions") or {}
    try:
        n = int(ra.get("max_commands") or 20)
    except (TypeError, ValueError):
        n = 20
    return max(1, n)


def limits_note(cfg: dict, provider_name: str) -> str:
    """The runner's limits, for the provider's system prompt, so it writes a script
    that will actually run. Empty when commands are not run at all."""
    ra = cfg.get("run_actions") or {}
    allow = allowlist(cfg, provider_name)
    if not ra.get("enabled") or not allow:
        return ""
    return ("Runner limits: the block may have at most %d commands, counting each "
            "command in a chain, pipe or loop body, and each command has to be one of: %s. A block over "
            "the limit or with any other command is not run at all. When the same "
            "command repeats, use one for-loop instead of a line per item."
            % (max_commands(cfg), ", ".join(sorted(allow))))


def run_allowed(text: str, cfg: dict, provider_name: str, cwd: str = "") -> dict:
    """Run allowed commands from text by writing each fence to a temp script.

    Returns {
      "ran": bool, "ok": bool, "commands": [str], "results": [...],
      "summary": str, "log": str,
    }
    """
    empty = {"ran": False, "ok": True, "commands": [], "results": [], "summary": "", "log": ""}
    ra = cfg.get("run_actions") or {}
    if not ra.get("enabled"):
        return empty
    allow = allowlist(cfg, provider_name)
    if not allow:
        return empty
    timeout = int(ra.get("timeout_seconds") or 60)
    max_n = max_commands(cfg)
    fences = extract_fences(text)
    if not fences:
        return empty

    # Prefer a single combined script from all fences (one provider block is the norm).
    body = "\n\n".join(fences)
    simple = script_commands(body)
    if not simple:
        return empty
    all_cmds = _logical_lines(body)

    # The whole block runs as one script, so it is all or nothing: skipping some
    # lines would change what the rest of the script does. A block over the cap or
    # with a command off the allow list is not run at all, and the result says so
    # plainly. complete() asks the provider once more within the limits first.
    if len(simple) > max_n:
        msg = ("Nothing was run: the script had %d commands and the limit is %d "
               "(run_actions.max_commands)." % (len(simple), max_n))
        log("run_actions over cap: %d > %d" % (len(simple), max_n))
        return {"ran": True, "ok": False, "commands": [], "results": [],
                "summary": msg, "log": msg, "not_run": "too_many"}
    rejected = [c for c in simple if first_word(c) not in allow]
    if rejected:
        msg = "Nothing was run: %s is not on the allow list." % first_word(rejected[0])
        log("run_actions rejected: %s" % rejected[0][:80])
        return {"ran": True, "ok": False, "commands": [], "results": [],
                "summary": msg, "log": msg, "not_run": "not_allowed"}
    allowed_cmds = all_cmds
    # Run the fence body verbatim so quotes, loops, and python -c stay intact.
    script = body if body.endswith("\n") else body + "\n"
    workdir = os.path.expanduser(cwd) if cwd else os.getcwd()
    if not os.path.isdir(workdir):
        workdir = os.getcwd()

    # Which delete targets exist now, so the result never claims a delete
    # of something that was already gone.
    before = {q: os.path.lexists(q) for q in outcome(allowed_cmds, workdir)["deleted"]
              if not GLOB.search(q) and "$" not in q}
    started = time.monotonic()
    path = None
    try:
        fd, path = tempfile.mkstemp(prefix="baton-", suffix=".sh")
        with os.fdopen(fd, "w") as f:
            f.write("#!/usr/bin/env bash\nset -e\n")
            f.write(script)
        os.chmod(path, 0o700)
        p = subprocess.run(
            ["bash", path],
            capture_output=True, text=True, timeout=timeout, cwd=workdir,
            env={**os.environ},
        )
        rec = {"cmd": "(script %d cmds)" % len(allowed_cmds), "exit": p.returncode,
               "stdout": p.stdout or "", "stderr": p.stderr or ""}
        blob = (rec["stdout"] + (("\n" + rec["stderr"]) if rec["stderr"] else ""))[:8000]
        log_blob = "$ bash <fence script>\nexit %s (%.1fs)\n%s" % (
            p.returncode, time.monotonic() - started, blob)
        log("run_actions script %d cmds -> %s" % (len(allowed_cmds), p.returncode))
        if p.returncode != 0:
            # Prefer a short cmd hint from the first allowed line for the error.
            summary = failure_line(rec["stderr"] or rec["stdout"], p.returncode)
            return {"ran": True, "ok": False, "commands": allowed_cmds,
                    "results": [rec], "summary": summary, "log": log_blob}
        prose = strip_fences(text)
        summary = summarize_success(allowed_cmds, [rec], prose=prose, cwd=workdir, before=before)
        still = _verify_gone(outcome(allowed_cmds, workdir)["deleted"])
        if any(v is False for v in before.values()):
            still = still or [None]
        return {"ran": True, "ok": not still, "commands": allowed_cmds, "results": [rec],
                "summary": summary, "log": log_blob}
    except subprocess.TimeoutExpired:
        summary = "It failed: timed out after %ss." % timeout
        log("run_actions timeout")
        return {"ran": True, "ok": False, "commands": allowed_cmds, "results": [],
                "summary": summary, "log": summary}
    except Exception as e:  # noqa: BLE001
        summary = "It failed: %s." % e
        log("run_actions error: %s" % e)
        return {"ran": True, "ok": False, "commands": allowed_cmds, "results": [],
                "summary": summary, "log": summary}
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
