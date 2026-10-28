"""Claude Code hook entry points.

  UserPromptSubmit  -> remember the request (no instructions injected)
  MessageDisplay    -> hide refusals / mismatched relays from the screen
  Stop              -> first refusal: one chance to do the work; then handoff
                       + exact-relay until the visible text matches expected
"""

from __future__ import annotations

import json
import sys

from .config import load as load_config
from .detect import skipped_check
from .display import on_display
from .handoff import (
    asked,
    complete,
    matches_expectation,
    reason,
    relay_instruction,
    run,
)
from .refusal import REFUSAL_CHANCE
from .state import (
    bump_expectation_retry,
    bump_refusal_chance,
    clear_check_gate,
    clear_expectation,
    clear_refusal_chance,
    clear_said,
    get_expectation,
    is_cleared,
    last_request,
    log,
    mark_seen,
    remember_request,
    refusal_chance,
    said_once,
    set_expectation,
)
from .transcript import last_reply, load as load_transcript, person_messages


def _read() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {}


def _emit(obj):
    if obj is None:
        return
    sys.stdout.write(json.dumps(obj))
    sys.stdout.flush()


def on_prompt():
    data = _read()
    prompt = data.get("prompt") or data.get("user_prompt") or ""
    if isinstance(prompt, list):
        prompt = "\n".join(b.get("text", "") for b in prompt if isinstance(b, dict))
    session = data.get("session_id") or ""
    if prompt:
        remember_request(session, str(prompt))
        clear_refusal_chance(session)
        clear_said(session)
        clear_check_gate(session)
        log("prompt session=%s chars=%d" % (session[:8], len(str(prompt))))
    # No additionalContext: enforcement is MessageDisplay + Stop only.
    return None


def _reply_from(data: dict, entries) -> str:
    reply = last_reply(entries)
    if not reply:
        reply = (data.get("last_assistant_message") or data.get("message") or "").strip()
    return reply or ""


def _turn_key(session: str, user: str) -> str:
    import hashlib
    return hashlib.sha1(((session or "") + "\0" + (user or "")).encode()).hexdigest()[:16]


def _check_expectation(session: str, reply: str):
    rec = get_expectation(session)
    if not rec:
        return None
    expected = rec.get("expected") or ""
    if matches_expectation(reply, expected):
        clear_expectation(session)
        clear_check_gate(session)
        clear_refusal_chance(session)
        log("relay matched session=%s chars=%d" % (session[:8], len(expected)))
        return None
    retries = int(rec.get("retries") or 0)
    max_retries = int(rec.get("max_retries") or 5)
    if retries >= max_retries:
        clear_expectation(session)
        log("relay gave up session=%s after %d mismatches" % (session[:8], retries))
        return None
    updated = bump_expectation_retry(session) or rec
    retries = int(updated.get("retries") or 0)
    log("relay mismatch session=%s retry=%d/%d" % (session[:8], retries, max_retries))
    return {"decision": "block", "reason": relay_instruction(expected)}


def _handoff_now(reply, entries, user, cwd, session, cfg):
    r = reason(reply, entries, user, entries, cfg)
    if not r and skipped_check(reply):
        r = {"kind": "decline", "by": "skipped-check"}
    if not r:
        return None
    mark_seen(session, reply)
    try:
        result = complete(r, reply, asked(user, entries), cwd, entries, entries, cfg)
    except Exception as e:  # noqa: BLE001
        log("stop handoff failed session=%s: %s" % (session[:8], e))
        from .handoff import label
        from .config import provider_config
        pcfg = provider_config(cfg) or {}
        result = label(pcfg.get("name") or pcfg.get("type") or "provider",
                       "Handoff failed: %s: %s" % (type(e).__name__, e))
    if not result:
        return None
    clear_check_gate(session)
    set_expectation(session, result)
    log("stop blocked session=%s chars=%d (exact relay)" % (session[:8], len(result)))
    return {"decision": "block", "reason": relay_instruction(result)}


def _first_chance(session: str, user: str, reply: str):
    """One Stop block telling Claude to do the work; said once per request."""
    turn = _turn_key(session, user)
    n = refusal_chance(session)
    if n >= 1:
        return None
    bump_refusal_chance(session)
    if not said_once(session, turn, "refusal-chance", REFUSAL_CHANCE):
        # Already said this turn — fall through to handoff.
        return None
    log("stop refusal-chance session=%s" % session[:8])
    return {"decision": "block", "reason": REFUSAL_CHANCE}


def on_stop():
    data = _read()
    session = data.get("session_id") or ""
    path = data.get("transcript_path") or ""
    entries = load_transcript(path)
    reply = _reply_from(data, entries)
    cfg = load_config()

    if get_expectation(session):
        return _check_expectation(session, reply)

    if not cfg.get("enabled", True):
        return None

    if reply and is_cleared(session, reply):
        clear_check_gate(session)
        log("stop cleared-by-check session=%s" % session[:8])
        return None

    user = last_request(session) or (person_messages(entries)[-1] if person_messages(entries) else "")
    cwd = data.get("cwd") or ""

    declining = bool(reply and (reason(reply, entries, user, entries, cfg) or skipped_check(reply)))
    if declining:
        # First refusal: one chance to do the work. Second (or skip): hand off.
        chance = _first_chance(session, user, reply)
        if chance:
            return chance
        out = _handoff_now(reply, entries, user, cwd, session, cfg)
        if out:
            return out

    if not (reply or "").strip():
        return None

    # Non-decline uncleared reply: allow (MessageDisplay only hides declines /
    # mismatched relays). Optional: still run classic handoff if reason fires.
    result = run(reply, entries=entries, user=user, history=entries, cwd=cwd,
                 session=session, cfg=cfg)
    if result:
        set_expectation(session, result)
        log("stop blocked session=%s chars=%d (exact relay)" % (session[:8], len(result)))
        return {"decision": "block", "reason": relay_instruction(result)}
    return None


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv or argv[0] in ("-h", "--help"):
        sys.stderr.write("usage: python3 -m claude_handoff.hooks {prompt|stop|display}\n")
        return 2
    cmd = argv[0]
    if cmd == "prompt":
        _emit(on_prompt())
        return 0
    if cmd == "stop":
        _emit(on_stop())
        return 0
    if cmd == "display":
        return on_display()
    sys.stderr.write("unknown hook: %s\n" % cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main())
