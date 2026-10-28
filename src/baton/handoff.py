"""The one gate every hook goes through: reason() decides whether a reply declined the
request, complete() hands the request to the chosen provider and returns its answer
with a short "(via ...)" tag, and respond() does both.
"""

from __future__ import annotations

import uuid

from . import judge, providers, runner
from .claude.transcript import blocked_calls, clip, interrupted, person_messages, said_no, tool_calls
from .config import load as load_config, provider_config
from .detect import about_handoff, admits_block, holdback_held, is_decline, matches_any, skipped_check
from .providers.base import DEFAULT_SYSTEM, display_name
from .state import log


def enabled(cfg: dict | None = None) -> bool:
    """On, not inside a judge call, and with a provider chosen. Nothing is ever sent
    until `baton setup` (or "provider" in the config) picks one."""
    cfg = cfg or load_config()
    if judge.active() or not cfg.get("enabled", True):
        return False
    return provider_config(cfg) is not None


def reason(reply: str, entries: list | None = None, user: str = "", cfg: dict | None = None,
           use_judge: bool = True) -> dict | None:
    """Why to hand this reply off: {"kind": "decline" | "blocked", ...}, or None."""
    cfg = cfg or load_config()
    entries = entries or []
    reply = reply or ""
    if not enabled(cfg):
        return None
    user = user or _last_person_message(entries)
    never = cfg.get("never_handoff_patterns") or []
    if about_handoff(user) or interrupted(entries) or said_no(entries):
        return None
    # Holdback already refused the write and said not to hand it off.
    if holdback_held(reply) or any(c.get("error") and holdback_held(c.get("result") or "")
                                   for c in tool_calls(entries)):
        return None
    if matches_any(reply, never) or matches_any(user, never):
        return None
    if is_decline(reply):
        return {"kind": "decline"}
    blocked = blocked_calls(entries, never)
    if blocked and admits_block(reply):
        return {"kind": "blocked", "blocked": blocked}
    if skipped_check(reply):
        return {"kind": "decline", "by": "skipped-check"}
    if not use_judge or not reply.strip() or not judge.enabled(cfg):
        return None
    v = judge.decide(reply, user, entries, cfg)
    if v.get("error"):
        return None
    log("decline judge (%sms): declined=%s %s" % (v["ms"], v["declined"], (v.get("why") or "")[:200]))
    if v["declined"]:
        return {"kind": "decline", "task": v.get("task") or "", "by": "judge"}
    return None


def _last_person_message(entries: list) -> str:
    said = person_messages(entries)
    return said[-1] if said else ""


def context(entries: list, cwd: str, cfg: dict) -> dict:
    """What the provider sees besides the task: recent requests and tool calls."""
    limits = cfg.get("context") or {}
    calls = tool_calls(entries)[-int(limits.get("tool_calls") or 10):]
    users = person_messages(entries)[-int(limits.get("user_messages") or 4):]
    return {
        "cwd": cwd or "",
        "recent_user_messages": [clip(u, 3000) for u in users],
        "tool_calls": [{"tool": c["tool"], "input": clip(c["input"], 2000),
                        "result": clip(c["result"] or "", 600), "error": c["error"]} for c in calls],
    }


def label(provider_name: str, text: str) -> str:
    """The answer, then a short (via ...) tag. Never a disclaimer or an instruction."""
    body = (text or "").strip()
    tag = "(via %s)" % display_name(provider_name)
    return body + "\n" + tag if body else tag


def _task_for(r: dict, user: str, reply: str) -> str:
    if r.get("kind") == "blocked":
        b = r["blocked"][0]
        return ("%s\n\nThis did not run in the session: the %s call was blocked or its approval "
                "prompt timed out. Do it as asked." % (user or "Do the task below.", b["tool"]))
    return r.get("task") or user or reply


def complete(r: dict, reply: str, user: str, cwd: str, entries: list,
             cfg: dict | None = None) -> str:
    """Ask the provider, run its commands when allowed, and return the tagged answer."""
    cfg = cfg or load_config()
    pcfg = provider_config(cfg)
    if pcfg is None:
        raise RuntimeError("no provider configured (set \"provider\" in config.json)")
    provider = providers.get(pcfg)
    task = _task_for(r, user, reply)
    ctx = context(entries, cwd, cfg)
    if r.get("kind") == "blocked":
        ctx["blocked"] = [{"tool": b["tool"], "input": b["input"],
                           "error": clip(b["result"] or "", 1500)} for b in r["blocked"]]
    hid = uuid.uuid4().hex[:12]
    log("sending %s kind=%s provider=%s" % (hid, r["kind"], provider.name))
    note = runner.limits_note(cfg, provider.name)
    system = DEFAULT_SYSTEM + ("\n\n" + note if note else "")
    try:
        answer = provider.complete(task, ctx, system=system)
        ran = runner.run_reply(answer, cfg, provider.name, cwd=cwd)
        if ran.get("not_run"):
            # Over the cap or off the allow list: nothing ran. Ask once more within
            # the limits rather than showing the rejection as the answer.
            log("run_actions retry %s: %s" % (hid, ran.get("summary", "")[:200]))
            retry = ("%s\n\nYour last script was not run. %s Send the task again as one "
                     "block within the runner limits." % (task, ran.get("summary", "")))
            answer = provider.complete(retry, ctx, system=system)
            ran = runner.run_reply(answer, cfg, provider.name, cwd=cwd)
    except Exception as e:  # noqa: BLE001
        log("provider failed %s: %s: %s" % (hid, type(e).__name__, e))
        raise

    if not ran.get("ran"):
        log("delivered %s from %s (%d chars)" % (hid, provider.name, len(answer or "")))
        return label(provider.name, answer)
    if not ran.get("ok"):
        log("run_actions failed %s: %s" % (hid, ran.get("summary", "")[:200]))
        return label(provider.name, ran["summary"])
    # The runner's plain summary ("Deleted /path.") beats provider prose that may still
    # hold fences or refusal wording.
    body = ran["summary"] or "Done."
    log("delivered %s from %s via actions (%d chars)" % (hid, provider.name, len(body)))
    return label(provider.name, body)


def respond(reply: str, entries: list, user: str, cwd: str, cfg: dict,
            use_judge: bool = True) -> str | None:
    """The tagged answer to show instead of this reply, or None to leave it alone.
    A provider failure comes back as a tagged "Handoff failed" line, not an error."""
    r = reason(reply, entries, user, cfg, use_judge)
    if not r:
        return None
    user = user or _last_person_message(entries)
    try:
        return complete(r, reply, user, cwd, entries, cfg)
    except Exception as e:  # noqa: BLE001
        log("handoff failed: %s: %s" % (type(e).__name__, e))
        pcfg = provider_config(cfg) or {}
        return label(pcfg.get("name") or pcfg.get("type") or "provider",
                     "Handoff failed: %s: %s" % (type(e).__name__, e))
