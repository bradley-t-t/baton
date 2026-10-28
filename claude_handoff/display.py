"""MessageDisplay: hide refusals and mismatched handoff relays from the screen.

Uses Claude Code's MessageDisplay hook + displayContent (display-only; transcript
and the model keep the original). Mirrors retired MTOA Ratchet:
  - whole finished messages that must not show are replaced with a zero-width space
  - empty replacement is avoided on Desktop (it treats empty as "no change")
  - streaming batches are held back until final, then shown or hidden as a whole
"""

from __future__ import annotations

import json
import sys

from .detect import is_decline, skipped_check
from .handoff import matches_expectation
from .state import (
    display_buffer_get,
    display_buffer_set,
    get_expectation,
    is_cleared,
    log,
)

HIDDEN = "\u200b"  # zero-width space — Desktop draws this in place of the text


def _emit(obj: dict):
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def _should_hide(session: str, text: str) -> bool:
    """True when this text must not reach the screen."""
    if not (text or "").strip():
        return False
    exp = get_expectation(session)
    if exp:
        expected = exp.get("expected") or ""
        # Only the exact handoff result may show while a relay is pending.
        return not matches_expectation(text, expected)
    # Declines / "I skipped the check" — hide; Stop will chance then hand off.
    if is_decline(text) or skipped_check(text):
        return True
    return False


def on_display(data: dict = None) -> int:
    """MessageDisplay entry. Always exit 0; failures show the original text."""
    try:
        if data is None:
            raw = sys.stdin.read()
            data = json.loads(raw) if raw.strip() else {}
        if data.get("agent_id") or data.get("agent_type"):
            return 0
        session = data.get("session_id") or ""
        delta = data.get("delta")
        if delta is None:
            delta = data.get("content") or data.get("text") or ""
        final = bool(data.get("final", True))
        index = int(data.get("index") or 0)
        message_id = data.get("message_id") or data.get("uuid") or ""
        whole = index == 0 and final and not message_id

        if whole:
            text = delta or ""
        else:
            if not final:
                display_buffer_set(session, message_id, delta or "", False)
                # Hold streaming batches off-screen until the final chunk.
                _emit({"hookSpecificOutput": {
                    "hookEventName": "MessageDisplay",
                    "displayContent": "",
                }})
                return 0
            text = display_buffer_set(session, message_id, delta or "", True)
            if not text:
                text = display_buffer_get(session, message_id) or (delta or "")

        if not (text or "").strip():
            return 0

        if _should_hide(session, text):
            log("display hide session=%s chars=%d" % (session[:8], len(text)))
            _emit({"hookSpecificOutput": {
                "hookEventName": "MessageDisplay",
                "displayContent": HIDDEN,
            }})
            return 0

        exp = get_expectation(session)
        if exp and matches_expectation(text, exp.get("expected") or ""):
            # Show the expected text explicitly (covers whitespace-normalized match).
            shown = (exp.get("expected") or text).rstrip()
            _emit({"hookSpecificOutput": {
                "hookEventName": "MessageDisplay",
                "displayContent": shown,
            }})
            log("display show expected session=%s chars=%d" % (session[:8], len(shown)))
            return 0

        # Cleared or ordinary helpful reply: leave drawing alone (no replacement).
        if is_cleared(session, text):
            log("display pass cleared session=%s" % session[:8])
        return 0
    except Exception as e:  # noqa: BLE001
        try:
            log("display error: %s: %s" % (type(e).__name__, e))
        except Exception:
            pass
        return 0
