"""Optional run_actions: if the provider returns fenced shell commands, run the ones whose
first word is on the per-provider allow-list, with a timeout, and feed the output back once.
Off by default.
"""

from __future__ import annotations

import re
import subprocess
import time

from .state import log

FENCE = re.compile(r"```(?:bash|sh|shell|zsh|fish)?\n(.*?)```", re.S | re.I)


def extract_commands(text: str) -> list:
    out = []
    for m in FENCE.finditer(text or ""):
        block = m.group(1)
        for line in block.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            out.append(s)
    return out


def first_word(cmd: str) -> str:
    return (cmd.split(None, 1)[0] if cmd.strip() else "").lstrip("./")


def run_allowed(text: str, cfg: dict, provider_name: str) -> str:
    """Run allowed commands found in text; return an appendix of their outputs (or "")."""
    ra = cfg.get("run_actions") or {}
    if not ra.get("enabled"):
        return ""
    allow = (ra.get("allow") or {}).get(provider_name) or (ra.get("allow") or {}).get("*") or []
    allow = set(str(a) for a in allow)
    if not allow:
        return ""
    timeout = int(ra.get("timeout_seconds") or 60)
    max_n = int(ra.get("max_commands") or 5)
    cmds = [c for c in extract_commands(text) if first_word(c) in allow][:max_n]
    if not cmds:
        return ""
    parts = []
    for cmd in cmds:
        started = time.monotonic()
        try:
            p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
            out = (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")
            parts.append("$ %s\nexit %s (%.1fs)\n%s" % (cmd, p.returncode, time.monotonic() - started, out[:8000]))
            log("run_actions ok: %s -> %s" % (first_word(cmd), p.returncode))
        except subprocess.TimeoutExpired:
            parts.append("$ %s\nTIMEOUT after %ss" % (cmd, timeout))
            log("run_actions timeout: %s" % cmd[:80])
        except Exception as e:  # noqa: BLE001
            parts.append("$ %s\nERROR: %s: %s" % (cmd, type(e).__name__, e))
            log("run_actions error: %s" % e)
    return "\n\n".join(parts)
