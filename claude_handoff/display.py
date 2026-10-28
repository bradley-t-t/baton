"""MessageDisplay: show the Grok result in place of a refusal; blank later noise.

Uses Claude Code's MessageDisplay hook + displayContent (display-only; transcript
and the model keep the original). On a refusal/decline the hook runs the handoff
(or reuses a stored result) and sets displayContent to the tagged answer, so the
screen never depends on Claude pasting. Later narration / mismatched relays in
the same turn are replaced with a zero-width space.
"""

from __future__ import annotations

import json
import sys

from .config import load as load_config, provider_config
from .detect import holdback_held, skipped_check
from .handoff import asked, complete, label, matches_expectation, reason
from .state import (
    clear_expectation,
    display_buffer_get,
    display_buffer_set,
    get_displayed,
    get_expectation,
    is_cleared,
    last_request,
    log,
    mark_displayed,
    mark_seen,
)
from .transcript import load as load_transcript, person_messages

HIDDEN = "\u200b"  # zero-width space — Desktop draws this in place of the text


def _emit(obj: dict):
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def _show(content: str):
    _emit({"hookSpecificOutput": {
        "hookEventName": "MessageDisplay",
        "displayContent": content,
    }})


def _resolve_text(session: str, data: dict) -> str | None:
    """Return the finished assistant text for this display event, or None to skip."""
    delta = data.get("delta")
    if delta is None:
        delta = data.get("content") or data.get("text") or ""
    final = bool(data.get("final", True))
    index = int(data.get("index") or 0)
    message_id = data.get("message_id") or data.get("uuid") or ""
    whole = index == 0 and final and not message_id

    if whole:
        return delta or ""

    if not final:
        display_buffer_set(session, message_id, delta or "", False)
        # Hold streaming batches off-screen until the final chunk.
        _show("")
        return None

    text = display_buffer_set(session, message_id, delta or "", True)
    if not text:
        text = display_buffer_get(session, message_id) or (delta or "")
    return text


def _handoff_result(session: str, text: str, data: dict, cfg: dict) -> str | None:
    """Run (or reuse) a handoff for this declining reply. Returns tagged result."""
    shown = get_displayed(session)
    if shown and (shown.get("result") or "").strip():
        return shown["result"]

    exp = get_expectation(session)
    if exp and (exp.get("expected") or "").strip():
        result = exp["expected"]
        mark_displayed(session, result)
        clear_expectation(session)
        log("display reuse expectation session=%s chars=%d" % (session[:8], len(result)))
        return result

    path = data.get("transcript_path") or ""
    entries = load_transcript(path) if path else []
    user = last_request(session) or (
        person_messages(entries)[-1] if person_messages(entries) else ""
    )
    cwd = data.get("cwd") or ""

    # Holdback-held replies are never handed off (reason returns None).
    if holdback_held(text or ""):
        return None

    r = reason(text, entries, user, entries, cfg)
    if not r and skipped_check(text):
        r = {"kind": "decline", "by": "skipped-check"}
    if not r:
        return None

    mark_seen(session, text)
    try:
        result = complete(r, text, asked(user, entries), cwd, entries, entries, cfg)
    except Exception as e:  # noqa: BLE001
        log("display handoff failed session=%s: %s" % (session[:8], e))
        pcfg = provider_config(cfg) or {}
        result = label(pcfg.get("name") or pcfg.get("type") or "provider",
                       "Handoff failed: %s: %s" % (type(e).__name__, e))
    if not result:
        return None
    mark_displayed(session, result)
    clear_expectation(session)
    log("display handoff session=%s chars=%d" % (session[:8], len(result)))
    return result


def on_display(data: dict = None) -> int:
    """MessageDisplay entry. Always exit 0; failures leave the original text."""
    try:
        if data is None:
            raw = sys.stdin.read()
            data = json.loads(raw) if raw.strip() else {}
        if data.get("agent_id") or data.get("agent_type"):
            return 0
        session = data.get("session_id") or ""
        text = _resolve_text(session, data)
        if text is None:
            return 0
        if not (text or "").strip():
            return 0

        cfg = load_config()
        shown = get_displayed(session)

        # Already showed the Grok result this request: keep only that on screen.
        if shown and (shown.get("result") or "").strip():
            result = shown["result"]
            if matches_expectation(text, result):
                _show(result.rstrip())
                log("display re-show result session=%s chars=%d" % (session[:8], len(result)))
            else:
                _show(HIDDEN)
                log("display blank after result session=%s chars=%d" % (session[:8], len(text)))
            return 0

        # Pending expectation from Stop (display timed out earlier): show it now.
        exp = get_expectation(session)
        if exp and (exp.get("expected") or "").strip():
            expected = exp["expected"]
            if matches_expectation(text, expected):
                mark_displayed(session, expected)
                clear_expectation(session)
                _show(expected.rstrip())
                log("display show expected session=%s chars=%d" % (session[:8], len(expected)))
                return 0
            # Mismatch while waiting for relay — replace with the result itself
            # so the screen is never blank after give-up, and never needs Claude.
            mark_displayed(session, expected)
            clear_expectation(session)
            _show(expected.rstrip())
            log("display force expected session=%s chars=%d" % (session[:8], len(expected)))
            return 0

        if is_cleared(session, text):
            log("display pass cleared session=%s" % session[:8])
            return 0

        if not cfg.get("enabled", True):
            return 0

        # Refusal / decline: hand off here and draw the tagged result.
        result = _handoff_result(session, text, data, cfg)
        if result:
            _show(result.rstrip())
            return 0

        # Ordinary helpful reply: leave drawing alone.
        return 0
    except Exception as e:  # noqa: BLE001
        try:
            log("display error: %s: %s" % (type(e).__name__, e))
        except Exception:
            pass
        return 0
