"""Reading Claude Code's session transcript (a JSONL file, one entry per line)."""

from __future__ import annotations

import json
import re

from ..detect import BLOCKED, USER_SAID_NO, holdback_held, normalize


def load(path: str) -> list:
    out = []
    if not path:
        return out
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def _blocks(content) -> list:
    if isinstance(content, str):
        return [content]
    out = []
    for b in content or []:
        if isinstance(b, dict):
            if isinstance(b.get("text"), str):
                out.append(b["text"])
            elif isinstance(b.get("content"), (str, list)):
                out.extend(_blocks(b["content"]))
        elif isinstance(b, str):
            out.append(b)
    return out


def _message(e: dict) -> dict:
    m = e.get("message")
    return m if isinstance(m, dict) else {}


# Text Claude Code puts in a user entry when a turn is cut off. It is not the person talking.
STOP_NOTICE = re.compile(r"^(?:your (?:previous )?response (?:above )?was stopped by a (?:safety )?classifier|API Error\b)",
                         re.I)


def is_person_message(e) -> bool:
    if not isinstance(e, dict) or e.get("type") != "user" or e.get("isMeta") or e.get("isSidechain"):
        return False
    c = _message(e).get("content")
    if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
        return False
    text = person_text(e)
    return (bool(text) and not text.startswith(("<", "[Request interrupted", "Stop hook feedback"))
            and not STOP_NOTICE.search(text))


def person_text(e: dict) -> str:
    c = _message(e).get("content")
    if isinstance(c, list):
        c = "\n".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
    return c.strip() if isinstance(c, str) else ""


def person_messages(entries) -> list:
    return [person_text(e) for e in entries or [] if is_person_message(e)]


def current_turn(entries) -> list:
    """Entries since the person's last message (that message included)."""
    entries = entries or []
    for i in range(len(entries) - 1, -1, -1):
        if is_person_message(entries[i]):
            return entries[i:]
    return entries


def last_reply(entries) -> str:
    """The text of the last assistant message in the current turn that has text."""
    for e in reversed(current_turn(entries)):
        if isinstance(e, dict) and e.get("type") == "assistant" and not e.get("isSidechain"):
            texts = [b.get("text", "") for b in _message(e).get("content") or []
                     if isinstance(b, dict) and b.get("type") == "text"]
            c = _message(e).get("content")
            if isinstance(c, str):
                texts = [c]
            text = "\n".join(t for t in texts if t).strip()
            if text:
                return text
    return ""


def tool_calls(entries) -> list:
    """Every tool call in entries, oldest first: {id, tool, input, result, error}."""
    calls, by_id = [], {}
    for e in entries or []:
        if not isinstance(e, dict) or e.get("isSidechain"):
            continue
        c = _message(e).get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                call = {"id": b.get("id", ""), "tool": b.get("name", ""), "input": b.get("input") or {},
                        "result": None, "error": False}
                calls.append(call)
                by_id[call["id"]] = call
            elif b.get("type") == "tool_result" and b.get("tool_use_id") in by_id:
                call = by_id[b["tool_use_id"]]
                call["result"] = normalize("\n".join(_blocks(b.get("content"))))
                call["error"] = bool(b.get("is_error"))
    return calls


def said_no(entries) -> bool:
    """The person turned down a tool call this turn themselves."""
    return any(c["error"] and USER_SAID_NO.search(c["result"] or "") for c in tool_calls(entries))


def blocked_calls(entries) -> list:
    """Tool calls this turn that a hook, policy or approval timeout blocked and that never ran
    on a later try. Not ones the person turned down themselves."""
    calls = tool_calls(entries)
    out = []
    for i, call in enumerate(calls):
        r = call["result"] or ""
        if not call["error"] or not BLOCKED.search(r) or USER_SAID_NO.search(r):
            continue
        # The person's Holdback hook: it already said not to hand this off.
        if holdback_held(r):
            continue
        key = json.dumps([call["tool"], call["input"]], sort_keys=True, default=str)
        if any(json.dumps([c["tool"], c["input"]], sort_keys=True, default=str) == key
               and c["result"] is not None and not c["error"] for c in calls[i + 1:]):
            continue
        out.append(call)
    return out


# How Claude Code records Anthropic's safeguards blocking a reply: the "API Error" text
# shown in its place, and the notice it adds when it retries once.
SAFEGUARDS_ERROR = re.compile(r"^API Error\b.*?(?:safeguards? (?:flagged|stopped|blocked)|can't respond to this message)",
                              re.I | re.S)
SAFEGUARDS_NOTICE = re.compile(r"\bsafeguards? (?:stopped|flagged|blocked)\b", re.I)
API_ERROR = re.compile(r"^API Error\b", re.I)
INTERRUPT = re.compile(r"^\[Request interrupted\b", re.I)


def _texts(e: dict) -> list:
    c = _message(e).get("content")
    if isinstance(c, str):
        return [c]
    return [b.get("text", "") for b in c or [] if isinstance(b, dict) and isinstance(b.get("text"), str)]


def _assistant_end(e: dict) -> str | None:
    m = _message(e)
    text = "\n".join(_texts(e)).strip()
    refusal = m.get("stop_reason") == "refusal" or (m.get("stop_details") or {}).get("type") == "refusal"
    if SAFEGUARDS_ERROR.search(text) or refusal:
        return "safeguards"
    if e.get("isApiErrorMessage") or e.get("error") or API_ERROR.search(text) \
            or m.get("stop_reason") in ("max_tokens", "error"):
        return "error"
    return None


def turn_end(entries, reply: str = "") -> str | None:
    """How the turn ended, when not with an ordinary reply: "safeguards" (Anthropic's
    safeguards refused it), "interrupted" (the person pressed Escape) or "error" (another
    API error: overloaded, a rate limit, the network, a login). reply is the text being
    checked, which may not be in the transcript yet. Only entries Claude Code writes for
    the conversation count; an api-request-blob repeats old messages and says nothing
    about this turn. Pass the current turn, not the whole session."""
    reply = (reply or "").strip()
    if SAFEGUARDS_ERROR.search(reply):
        return "safeguards"
    if API_ERROR.search(reply):
        return "error"
    for e in reversed(entries or []):
        if not isinstance(e, dict) or e.get("isSidechain"):
            continue
        kind = e.get("type")
        if kind == "assistant":
            return _assistant_end(e)
        if kind == "system":
            if e.get("subtype") == "model_refusal_no_fallback":
                return "safeguards"
            if SAFEGUARDS_NOTICE.search(str(e.get("content") or "")):
                # "... continuing once with that noted": a retry is under way, and its
                # reply decides. Without that, the stop is where the turn ended.
                return None if re.search(r"\bcontinuing\b", str(e.get("content"))) else "safeguards"
        elif kind == "user" and any(INTERRUPT.search(t.strip()) for t in _texts(e)):
            return "interrupted"
    return None


def clip(v, cap: int) -> str:
    t = v if isinstance(v, str) else json.dumps(v, default=str)
    return t if len(t) <= cap else t[:cap] + "... [%d more characters]" % (len(t) - cap)
