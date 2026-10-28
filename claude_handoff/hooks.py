"""Claude Code hook entry points.

Claude Code feeds each hook a JSON object on stdin and reads JSON (or nothing) on stdout.

  UserPromptSubmit  -> remember the request AND inject the draft-check step
  Stop              -> hard gate: every person-facing reply must be check-cleared
                      or the exact expected handoff replacement; declines/skips
                      hand off immediately and exact-relay until match.
"""

from __future__ import annotations

import json
import sys

from .check import check_command, check_step_text
from .config import load as load_config
from .detect import skipped_check
from .handoff import matches_expectation, reason, relay_instruction, run, complete, asked
from .state import (
    MAX_CHECK_GATE_RETRIES,
    bump_check_gate,
    bump_expectation_retry,
    clear_check_gate,
    clear_expectation,
    get_check_gate,
    get_expectation,
    is_cleared,
    last_request,
    log,
    mark_seen,
    remember_request,
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
        log("prompt session=%s chars=%d" % (session[:8], len(str(prompt))))
    step = check_step_text()
    if not step:
        return None
    return {"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": step,
    }}


def _reply_from(data: dict, entries) -> str:
    reply = last_reply(entries)
    if not reply:
        reply = (data.get("last_assistant_message") or data.get("message") or "").strip()
    return reply or ""


def _check_required_reason() -> str:
    return (
        "Run claude-handoff-check on your draft before sending. "
        "Write the draft to a file and run %s <file>; then send only what it tells you."
        % check_command()
    )


def _check_expectation(session: str, reply: str):
    rec = get_expectation(session)
    if not rec:
        return None
    expected = rec.get("expected") or ""
    if matches_expectation(reply, expected):
        clear_expectation(session)
        clear_check_gate(session)
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
    """Run handoff+actions for an uncleared decline/skip; store exact expected text."""
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


def on_stop():
    data = _read()
    session = data.get("session_id") or ""
    path = data.get("transcript_path") or ""
    entries = load_transcript(path)
    reply = _reply_from(data, entries)
    cfg = load_config()
    active = bool(data.get("stop_hook_active"))

    # Exact-relay always wins, including when stop_hook_active.
    if get_expectation(session):
        return _check_expectation(session, reply)

    if not cfg.get("enabled", True):
        return None

    # Cleared by draft check → allow.
    if reply and is_cleared(session, reply):
        clear_check_gate(session)
        log("stop cleared-by-check session=%s" % session[:8])
        return None

    user = last_request(session) or (person_messages(entries)[-1] if person_messages(entries) else "")
    cwd = data.get("cwd") or ""

    # Uncleared decline or "I skipped the check/handoff" → hand off now, exact-relay.
    if reply and (reason(reply, entries, user, entries, cfg) or skipped_check(reply)):
        out = _handoff_now(reply, entries, user, cwd, session, cfg)
        if out:
            return out
        # handoff failed to produce text; fall through to check-required block

    # No person-facing text yet → allow (tool-only turn).
    if not (reply or "").strip():
        return None

    # Uncleared reply: hard-block until Claude runs the check (cap retries).
    gate = get_check_gate(session)
    retries = int(gate.get("retries") or 0)
    if retries >= MAX_CHECK_GATE_RETRIES:
        clear_check_gate(session)
        log("stop check-gate gave up session=%s after %d; falling through" % (
            session[:8], retries))
        # Last resort: classic decline→handoff path (run() no-ops if not a decline).
        result = run(reply, entries=entries, user=user, history=entries, cwd=cwd,
                     session=session, cfg=cfg)
        if result:
            set_expectation(session, result)
            log("stop blocked session=%s chars=%d (exact relay after gate)" % (
                session[:8], len(result)))
            return {"decision": "block", "reason": relay_instruction(result)}
        return None

    bump_check_gate(session)
    log("stop check-required session=%s retry=%d/%d active=%s" % (
        session[:8], retries + 1, MAX_CHECK_GATE_RETRIES, active))
    return {"decision": "block", "reason": _check_required_reason()}


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv or argv[0] in ("-h", "--help"):
        sys.stderr.write("usage: python3 -m claude_handoff.hooks {prompt|stop}\n")
        return 2
    cmd = argv[0]
    if cmd == "prompt":
        _emit(on_prompt())
        return 0
    if cmd == "stop":
        _emit(on_stop())
        return 0
    sys.stderr.write("unknown hook: %s\n" % cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main())
