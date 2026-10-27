"""Run shell commands the provider returns in a fenced bash/sh block.

Convention (README + provider system prompt): one ```bash / ```sh fence. The
runner writes the fence body to a temp script and runs it with `bash` in the
Claude session's cwd. ~ is expanded by bash. Allow-list is checked per simple
command line (first real token). Off unless run_actions.enabled is true.
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


def scan_script_commands(body: str) -> list:
    """First-token scan of each logical line in a script (skip keywords/assignments)."""
    cmds = []
    for line in _logical_lines(body):
        # Drop leading pipe/and/or separators when a line continues a pipeline oddly.
        line = re.sub(r"^(?:\|\||&&|;|\|)\s*", "", line)
        fw = first_word(line)
        if fw:
            cmds.append(line)
    return cmds


def summarize_success(cmds: list, results: list, prose: str = "") -> str:
    """Short user-facing line. Prefer clean provider prose; else describe what ran."""
    prose = (prose or "").strip()
    # Provider often writes "Created 5 files…" before the fence — use that if present.
    if prose and len(prose) < 500 and not prose.lower().startswith("sure"):
        # Drop leftover meta lines.
        keep = []
        for line in prose.splitlines():
            s = line.strip()
            if not s:
                continue
            if re.search(r"\b(?:here(?:'s| is)|runn?ing|execut)", s, re.I) and len(s) < 40:
                continue
            keep.append(s)
        if keep:
            return "\n".join(keep)

    created, deleted, other = [], [], []
    for cmd in cmds:
        fw = first_word(cmd)
        try:
            parts = shlex.split(cmd, posix=True)
        except ValueError:
            parts = cmd.split()
        args = []
        i = 0
        while i < len(parts):
            tok = parts[i]
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
                i += 1
                continue
            name = Path(tok.lstrip("./")).name
            if name in PREFIXES or tok.startswith("-") or name in KEYWORDS:
                if name == "env":
                    i += 1
                    while i < len(parts) and "=" in parts[i]:
                        i += 1
                    continue
                i += 1
                continue
            args = parts[i + 1:]
            break
        paths = [os.path.expanduser(a) for a in args if a and not a.startswith("-")]
        # printf/echo redirects: look for > path in the raw command
        redir = re.findall(r"(?:^|[\s])>(?:\|)?\s*([^\s;|&]+)", cmd)
        redir_paths = [os.path.expanduser(p.strip("\"'")) for p in redir]
        if fw == "rm" and paths:
            deleted.extend(paths)
        elif fw in ("mkdir", "touch") and paths:
            created.extend(paths)
        elif fw in ("printf", "echo") and redir_paths:
            created.extend(redir_paths)
        elif fw == "cat" and redir_paths:
            created.extend(redir_paths)
        elif fw in ("mv",) and len(paths) >= 2:
            other.append("Moved %s to %s." % (paths[0], paths[-1]))
        elif fw in ("cp",) and len(paths) >= 2:
            other.append("Copied %s to %s." % (paths[0], paths[-1]))

    lines = []
    if deleted:
        lines.append("Deleted %s." % " ".join(deleted))
    if created:
        # Collapse many files under the same parent.
        if len(created) >= 3:
            parent = str(Path(created[0]).expanduser().parent)
            names = [Path(p).name for p in created]
            lines.append("Created %d files in %s: %s." % (len(created), parent, ", ".join(names)))
        else:
            lines.append("Created %s." % " ".join(created))
    lines.extend(other)
    if lines:
        return "\n".join(lines)
    out = ""
    for r in results:
        out += ((r.get("stdout") or "") + (r.get("stderr") or ""))
    out = out.strip()
    return out[:800] if out else "Done."


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
    max_n = int(ra.get("max_commands") or 20)
    fences = extract_fences(text)
    if not fences:
        return empty

    # Prefer a single combined script from all fences (one provider block is the norm).
    body = "\n\n".join(fences)
    all_cmds = scan_script_commands(body)
    if not all_cmds:
        return empty

    # Cap how many real commands we will accept in one fence.
    if len(all_cmds) > max_n:
        msg = "Too many commands in fence (%d > %d)" % (len(all_cmds), max_n)
        return {"ran": True, "ok": False, "commands": all_cmds[:max_n], "results": [],
                "summary": msg, "log": msg}
    rejected = [c for c in all_cmds if first_word(c) not in allow]
    if rejected:
        msg = "Command not allowed: %s" % first_word(rejected[0])
        log("run_actions rejected: %s" % rejected[0][:80])
        return {"ran": True, "ok": False, "commands": [], "results": [],
                "summary": msg, "log": msg}
    allowed_cmds = all_cmds
    # Run the fence body verbatim so quotes, loops, and python -c stay intact.
    script = body if body.endswith("\n") else body + "\n"
    workdir = os.path.expanduser(cwd) if cwd else os.getcwd()
    if not os.path.isdir(workdir):
        workdir = os.getcwd()

    started = time.monotonic()
    path = None
    try:
        fd, path = tempfile.mkstemp(prefix="claude-handoff-", suffix=".sh")
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
            hint = allowed_cmds[0][:120] if allowed_cmds else "script"
            err = (rec["stderr"] or rec["stdout"] or "(no output)").strip()[:500]
            summary = "Command failed (`%s`, exit %s): %s" % (hint, p.returncode, err)
            return {"ran": True, "ok": False, "commands": allowed_cmds,
                    "results": [rec], "summary": summary, "log": log_blob}
        prose = strip_fences(text)
        summary = summarize_success(allowed_cmds, [rec], prose=prose)
        return {"ran": True, "ok": True, "commands": allowed_cmds, "results": [rec],
                "summary": summary, "log": log_blob}
    except subprocess.TimeoutExpired:
        summary = "Command timed out after %ss" % timeout
        log("run_actions timeout")
        return {"ran": True, "ok": False, "commands": allowed_cmds, "results": [],
                "summary": summary, "log": summary}
    except Exception as e:  # noqa: BLE001
        summary = "Command error: %s: %s" % (type(e).__name__, e)
        log("run_actions error: %s" % e)
        return {"ran": True, "ok": False, "commands": allowed_cmds, "results": [],
                "summary": summary, "log": summary}
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
