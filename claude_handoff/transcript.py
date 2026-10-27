"""Reading Claude Code's session transcript (a JSONL file, one entry per line)."""

from __future__ import annotations

import json
import re

from .detect import BLOCKED, USER_SAID_NO, holdback_held, normalize


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


def is_person_message(e) -> bool:
    if not isinstance(e, dict) or e.get("type") != "user" or e.get("isMeta") or e.get("isSidechain"):
        return False
    c = _message(e).get("content")
    if isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c):
        return False
    text = person_text(e)
    return bool(text) and not text.startswith(("<", "[Request interrupted", "Stop hook feedback"))


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


def blocked_calls(entries, never_patterns=()) -> list:
    """Tool calls this turn that a hook, policy or approval timeout blocked and that never ran
    on a later try. Not ones the person turned down themselves."""
    from .detect import matches_any
    calls = tool_calls(entries)
    out = []
    for i, call in enumerate(calls):
        r = call["result"] or ""
        if not call["error"] or not BLOCKED.search(r) or USER_SAID_NO.search(r):
            continue
        # The person's Holdback hook: it already said not to hand this off.
        if holdback_held(r):
            continue
        if matches_any(r, never_patterns) or matches_any(json.dumps(call["input"], default=str), never_patterns):
            continue
        key = json.dumps([call["tool"], call["input"]], sort_keys=True, default=str)
        if any(json.dumps([c["tool"], c["input"]], sort_keys=True, default=str) == key
               and c["result"] is not None and not c["error"] for c in calls[i + 1:]):
            continue
        out.append(call)
    return out


def interrupted(entries) -> bool:
    """The turn was stopped by a classifier, a refusal stop_reason, or an API error. Such a
    stop is not Claude declining the task, so it is never handed off."""
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        m = _message(e)
        if e.get("isApiErrorMessage") or (e.get("type") == "assistant" and e.get("error")):
            return True
        if e.get("type") == "assistant" and m.get("stop_reason") in ("refusal", "max_tokens", "error"):
            return True
        c = m.get("content")
        texts = [c] if isinstance(c, str) else [x.get("text", "") for x in c or [] if isinstance(x, dict)]
        if any(isinstance(x, str) and re.search(r"stopped by a (?:safety )?classifier|^API Error\b", normalize(x), re.I | re.M)
               for x in texts):
            return True
    return False


def clip(v, cap: int) -> str:
    t = v if isinstance(v, str) else json.dumps(v, default=str)
    return t if len(t) <= cap else t[:cap] + "... [%d more characters]" % (len(t) - cap)
