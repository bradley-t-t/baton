"""State kept between hook runs: the log, each session's last request, handoffs still
waiting on a result, and replies already handed off."""

from __future__ import annotations

import hashlib
import json
import os
import time

from .config import state_dir
from .detect import normalize


def _path(name: str):
    return state_dir() / name


def log(msg: str):
    try:
        d = state_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "handoff.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg.replace("\n", " ")[:2000] + "\n")
    except OSError:
        pass


def load(name: str, default):
    try:
        return json.loads(_path(name).read_text())
    except (OSError, ValueError):
        return default


def save(name: str, data):
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, d / name)


def remember_request(session: str, prompt: str):
    data = load("requests.json", {})
    data[session or "-"] = {"prompt": prompt, "at": time.time()}
    if len(data) > 200:
        for k in sorted(data, key=lambda k: data[k].get("at", 0))[:len(data) - 200]:
            data.pop(k, None)
    save("requests.json", data)


def last_request(session: str) -> str:
    return (load("requests.json", {}).get(session or "-") or {}).get("prompt", "")


def _key(session: str, reply: str) -> str:
    return hashlib.sha1(((session or "") + "\0" + normalize(reply)).encode()).hexdigest()


def seen(session: str, reply: str) -> bool:
    return _key(session, reply) in load("seen.json", [])


def mark_seen(session: str, reply: str):
    keys = load("seen.json", [])
    k = _key(session, reply)
    if k not in keys:
        save("seen.json", (keys + [k])[-500:])


def add_pending(hid: str, record: dict):
    p = load("pending.json", {})
    p[hid] = record
    save("pending.json", p)


def pending() -> dict:
    return load("pending.json", {})


def drop_pending(hid: str):
    p = load("pending.json", {})
    if p.pop(hid, None) is not None:
        save("pending.json", p)


MAX_RELAY_RETRIES = 5


def set_expectation(session: str, expected: str, max_retries: int = MAX_RELAY_RETRIES):
    """Remember the exact text Claude must relay for this session after a handoff."""
    data = load("expectations.json", {})
    data[session or "-"] = {
        "expected": expected,
        "retries": 0,
        "max_retries": int(max_retries),
        "at": time.time(),
    }
    if len(data) > 200:
        for k in sorted(data, key=lambda k: data[k].get("at", 0))[: len(data) - 200]:
            data.pop(k, None)
    save("expectations.json", data)


def get_expectation(session: str):
    return load("expectations.json", {}).get(session or "-")


def clear_expectation(session: str):
    data = load("expectations.json", {})
    if data.pop(session or "-", None) is not None:
        save("expectations.json", data)


def bump_expectation_retry(session: str) -> dict:
    """Increment retries; return the updated record (or None if gone)."""
    data = load("expectations.json", {})
    rec = data.get(session or "-")
    if not rec:
        return None
    rec = dict(rec)
    rec["retries"] = int(rec.get("retries") or 0) + 1
    data[session or "-"] = rec
    save("expectations.json", data)
    return rec


def _draft_key(session: str, draft: str) -> str:
    return hashlib.sha1(((session or "") + "\0" + (draft or "").rstrip()).encode()).hexdigest()


def mark_cleared(session: str, draft: str):
    """A draft the check already cleared — Stop must not hand it off again."""
    data = load("cleared.json", {})
    keys = data.get(session or "-") or []
    k = _draft_key(session, draft)
    if k not in keys:
        data[session or "-"] = (keys + [k])[-200:]
        # prune old sessions
        if len(data) > 200:
            # keep insertion order roughly by trimming arbitrary extras
            for old in list(data)[: len(data) - 200]:
                data.pop(old, None)
        save("cleared.json", data)


def is_cleared(session: str, draft: str) -> bool:
    keys = (load("cleared.json", {}) or {}).get(session or "-") or []
    return _draft_key(session, draft) in keys



MAX_CHECK_GATE_RETRIES = 3


def get_check_gate(session: str) -> dict:
    return load("check_gate.json", {}).get(session or "-") or {"retries": 0}


def bump_check_gate(session: str) -> dict:
    data = load("check_gate.json", {})
    rec = dict(data.get(session or "-") or {})
    rec["retries"] = int(rec.get("retries") or 0) + 1
    rec["at"] = time.time()
    data[session or "-"] = rec
    if len(data) > 200:
        for k in sorted(data, key=lambda k: data[k].get("at", 0))[: len(data) - 200]:
            data.pop(k, None)
    save("check_gate.json", data)
    return rec


def clear_check_gate(session: str):
    data = load("check_gate.json", {})
    if data.pop(session or "-", None) is not None:
        save("check_gate.json", data)


def _fp(text: str) -> str:
    return hashlib.sha1((text or "").rstrip().encode()).hexdigest()


def said_ledger(session: str, turn: str) -> dict:
    data = load("said.json", {})
    key = "%s|%s" % (session or "-", turn or "-")
    rec = data.get(key) or {}
    return rec if isinstance(rec, dict) else {}


def said_once(session: str, turn: str, kind: str, trigger: str) -> bool:
    """True the first time this kind+trigger is said this turn; False if repeat."""
    data = load("said.json", {})
    key = "%s|%s" % (session or "-", turn or "-")
    rec = dict(data.get(key) or {})
    marks = list(rec.get(kind) or [])
    fp = _fp(trigger)
    if fp in marks:
        return False
    marks.append(fp)
    rec[kind] = marks[-50:]
    rec["at"] = time.time()
    data[key] = rec
    if len(data) > 300:
        for old in sorted(data, key=lambda k: (data[k] or {}).get("at", 0))[: len(data) - 300]:
            data.pop(old, None)
    save("said.json", data)
    return True


def clear_said(session: str):
    data = load("said.json", {})
    prefix = "%s|" % (session or "-")
    changed = False
    for k in list(data):
        if k.startswith(prefix):
            data.pop(k, None)
            changed = True
    if changed:
        save("said.json", data)


def refusal_chance(session: str) -> int:
    return int((load("refusal_chance.json", {}).get(session or "-") or {}).get("n") or 0)


def bump_refusal_chance(session: str) -> int:
    data = load("refusal_chance.json", {})
    n = int((data.get(session or "-") or {}).get("n") or 0) + 1
    data[session or "-"] = {"n": n, "at": time.time()}
    if len(data) > 200:
        for old in sorted(data, key=lambda k: data[k].get("at", 0))[: len(data) - 200]:
            data.pop(old, None)
    save("refusal_chance.json", data)
    return n


def clear_refusal_chance(session: str):
    data = load("refusal_chance.json", {})
    if data.pop(session or "-", None) is not None:
        save("refusal_chance.json", data)


def display_buffer_get(session: str, message_id: str) -> str:
    data = load("display_buf.json", {})
    return (data.get("%s|%s" % (session or "-", message_id or "")) or {}).get("text") or ""


def display_buffer_set(session: str, message_id: str, text: str, final: bool) -> str:
    data = load("display_buf.json", {})
    key = "%s|%s" % (session or "-", message_id or "")
    prev = (data.get(key) or {}).get("text") or ""
    joined = prev + (text or "")
    if final:
        data.pop(key, None)
    else:
        data[key] = {"text": joined, "at": time.time()}
    # prune
    if len(data) > 100:
        for old in sorted(data, key=lambda k: data[k].get("at", 0))[: len(data) - 100]:
            data.pop(old, None)
    save("display_buf.json", data)
    return joined
