"""State kept between hook runs, in ~/.local/state/baton: the log, each session's last
request, the result already shown for it, and display text still streaming in."""

from __future__ import annotations

import json
import os
import time

from .config import state_dir

LOG_FILE = "handoff.log"
REQUESTS = "requests.json"
DISPLAYED = "displayed.json"
DISPLAY_BUFFER = "display_buf.json"
MAX_SESSIONS = 200
MAX_BUFFERS = 100


def log(msg: str) -> None:
    try:
        d = state_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg.replace("\n", " ")[:2000] + "\n")
    except OSError:
        pass


def _load(name: str) -> dict:
    try:
        data = json.loads((state_dir() / name).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(name: str, data: dict) -> None:
    d = state_dir()
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, d / name)


def _get(name: str, key: str) -> dict | None:
    return _load(name).get(key)


def _put(name: str, key: str, record: dict, limit: int = MAX_SESSIONS) -> None:
    """Store record under key with a timestamp, dropping the oldest past limit."""
    data = _load(name)
    data[key] = dict(record, at=time.time())
    for old in sorted(data, key=lambda k: (data[k] or {}).get("at", 0))[:max(0, len(data) - limit)]:
        data.pop(old, None)
    _save(name, data)


def _drop(name: str, key: str) -> None:
    data = _load(name)
    if data.pop(key, None) is not None:
        _save(name, data)


def _session(session: str) -> str:
    return session or "-"


def remember_request(session: str, prompt: str) -> None:
    _put(REQUESTS, _session(session), {"prompt": prompt})


def last_request(session: str) -> str:
    return (_get(REQUESTS, _session(session)) or {}).get("prompt", "")


def get_displayed(session: str) -> dict | None:
    """The result already shown on screen for this session's current request."""
    return _get(DISPLAYED, _session(session))


def mark_displayed(session: str, result: str) -> None:
    _put(DISPLAYED, _session(session), {"result": result})


def clear_displayed(session: str) -> None:
    _drop(DISPLAYED, _session(session))


def buffer_display(session: str, message_id: str, text: str, final: bool) -> str:
    """Add a streamed chunk to the message's text and return the text so far. The
    final chunk returns the whole message and forgets it."""
    key = "%s|%s" % (_session(session), message_id or "")
    joined = ((_get(DISPLAY_BUFFER, key) or {}).get("text") or "") + (text or "")
    if final:
        _drop(DISPLAY_BUFFER, key)
    else:
        _put(DISPLAY_BUFFER, key, {"text": joined}, limit=MAX_BUFFERS)
    return joined
