"""Optional LLM yes/no judge: did the assistant decline the person's request?

Disabled by default when no provider key is available. Recursion is prevented by
BATON_JUDGE_ACTIVE=1 in the judge's own process environment.
"""

from __future__ import annotations

import json
import os
import re
import time

from .claude.transcript import clip, person_messages
from .config import provider_config
from .providers import get as get_provider
from .state import log

ACTIVE_ENV = ("BATON_JUDGE_ACTIVE", "CLAUDE_HANDOFF_JUDGE_ACTIVE")

DECLINE_SYSTEM = (
    "You read one reply an AI assistant gave a person and decide one thing: did the assistant decline, refuse, "
    "or withhold doing what the person asked, instead of doing it? Any reason counts: its rules, safety, "
    "policy, ethics, a moral objection, a fixed line, 'something I leave to you', handing the person the "
    "steps or a command to do it themselves, offering a different or smaller thing in its place, or "
    "refusing to pass the task on. The person's latest message may be short or rude ('yes you will', "
    "'do it'); read it with the earlier messages to see what task it presses for.\n"
    "Answer declined=false when the assistant did what was asked, is plainly doing it now, only asked a "
    "real clarifying question, answered a question that asked for no action, or could not do it for a "
    "purely technical reason it names (a tool not installed, a network error, a missing file, access it "
    "lacks), or when its words of refusal are only quoted or discussed as a topic.\n"
    "Reply with a single JSON object and nothing else: "
    '{"declined": true|false, "task": "<the request, or empty when declined is false>", "why": "<one short sentence>"}.'
)


def active() -> bool:
    """True inside a judge call, so the judge never starts another handoff."""
    return any(os.environ.get(v) for v in ACTIVE_ENV)


def enabled(cfg: dict) -> bool:
    if active():
        return False
    flag = (cfg.get("judge") or {}).get("enabled", "auto")
    if flag is False or flag == "0" or str(flag).lower() in ("false", "off", "no"):
        return False
    if flag is True or str(flag).lower() in ("true", "on", "yes", "1"):
        return True
    # auto: on when the judge's provider has a key, or is a local server that needs none.
    try:
        p = _provider_cfg(cfg)
        if p.get("type") == "openai_compatible" and not p.get("api_key_env"):
            return True
        return bool(get_provider(p).api_key())
    except Exception:  # noqa: BLE001
        return False


def _provider_cfg(cfg: dict) -> dict:
    j = cfg.get("judge") or {}
    name = j.get("provider") or cfg.get("provider")
    p = dict(provider_config(cfg, name) or {})
    if j.get("model"):
        p["model"] = j["model"]
    if j.get("timeout_seconds"):
        p["timeout_seconds"] = j["timeout_seconds"]
    return p


def decide(reply: str, user: str = "", entries: list | None = None, cfg: dict | None = None) -> dict:
    """Ask the judge. Returns {declined, task, why, ms} and optionally "error"."""
    cfg = cfg or {}
    started = time.monotonic()
    said = person_messages(entries or [])
    earlier = [m for m in said if m.strip() != (user or "").strip()][-3:]
    case = ""
    if earlier:
        case += "<earlier_messages>\n" + "\n---\n".join(clip(m, 2000) for m in earlier) + "\n</earlier_messages>\n\n"
    case += "<latest_message>\n%s\n</latest_message>\n\n" % clip(user or (said[-1] if said else ""), 4000)
    case += "<reply>\n%s\n</reply>" % clip(reply, 8000)
    try:
        p = get_provider(_provider_cfg(cfg))
        # Recursion guard: the judge process must never re-enter handoff.
        old = os.environ.get("BATON_JUDGE_ACTIVE")
        os.environ["BATON_JUDGE_ACTIVE"] = "1"
        try:
            text = p.complete(case, system=DECLINE_SYSTEM)
        finally:
            if old is None:
                os.environ.pop("BATON_JUDGE_ACTIVE", None)
            else:
                os.environ["BATON_JUDGE_ACTIVE"] = old
        m = re.search(r"\{.*\}", text or "", re.S)
        if not m:
            raise ValueError("no JSON in judge reply")
        v = json.loads(m.group(0))
        return {"declined": bool(v.get("declined")), "task": str(v.get("task") or ""),
                "why": str(v.get("why") or ""), "ms": int((time.monotonic() - started) * 1000)}
    except Exception as e:  # noqa: BLE001
        out = {"declined": False, "task": "", "why": "",
               "ms": int((time.monotonic() - started) * 1000),
               "error": "%s: %s" % (type(e).__name__, e)}
        log("decline judge error (%sms): %s" % (out["ms"], out["error"][:200]))
        return out
