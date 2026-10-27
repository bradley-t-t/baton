"""Claude Code hook entry points.

Claude Code feeds each hook a JSON object on stdin and reads JSON (or nothing) on stdout.

  UserPromptSubmit  -> remember the person's request for this session
  Stop              -> if the final reply is a decline, hand it off, wait, and block the stop
                       with the provider's labeled answer so Claude relays it
"""

from __future__ import annotations

import json
import sys

from .config import load as load_config
from .handoff import run
from .state import last_request, log, remember_request
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
    # Pass through: no additionalContext unless we later want one.
    return None


def on_stop():
    data = _read()
    # Prevent loops: when Claude Code re-runs Stop after we blocked it with a reason,
    # stop_hook_active is true. Never hand off again in that case.
    if data.get("stop_hook_active"):
        return None
    cfg = load_config()
    if not cfg.get("enabled", True):
        return None
    session = data.get("session_id") or ""
    path = data.get("transcript_path") or ""
    entries = load_transcript(path)
    reply = last_reply(entries)
    if not reply:
        # Some Claude Code builds put the final text on the event itself.
        reply = (data.get("last_assistant_message") or data.get("message") or "").strip()
    user = last_request(session) or (person_messages(entries)[-1] if person_messages(entries) else "")
    cwd = data.get("cwd") or ""
    result = run(reply, entries=entries, user=user, history=entries, cwd=cwd, session=session, cfg=cfg)
    if not result:
        return None
    # Block the stop so Claude has to continue, with the labeled handoff text as the reason.
    # Claude Code shows reason to the model as Stop-hook feedback; Claude then relays it.
    log("stop blocked session=%s chars=%d" % (session[:8], len(result)))
    return {"decision": "block", "reason": result}


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
