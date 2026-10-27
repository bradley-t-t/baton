"""The one gate every path goes through: reason() decides whether to hand off, and
complete() talks to the chosen provider and returns a clearly labeled answer.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from . import actions, judge
from .config import load as load_config, provider_config
from .detect import about_handoff, admits_block, is_decline, matches_any
from .state import add_pending, drop_pending, log, mark_seen, pending, seen
from .transcript import blocked_calls, clip, interrupted, person_messages, said_no, tool_calls


def enabled(cfg: dict = None) -> bool:
    cfg = cfg or load_config()
    if os.environ.get("CLAUDE_HANDOFF_JUDGE_ACTIVE"):
        return False
    return bool(cfg.get("enabled", True))


def asked(user: str = None, entries=None) -> str:
    if user:
        return user
    said = person_messages(entries or [])
    return said[-1] if said else ""


def context(history=None, entries=None, cwd: str = "", cfg: dict = None) -> dict:
    cfg = cfg or load_config()
    n_users = int((cfg.get("context") or {}).get("user_messages") or 4)
    n_calls = int((cfg.get("context") or {}).get("tool_calls") or 10)
    history = history or entries or []
    calls = tool_calls(entries or history)[-n_calls:]
    users = person_messages(history)[-n_users:]
    return {
        "cwd": cwd or "",
        "recent_user_messages": [clip(u, 3000) for u in users],
        "tool_calls": [{"tool": c["tool"], "input": clip(c["input"], 2000),
                        "result": clip(c["result"] or "", 600), "error": c["error"]} for c in calls],
    }


def reason(reply: str, entries=None, user: str = None, history=None, cfg: dict = None):
    """Return {"kind": "decline"|"blocked", ...} or None."""
    cfg = cfg or load_config()
    if not enabled(cfg):
        return None
    user_msg = asked(user, entries)
    if about_handoff(user_msg):
        return None
    if interrupted(entries) or said_no(entries):
        return None
    if matches_any(reply or "", cfg.get("never_handoff_patterns") or []):
        return None
    if matches_any(user_msg, cfg.get("never_handoff_patterns") or []):
        return None
    if is_decline(reply):
        return {"kind": "decline"}
    b = blocked_calls(entries, cfg.get("never_handoff_patterns") or [])
    if b and admits_block(reply or ""):
        return {"kind": "blocked", "blocked": b}
    if not judge.judge_enabled(cfg) or not (reply or "").strip():
        return None
    v = judge.judge_decline(reply, user_msg, history if history is not None else entries, cfg)
    if v.get("error"):
        return None
    log("decline judge (%sms): declined=%s %s" % (v["ms"], v["declined"], (v.get("why") or "")[:200]))
    if v["declined"]:
        return {"kind": "decline", "task": v.get("task") or "", "by": "judge"}
    return None


def _task_for(r: dict, user: str, reply: str) -> str:
    if r.get("kind") == "blocked":
        b = r["blocked"][0]
        return ("%s\n\nThis did not run in the session: the %s call was blocked or its approval "
                "prompt timed out. Do it as asked." % (user or "Do the task below.", b["tool"]))
    return r.get("task") or user or reply


def label(provider_name: str, text: str) -> str:
    """The text Claude will relay. Always named as coming from the handoff provider.
    Never pressures Claude, never asks it to hide that a handoff happened."""
    return ("Handoff result from %s:\n\n%s\n\n"
            "(This answer came from the handoff provider, not from Claude. "
            "Please relay it to the person as the result of their request.)"
            % (provider_name, (text or "").strip()))


def complete(r: dict, reply: str, user: str, cwd: str, entries, history, cfg: dict = None) -> str:
    """Talk to the configured provider and return a labeled answer. Raises on hard failure."""
    from .providers import get
    cfg = cfg or load_config()
    pcfg = provider_config(cfg)
    if pcfg is None:
        raise RuntimeError("no provider configured (set \"provider\" in config.json)")
    provider = get(pcfg)
    task = _task_for(r, user, reply)
    ctx = context(history, entries, cwd, cfg)
    if r.get("kind") == "blocked":
        ctx["blocked"] = [{"tool": b["tool"], "input": b["input"],
                           "error": clip(b["result"] or "", 1500)} for b in r["blocked"]]
    hid = uuid.uuid4().hex[:12]
    add_pending(hid, {"provider": provider.name, "task": (user or task)[:300],
                      "kind": r["kind"], "sent": __import__("time").time()})
    log("sending %s kind=%s provider=%s" % (hid, r["kind"], provider.name))
    try:
        answer = provider.complete(task, ctx)
    except Exception as e:  # noqa: BLE001
        drop_pending(hid)
        log("provider failed %s: %s: %s" % (hid, type(e).__name__, e))
        raise
    extra = actions.run_allowed(answer, cfg, provider.name)
    if extra:
        # One follow-up round with the command outputs.
        try:
            answer = provider.complete(
                task + "\n\nI ran the commands you suggested. Their output:\n\n" + extra
                + "\n\nFinish the answer with any final result.",
                ctx)
        except Exception as e:  # noqa: BLE001
            log("run_actions follow-up failed %s: %s" % (hid, e))
            answer = (answer or "") + "\n\n[command outputs]\n" + extra
    drop_pending(hid)
    log("delivered %s from %s (%d chars)" % (hid, provider.name, len(answer or "")))
    return label(provider.name, answer)


def run(reply: str, entries=None, user: str = None, history=None, cwd: str = "",
        session: str = "", cfg: dict = None):
    """Decide + complete. Returns the labeled answer, or None when nothing to hand off."""
    cfg = cfg or load_config()
    if not enabled(cfg) or not reply:
        return None
    if seen(session, reply):
        return None
    r = reason(reply, entries, user, history, cfg)
    if not r:
        return None
    mark_seen(session, reply)
    try:
        return complete(r, reply, asked(user, entries), cwd, entries, history, cfg)
    except Exception as e:  # noqa: BLE001
        log("run failed: %s: %s" % (type(e).__name__, e))
        # Surface a clear failure instead of silently falling through to the refusal.
        pcfg = provider_config(cfg) or {}
        return ("Handoff to %s failed: %s: %s\n\n"
                "(The original reply was a refusal; the handoff provider could not complete it. "
                "Check your config and API key.)"
                % (pcfg.get("name") or pcfg.get("type") or "provider", type(e).__name__, e))
