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
