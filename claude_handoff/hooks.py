"""Claude Code hook entry points.

Claude Code feeds each hook a JSON object on stdin and reads JSON (or nothing) on stdout.

  UserPromptSubmit  -> remember the request AND inject the draft-check step
  Stop              -> backstop: decline→handoff→exact-relay; allow when check already cleared
"""

from __future__ import annotations

import json
import sys

from .check import check_step_text
from .config import load as load_config
from .handoff import matches_expectation, relay_instruction, run
from .state import (
    bump_expectation_retry,
    clear_expectation,
    get_expectation,
    is_cleared,
    last_request,
    log,
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


def _check_expectation(session: str, reply: str):
    rec = get_expectation(session)
    if not rec:
        return None
    expected = rec.get("expected") or ""
    if matches_expectation(reply, expected):
        clear_expectation(session)
        log("relay matched session=%s chars=%d" % (session[:8], len(expected)))
        return None
    retries = int(rec.get("retries") or 0)
    max_retries = int(rec.get("max_retries") or 3)
    if retries >= max_retries:
        clear_expectation(session)
        log("relay gave up session=%s after %d mismatches" % (session[:8], retries))
        return None
    updated = bump_expectation_retry(session) or rec
    retries = int(updated.get("retries") or 0)
    log("relay mismatch session=%s retry=%d/%d" % (session[:8], retries, max_retries))
    return {"decision": "block", "reason": relay_instruction(expected)}


def on_stop():
    data = _read()
    session = data.get("session_id") or ""
    path = data.get("transcript_path") or ""
    entries = load_transcript(path)
    reply = _reply_from(data, entries)

    if get_expectation(session):
        return _check_expectation(session, reply)

    if data.get("stop_hook_active"):
        return None

    # Draft check already cleared this exact reply — do not hand it off again.
    if reply and is_cleared(session, reply):
        log("stop cleared-by-check session=%s" % session[:8])
        return None

    cfg = load_config()
    if not cfg.get("enabled", True):
        return None
    user = last_request(session) or (person_messages(entries)[-1] if person_messages(entries) else "")
    cwd = data.get("cwd") or ""
    result = run(reply, entries=entries, user=user, history=entries, cwd=cwd, session=session, cfg=cfg)
    if not result:
        return None
    set_expectation(session, result)
    reason = relay_instruction(result)
    log("stop blocked session=%s chars=%d (exact relay)" % (session[:8], len(result)))
    return {"decision": "block", "reason": reason}


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
