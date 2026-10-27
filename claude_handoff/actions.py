"""Run shell commands the provider returns in a fenced bash/sh block.

Convention (documented in README and the provider system prompt): put the exact
commands in one ```bash or ```sh fence. The runner executes each line whose first
real token is on the allow-list, in the Claude session's cwd, with ~ expanded.
Off unless run_actions.enabled is true.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from .state import log

FENCE = re.compile(r"```(?:bash|sh|shell|zsh|fish)\s*\n(.*?)```", re.S | re.I)
# Also accept a bare ``` fence that clearly looks like shell (starts with a known cmd).
BARE_FENCE = re.compile(r"```\s*\n(.*?)```", re.S)

# Tokens stripped from the front before the allow-list check.
PREFIXES = {"sudo", "command", "time", "nice", "nohup", "env"}


def extract_commands(text: str) -> list:
    """Lines from bash/sh fenced blocks (comments and blanks skipped)."""
    out = []
    blocks = list(FENCE.finditer(text or ""))
    if not blocks:
        blocks = list(BARE_FENCE.finditer(text or ""))
    for m in blocks:
        for line in m.group(1).splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            out.append(s)
    return out


def strip_fences(text: str) -> str:
    """Provider prose with bash/sh fences removed."""
    t = FENCE.sub("", text or "")
    t = BARE_FENCE.sub("", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def first_word(cmd: str) -> str:
    """The executable token after sudo/env/... prefixes."""
    try:
        parts = shlex.split(cmd, posix=True)
    except ValueError:
        parts = cmd.split()
    i = 0
    while i < len(parts):
        tok = parts[i]
        base = tok.lstrip("./")
        name = Path(base).name
        if name in PREFIXES or tok.startswith("-"):
            # env FOO=bar cmd → skip assignments after env
            if name == "env":
                i += 1
                while i < len(parts) and "=" in parts[i] and not parts[i].startswith("-"):
                    i += 1
                continue
            i += 1
            continue
        return name
    return ""


def allowlist(cfg: dict, provider_name: str) -> set:
    """Accept allow as a flat list, or as a dict keyed by provider / '*'."""
    ra = cfg.get("run_actions") or {}
    raw = ra.get("allow")
    if isinstance(raw, list):
        return set(str(a) for a in raw)
    if isinstance(raw, dict):
        items = raw.get(provider_name) or raw.get("*") or []
        return set(str(a) for a in items)
    return set()


def _expand(cmd: str, cwd: str) -> str:
    """Expand ~ in the command for the user running the hook."""
    try:
        parts = shlex.split(cmd, posix=True)
    except ValueError:
        return os.path.expanduser(cmd)
    out = []
    for part in parts:
        if part == "~" or part.startswith("~/"):
            out.append(os.path.expanduser(part))
        else:
            out.append(part)
    return " ".join(shlex.quote(part) for part in out)


def summarize_success(cmds: list, results: list) -> str:
    """A short user-facing line for successful runs (prefer over raw dumps)."""
    lines = []
    for cmd, r in zip(cmds, results):
        fw = first_word(cmd)
        try:
            parts = shlex.split(cmd, posix=True)
        except ValueError:
            parts = cmd.split()
        # Drop prefixes to find args
        args = []
        i = 0
        while i < len(parts):
            name = Path(parts[i].lstrip("./")).name
            if name in PREFIXES or parts[i].startswith("-"):
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
        if fw == "rm" and paths:
            lines.append("Deleted %s." % " ".join(paths))
        elif fw in ("mkdir",) and paths:
            lines.append("Created %s." % " ".join(paths))
        elif fw in ("touch",) and paths:
            lines.append("Touched %s." % " ".join(paths))
        elif fw in ("mv",) and len(paths) >= 2:
            lines.append("Moved %s to %s." % (paths[0], paths[-1]))
        elif fw in ("cp",) and len(paths) >= 2:
            lines.append("Copied %s to %s." % (paths[0], paths[-1]))
        elif fw in ("echo",):
            out = (r.get("stdout") or "").rstrip("\n")
            if out:
                lines.append(out)
        else:
            out = ((r.get("stdout") or "") + (r.get("stderr") or "")).strip()
            if out:
                lines.append(out)
            else:
                lines.append("Ran `%s` (exit 0)." % cmd)
    return "\n".join(lines).strip() or "Done."


def run_allowed(text: str, cfg: dict, provider_name: str, cwd: str = "") -> dict:
    """Run allowed commands from text.

    Returns {
      "ran": bool,
      "ok": bool,
      "commands": [str],
      "results": [{cmd, exit, stdout, stderr}],
      "summary": str,   # clean user-facing text when ok, or failure note
      "log": str,       # detailed transcript for a follow-up round
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
    max_n = int(ra.get("max_commands") or 5)
    all_cmds = extract_commands(text)
    if not all_cmds:
        return empty
    # Reject disallowed commands rather than silently skipping them when any are present.
    rejected = [c for c in all_cmds if first_word(c) not in allow]
    cmds = [c for c in all_cmds if first_word(c) in allow][:max_n]
    if rejected and not cmds:
        msg = "Command not allowed: %s" % first_word(rejected[0])
        log("run_actions rejected: %s" % rejected[0][:80])
        return {"ran": True, "ok": False, "commands": [], "results": [],
                "summary": msg, "log": msg}
    if not cmds:
        return empty

    workdir = os.path.expanduser(cwd) if cwd else os.getcwd()
    if not os.path.isdir(workdir):
        workdir = os.getcwd()

    results = []
    log_parts = []
    for cmd in cmds:
        expanded = _expand(cmd, workdir)
        started = time.monotonic()
        try:
            p = subprocess.run(expanded, shell=True, capture_output=True, text=True,
                               timeout=timeout, cwd=workdir)
            rec = {"cmd": cmd, "exit": p.returncode,
                   "stdout": p.stdout or "", "stderr": p.stderr or ""}
            results.append(rec)
            blob = (rec["stdout"] + (("\n" + rec["stderr"]) if rec["stderr"] else ""))[:8000]
            log_parts.append("$ %s\nexit %s (%.1fs)\n%s" % (
                cmd, p.returncode, time.monotonic() - started, blob))
            log("run_actions %s -> %s" % (first_word(cmd), p.returncode))
            if p.returncode != 0:
                summary = "Command failed (`%s`, exit %s): %s" % (
                    cmd, p.returncode, (rec["stderr"] or rec["stdout"] or "(no output)").strip()[:500])
                return {"ran": True, "ok": False, "commands": cmds[:len(results)],
                        "results": results, "summary": summary, "log": "\n\n".join(log_parts)}
        except subprocess.TimeoutExpired:
            summary = "Command timed out after %ss: `%s`" % (timeout, cmd)
            log("run_actions timeout: %s" % cmd[:80])
            return {"ran": True, "ok": False, "commands": cmds[:len(results) + 1],
                    "results": results, "summary": summary, "log": "\n\n".join(log_parts + [summary])}
        except Exception as e:  # noqa: BLE001
            summary = "Command error (`%s`): %s: %s" % (cmd, type(e).__name__, e)
            log("run_actions error: %s" % e)
            return {"ran": True, "ok": False, "commands": cmds[:len(results) + 1],
                    "results": results, "summary": summary, "log": "\n\n".join(log_parts + [summary])}

    if rejected:
        # Ran the allowed ones; note the skipped.
        log("run_actions skipped disallowed: %s" % ", ".join(first_word(c) for c in rejected))

    summary = summarize_success(cmds, results)
    return {"ran": True, "ok": True, "commands": cmds, "results": results,
            "summary": summary, "log": "\n\n".join(log_parts)}
