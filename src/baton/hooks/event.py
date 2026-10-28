"""Reading a hook's input event."""

from __future__ import annotations

import json
import sys

from ..claude.transcript import person_messages
from ..state import last_request


def read_event() -> dict:
    """The JSON object Claude Code sends on stdin, or {}."""
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def request_of(session: str, entries: list) -> str:
    """What the person asked: the prompt hook's copy, else the transcript's last one."""
    if last_request(session):
        return last_request(session)
    said = person_messages(entries)
    return said[-1] if said else ""
