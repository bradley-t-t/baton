"""Forwards `python3 -m claude_handoff.cli` to `baton.cli`."""
import sys

from baton.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
