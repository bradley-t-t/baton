"""UserPromptSubmit: remember the request. Nothing is added to Claude's context."""

from __future__ import annotations

from ..state import clear_displayed, log, remember_request
from .event import read_event


def on_prompt(data: dict | None = None) -> int:
    data = read_event() if data is None else data
    prompt = data.get("prompt") or data.get("user_prompt") or ""
    if isinstance(prompt, list):
        prompt = "\n".join(b.get("text", "") for b in prompt if isinstance(b, dict))
    session = data.get("session_id") or ""
    if prompt:
        remember_request(session, str(prompt))
        clear_displayed(session)
        log("prompt session=%s chars=%d" % (session[:8], len(str(prompt))))
    return 0
