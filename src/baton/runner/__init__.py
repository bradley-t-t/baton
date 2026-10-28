"""Run the shell block a provider's answer holds.

The provider is asked for one ```bash or ```sh fence. Its body runs verbatim as a temp
script with bash, in the Claude session's cwd, without API keys in its environment. On
Windows that bash is Git Bash; without Git Bash the provider is asked for a
```powershell fence instead, and PowerShell runs it.
Every simple command (each part of a chain, pipe, loop body or $(...)) has to be on
the allow list unless run_actions.allow is "all", and run_actions.max_commands caps
how many there are. A block that fails a check is not run at all.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time

from .. import keys, system
from ..state import log
from .shell import extract_fences, first_word, logical_lines, script_commands, strip_fences, writes_file
from .summary import GLOB, describe, failure_line, outcome, still_there

ALL = "all"
# How each shell's script file starts: stop at the first failing command.
SCRIPT_HEADER = {"bash": "#!/usr/bin/env bash\nset -e\n",
                 "powershell": "$ErrorActionPreference = 'Stop'\n"}
SCRIPT_SUFFIX = {"bash": ".sh", "powershell": ".ps1"}
DEFAULT_MAX_COMMANDS = 20
KEY_NAMES = ("XAI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GITHUB_TOKEN",
             "BATON_TOKEN", "CLAUDE_HANDOFF_TOKEN")


def _allow_value(cfg: dict, provider_name: str):
    """run_actions.allow for this provider: a dict maps provider names (or "*")."""
    raw = (cfg.get("run_actions") or {}).get("allow")
    if isinstance(raw, dict):
        return raw.get(provider_name) or raw.get("*")
    return raw


def allows_all(cfg: dict, provider_name: str) -> bool:
    """run_actions.allow is "all" (or a list holding "all" or "*"): any command may run.
    The cap on commands per block and the timeout still apply."""
    raw = _allow_value(cfg, provider_name)
    if isinstance(raw, str):
        raw = [raw]
    return isinstance(raw, list) and any(str(a).strip().lower() in (ALL, "*") for a in raw)


def allowlist(cfg: dict, provider_name: str) -> set:
    raw = _allow_value(cfg, provider_name)
    return set(str(a) for a in raw) if isinstance(raw, list) else set()


def max_commands(cfg: dict) -> int:
    try:
        n = int((cfg.get("run_actions") or {}).get("max_commands") or DEFAULT_MAX_COMMANDS)
    except (TypeError, ValueError):
        n = DEFAULT_MAX_COMMANDS
    return max(1, n)


def limits_note(cfg: dict, provider_name: str) -> str:
    """The runner's limits, for the provider's system prompt, so it writes a script
    that will actually run. Empty when commands are not run at all."""
    ra = cfg.get("run_actions") or {}
    allow = allowlist(cfg, provider_name)
    every = allows_all(cfg, provider_name)
    if not ra.get("enabled") or not (allow or every):
        return ""
    extra = " Writing to files with > or >> is not allowed." if ra.get("read_only") else ""
    if every:
        limits = ("Runner limits: the block may have at most %d commands, counting each "
                  "command in a chain, pipe or loop body. A block over the limit is not run "
                  "at all. When the same command repeats, use one for-loop instead of a line "
                  "per item.%s" % (max_commands(cfg), extra))
    else:
        limits = ("Runner limits: the block may have at most %d commands, counting each "
                  "command in a chain, pipe or loop body, and each command has to be one of: "
                  "%s. A block over the limit or with any other command is not run at all. "
                  "When the same command repeats, use one for-loop instead of a line per "
                  "item.%s" % (max_commands(cfg), ", ".join(sorted(allow)), extra))
    return " ".join(n for n in (shell_note(), limits) if n)


def shell_note() -> str:
    """What the provider needs to know about the shell on Windows; empty elsewhere."""
    if not system.windows():
        return ""
    python = system.python_command()
    run_python = "" if python == "python3" else " Run Python as %s, not python3." % python
    if system.script_shell() == "bash":
        return ("This computer runs Windows. The block runs in Git Bash, so ~ is the home "
                "folder and paths take forward slashes: C:\\Users is /c/Users." + run_python)
    return ("This computer runs Windows without bash. Tag the block powershell instead of "
            "bash: it runs as a PowerShell script, so use PowerShell commands such as "
            "New-Item, Set-Content, Get-ChildItem and Remove-Item." + run_python)


def script_env(cfg: dict) -> dict:
    """This environment minus API keys, so a script can't read or print them."""
    hidden = set(keys.read()) | set(KEY_NAMES)
    for p in (cfg.get("providers") or {}).values():
        if isinstance(p, dict) and p.get("api_key_env"):
            hidden.add(p["api_key_env"])
    return {k: v for k, v in os.environ.items() if k not in hidden}


