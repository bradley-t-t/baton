"""Claude Code hook entry points, run as `python3 -m baton.hooks <name>`.

  prompt   UserPromptSubmit: remember the request.
  display  MessageDisplay: when the reply declines, hand the request off and show the
           answer in its place; hide later text in the same turn.
  stop     Stop: hand off here only when display didn't. Never blocks.

Each run also starts a background update when one is due (see baton.update).

The hook commands in ~/.claude/settings.json name this module and these three names,
so neither may change.
"""

from __future__ import annotations

import sys

from .. import keys, update
from .display import on_display
from .prompt import on_prompt
from .stop import on_stop

HOOKS = {"prompt": on_prompt, "stop": on_stop, "display": on_display}


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        sys.stderr.write("usage: python3 -m baton.hooks {prompt|stop|display}\n")
        return 2
    hook = HOOKS.get(argv[0])
    if hook is None:
        sys.stderr.write("unknown hook: %s\n" % argv[0])
        return 2
    # Claude Code speaks UTF-8 JSON; Windows would otherwise use its ANSI code page.
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    # The POSIX hook command sources secrets.env first; the Windows one has no shell.
    keys.load_into_env()
    try:
        return hook()
    finally:
        update.start_if_due()
