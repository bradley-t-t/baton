"""Claude Code hook entry points.

  UserPromptSubmit  -> remember the request (no instructions injected)
  MessageDisplay    -> on a refusal, hand off and show `<result>\\n(via Grok)`;
                       blank later narration in the same turn
  Stop              -> never blocks. Hands off (no block) only if display did not,
                       so the next display draws the result.
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
    run,
)
from .state import (
    clear_check_gate,
    clear_displayed,
    clear_expectation,
    clear_refusal_chance,
    clear_said,
    get_displayed,
    get_expectation,
    is_cleared,
    last_request,
    log,
    mark_displayed,
    mark_seen,
    remember_request,
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
        clear_displayed(session)
        clear_expectation(session)
        log("prompt session=%s chars=%d" % (session[:8], len(str(prompt))))
    # No additionalContext: enforcement is MessageDisplay + Stop only.
    return None


def _reply_from(data: dict, entries) -> str:
    reply = last_reply(entries)
    if not reply:
        reply = (data.get("last_assistant_message") or data.get("message") or "").strip()
    return reply or ""


def _check_expectation(session: str, reply: str):
    """Legacy exact-relay path. Prefer display-shown results; never leave a blank."""
    rec = get_expectation(session)
    if not rec:
        return None
    expected = rec.get("expected") or ""
    if matches_expectation(reply, expected):
        mark_displayed(session, expected)
        clear_expectation(session)
        clear_check_gate(session)
        clear_refusal_chance(session)
        log("relay matched session=%s chars=%d" % (session[:8], len(expected)))
        return None
    # Result was already drawn by MessageDisplay — stop re-blocking.
    shown = get_displayed(session)
    if shown and (shown.get("result") or "").strip():
        clear_expectation(session)
        log("relay skip; already displayed session=%s" % session[:8])
        return None
    # Never block for exact relay: the display hook draws the result itself.
    mark_displayed(session, expected)
    clear_expectation(session)
    log("relay skip; display owns result session=%s" % session[:8])
    return None


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
    # Display already owns the screen when it can; mark displayed so we never
    # depend on Claude pasting. Keep a soft expectation only as fallback.
    mark_displayed(session, result)
    clear_expectation(session)
    log("stop handed off + displayed session=%s chars=%d" % (session[:8], len(result)))
    # No exact-relay block — the display hook shows the result.
    return None


def on_stop():
    data = _read()
    session = data.get("session_id") or ""
    path = data.get("transcript_path") or ""
    entries = load_transcript(path)
    reply = _reply_from(data, entries)
    cfg = load_config()

    # Display already put the Grok result on screen for this request.
    shown = get_displayed(session)
    if shown and (shown.get("result") or "").strip():
        clear_expectation(session)
        clear_check_gate(session)
        log("stop allow; already displayed session=%s" % session[:8])
        return None

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
        # No Stop block (each block is a visible "Hook re-prompted Claude" row).
        # Display usually already handed off; if not (timeout/miss), do it here
        # and mark displayed so the next MessageDisplay can draw the result.
        return _handoff_now(reply, entries, user, cwd, session, cfg)

    if not (reply or "").strip():
        return None

    result = run(reply, entries=entries, user=user, history=entries, cwd=cwd,
                 session=session, cfg=cfg)
    if result:
        mark_displayed(session, result)
        clear_expectation(session)
        log("stop handed off + displayed session=%s chars=%d" % (session[:8], len(result)))
        return None
    return None


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv or argv[0] in ("-h", "--help"):
        sys.stderr.write("usage: python3 -m baton.hooks {prompt|stop|display}\n")
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
