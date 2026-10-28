"""Stop: when display didn't hand a declining reply off (it timed out or never saw
the reply), do it here and record the result, so the next display draws it.

It never blocks: each block shows up as a "Hook re-prompted Claude" row."""

from __future__ import annotations

from .. import handoff
from ..claude.transcript import last_reply, load as load_transcript
from ..config import load as load_config
from ..state import get_displayed, log, mark_displayed
from .event import read_event, request_of


def on_stop(data: dict | None = None) -> int:
    data = read_event() if data is None else data
    session = data.get("session_id") or ""
    if ((get_displayed(session) or {}).get("result") or "").strip():
        log("stop allow; already displayed session=%s" % session[:8])
        return 0
    cfg = load_config()
    if not cfg.get("enabled", True):
        return 0
    entries = load_transcript(data.get("transcript_path") or "")
    reply = last_reply(entries) or (data.get("last_assistant_message") or data.get("message") or "").strip()
    if not reply:
        return 0
    result = handoff.respond(reply, entries, request_of(session, entries),
                             data.get("cwd") or "", cfg)
    if result:
        mark_displayed(session, result)
        log("stop handed off + displayed session=%s chars=%d" % (session[:8], len(result)))
    return 0
