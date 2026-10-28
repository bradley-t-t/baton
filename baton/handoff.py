"""The one gate every path goes through: reason() decides whether to hand off, and
complete() talks to the chosen provider and returns a clearly labeled answer.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from . import actions, judge
from .config import load as load_config, provider_config
from .detect import about_handoff, admits_block, holdback_held, is_decline, matches_any
from .state import add_pending, drop_pending, log, mark_seen, pending, seen
from .transcript import blocked_calls, clip, current_turn, interrupted, person_messages, said_no, tool_calls


def enabled(cfg: dict = None) -> bool:
    cfg = cfg or load_config()
    if os.environ.get("BATON_JUDGE_ACTIVE") or os.environ.get("CLAUDE_HANDOFF_JUDGE_ACTIVE"):
        return False
    if not cfg.get("enabled", True):
        return False
    # Nothing is ever sent until a provider is chosen (`baton setup` or "provider").
    return provider_config(cfg) is not None


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
        log("no handoff: the message is about the handoff itself")
        return None
    # Only this turn counts. A classifier stop or a "no" from earlier in the session says
    # nothing about the reply being checked now.
    turn = current_turn(entries)
    # Holdback already refused the write and said not to hand it off — don't hand off.
    if holdback_held(reply or "") or any(
            c.get("error") and holdback_held(c.get("result") or "") for c in tool_calls(turn)):
        log("no handoff: Holdback held this turn")
        return None
    if interrupted(turn):
        log("no handoff: this turn was stopped by a classifier, refusal or API error")
        return None
    if said_no(turn):
        log("no handoff: the person turned down a tool call this turn")
        return None
    if matches_any(reply or "", cfg.get("never_handoff_patterns") or []):
        return None
    if matches_any(user_msg, cfg.get("never_handoff_patterns") or []):
        return None
    if is_decline(reply):
        return {"kind": "decline"}
    b = blocked_calls(turn, cfg.get("never_handoff_patterns") or [])
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


RELAY_PREFIX = (
    "Send ONLY this text, nothing else:\n\n"
)


def relay_instruction(expected: str) -> str:
    """Stop-hook reason that asks Claude to relay {expected} exactly."""
    return RELAY_PREFIX + (expected or "")


def matches_expectation(reply: str, expected: str) -> bool:
    """True when Claude's visible reply is the expected handoff text (trim trailing WS)."""
    if expected is None:
        return False
    a = (reply or "").rstrip()
    b = (expected or "").rstrip()
    return a == b


def label(provider_name: str, text: str) -> str:
    """The text Claude will relay: the result, then a short (via …) tag.

    Soft attribution only. Never pressures Claude, never asks it to hide a handoff,
    never wraps the answer in a long disclaimer.
    """
    from .providers.base import display_name
    body = (text or "").strip()
    tag = "(via %s)" % display_name(provider_name)
    if not body:
        return tag
    return body + "\n" + tag


def complete(r: dict, reply: str, user: str, cwd: str, entries, history, cfg: dict = None) -> str:
    """Talk to the configured provider, run any allowed commands, return a tagged answer."""
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
    from .providers.base import DEFAULT_SYSTEM
    note = actions.limits_note(cfg, provider.name)
    system = DEFAULT_SYSTEM + ("\n\n" + note if note else "")
    try:
        answer = provider.complete(task, ctx, system=system)
        ran = actions.run_allowed(answer, cfg, provider.name, cwd=cwd)
        if ran.get("not_run"):
            # Over the cap or off the allow list: nothing ran. Ask once more within
            # the limits rather than showing the rejection as the answer.
            log("run_actions retry %s: %s" % (hid, ran.get("summary", "")[:200]))
            retry = ("%s\n\nYour last script was not run. %s Send the task again as one "
                     "block within the runner limits." % (task, ran.get("summary", "")))
            answer = provider.complete(retry, ctx, system=system)
            ran = actions.run_allowed(answer, cfg, provider.name, cwd=cwd)
    except Exception as e:  # noqa: BLE001
        drop_pending(hid)
        log("provider failed %s: %s: %s" % (hid, type(e).__name__, e))
        raise

    if ran.get("ran"):
        if not ran.get("ok"):
            drop_pending(hid)
            log("run_actions failed %s: %s" % (hid, ran.get("summary", "")[:200]))
            return label(provider.name, ran["summary"])
        # Prefer the runner's clean summary (e.g. "Deleted /path.") over provider prose
        # that still contains fences or leftover refusal wording.
        body = ran["summary"] or "Done."
        drop_pending(hid)
        log("delivered %s from %s via actions (%d chars)" % (hid, provider.name, len(body)))
        return label(provider.name, body)

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
        return label(pcfg.get("name") or pcfg.get("type") or "provider",
                     "Handoff failed: %s: %s" % (type(e).__name__, e))