def _result(ran: bool, ok: bool, summary: str = "", output: str = "", not_run: str = "") -> dict:
    out = {"ran": ran, "ok": ok, "summary": summary, "log": output or summary}
    if not_run:
        out["not_run"] = not_run
    return out


def _refused(msg: str, why: str) -> dict:
    log("run_actions not run (%s): %s" % (why, msg[:120]))
    return _result(True, False, msg, not_run=why)


def run_reply(text: str, cfg: dict, provider_name: str, cwd: str = "") -> dict:
    """Run the shell block in a provider's answer, when commands are on.

    Returns {"ran", "ok", "summary", "log"} plus "not_run" ("too_many" or
    "not_allowed") when a block was refused. "ran" is False when there was nothing
    to run; "summary" is the plain result to show.
    """
    nothing = _result(False, True)
    ra = cfg.get("run_actions") or {}
    every = allows_all(cfg, provider_name)
    allow = allowlist(cfg, provider_name)
    if not ra.get("enabled") or not (allow or every):
        return nothing
    fences = extract_fences(text)
    body = "\n\n".join(fences)
    simple = script_commands(body)
    if not simple:
        return nothing

    # The block runs as one script, so it is all or nothing: skipping some lines would
    # change what the rest does. handoff.complete() asks once more within the limits.
    cap = max_commands(cfg)
    if len(simple) > cap:
        return _refused("Nothing was run: the script had %d commands and the limit is %d "
                        "(run_actions.max_commands)." % (len(simple), cap), "too_many")
    rejected = [] if every else [c for c in simple if first_word(c) not in allow]
    if rejected:
        return _refused("Nothing was run: %s is not on the allow list."
                        % first_word(rejected[0]), "not_allowed")
    lines = logical_lines(body)
    if ra.get("read_only") and any(writes_file(line) for line in lines):
        return _refused("Nothing was run: read-only mode doesn't allow writing to files.",
                        "not_allowed")
    workdir = os.path.expanduser(cwd) if cwd else os.getcwd()
    if not os.path.isdir(workdir):
        workdir = os.getcwd()
    timeout = int(ra.get("timeout_seconds") or 60)
    return _run_script(body, lines, text, cfg, workdir, timeout)


def _run_script(body: str, lines: list, text: str, cfg: dict, workdir: str, timeout: int) -> dict:
    # Which delete targets exist now, so the result never claims a delete of
    # something that was already gone.
    before = {q: os.path.lexists(q) for q in outcome(lines, workdir)["deleted"]
              if not GLOB.search(q) and "$" not in q}
    started = time.monotonic()
    path = None
    try:
        shell = system.script_shell()
        fd, path = tempfile.mkstemp(prefix="baton-", suffix=SCRIPT_SUFFIX[shell])
        # Windows PowerShell reads a file without a byte order mark as ANSI.
        with os.fdopen(fd, "w", encoding="utf-8-sig" if shell == "powershell" else "utf-8") as f:
            # The body runs verbatim so quotes, loops and python -c stay intact.
            f.write(SCRIPT_HEADER[shell] + body + ("" if body.endswith("\n") else "\n"))
        os.chmod(path, 0o700)
        p = subprocess.run(system.script_command(path), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout, cwd=workdir,
                           env=script_env(cfg))
        output = ((p.stdout or "") + (("\n" + p.stderr) if p.stderr else ""))[:8000]
        output = "$ %s <fence script>\nexit %s (%.1fs)\n%s" % (
            shell, p.returncode, time.monotonic() - started, output)
        log("run_actions script %d lines -> %s" % (len(lines), p.returncode))
        if p.returncode != 0:
            return _result(True, False, failure_line(p.stderr or p.stdout, p.returncode), output)
        summary = describe(lines, prose=strip_fences(text), cwd=workdir, before=before)
        failed = bool(still_there(outcome(lines, workdir)["deleted"])) or False in before.values()
        return _result(True, not failed, summary, output)
    except subprocess.TimeoutExpired:
        log("run_actions timeout")
        return _result(True, False, "It failed: timed out after %ss." % timeout)
    except Exception as e:  # noqa: BLE001
        log("run_actions error: %s" % e)
        return _result(True, False, "It failed: %s." % e)
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
