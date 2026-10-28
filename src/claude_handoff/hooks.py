"""Forwards `python3 -m claude_handoff.hooks` to `baton.hooks`."""
import sys

from baton.hooks import main

if __name__ == "__main__":
    sys.exit(main())
