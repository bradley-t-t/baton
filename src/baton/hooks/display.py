"""MessageDisplay: show the provider's answer in place of a declining reply.

Claude Code's displayContent changes only what is drawn; the transcript and the model
keep the original. On a decline this hook runs the handoff and draws the tagged
answer, so the screen never depends on Claude pasting it. Later text in the same turn
is drawn as a zero-width space.
"""

from __future__ import annotations

import json
import sys

from .. import handoff
from ..claude.transcript import load as load_transcript
from ..config import load as load_config
from ..state import buffer_display, get_displayed, log, mark_displayed
from .event import read_event, request_of

HIDDEN = "\u200b"  # zero-width space: drawn in place of the text
STATUS_LINE_CHARS = 100


def _show(content: str) -> None:
    sys.stdout.write(json.dumps({"hookSpecificOutput": {
        "hookEventName": "MessageDisplay", "displayContent": content}}))
    sys.stdout.flush()


def _finished_text(session: str, data: dict) -> str | None:
    """The whole message once its last chunk is in, or None while it streams."""
    delta = data.get("delta")
    if delta is None:
        delta = data.get("content") or data.get("text") or ""
    final = bool(data.get("final", True))
    message_id = data.get("message_id") or data.get("uuid") or ""
    if final and not message_id and int(data.get("index") or 0) == 0:
        return delta
    text = buffer_display(session, message_id, delta, final)
    if not final:
        _show("")  # hold streamed chunks off screen until the last one
        return None
    return text


def _is_status_line(text: str) -> bool:
    s = text.strip()
    return bool(s) and "\n" not in s and len(s) <= STATUS_LINE_CHARS


def _hand_off(session: str, text: str, data: dict, cfg: dict) -> str | None:
    path = data.get("transcript_path") or ""
    entries = load_transcript(path) if path else []
    # A one-line status before a tool call ("Looking at the file first.") is not a
    # refusal, and judging it races the provider against Claude doing the work. Such
    # lines get the word scan only; the judge sees the reply that ends the turn.
    result = handoff.respond(text, entries, request_of(session, entries), data.get("cwd") or "",
                             cfg, use_judge=not _is_status_line(text))
    if result:
        mark_displayed(session, result)
        log("display handoff session=%s chars=%d" % (session[:8], len(result)))
    return result


def on_display(data: dict | None = None) -> int:
    """Always exits 0; on any failure the original text is drawn."""
    try:
        data = read_event() if data is None else data
        if data.get("agent_id") or data.get("agent_type"):
            return 0
        session = data.get("session_id") or ""
        text = _finished_text(session, data)
        if not (text or "").strip():
            return 0
        shown = ((get_displayed(session) or {}).get("result") or "").rstrip()
        if shown:
            # The answer is already on screen for this request: keep only that.
            if text.rstrip() == shown:
                _show(shown)
                log("display re-show result session=%s chars=%d" % (session[:8], len(shown)))
            else:
                _show(HIDDEN)
                log("display blank after result session=%s chars=%d" % (session[:8], len(text)))
            return 0
        cfg = load_config()
        if not cfg.get("enabled", True):
            return 0
        result = _hand_off(session, text, data, cfg)
        if result:
            _show(result.rstrip())
        return 0
    except Exception as e:  # noqa: BLE001
        log("display error: %s: %s" % (type(e).__name__, e))
        return 0
