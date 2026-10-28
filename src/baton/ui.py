"""Small terminal helpers for setup, doctor and uninstall. Standard library only."""

from __future__ import annotations

import getpass
import os
import sys

from . import system


def color_on(stream=None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    return hasattr(stream, "isatty") and stream.isatty() and system.ansi_colors()


def _c(code: str, text: str) -> str:
    return "\033[%sm%s\033[0m" % (code, text) if color_on() else text


def bold(t): return _c("1", t)
def dim(t): return _c("2", t)
def green(t): return _c("32", t)
def yellow(t): return _c("33", t)
def red(t): return _c("31", t)
def cyan(t): return _c("36", t)


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def say(text: str = ""):
    print(text)


def heading(text: str):
    print()
    print(bold(text))


def ask(prompt: str, default: str = "") -> str:
    hint = " [%s]" % default if default else ""
    try:
        got = input("%s%s: " % (prompt, dim(hint))).strip()
    except EOFError:
        got = ""
    return got or default


def ask_secret(prompt: str) -> str:
    try:
        return getpass.getpass("%s: " % prompt).strip()
    except EOFError:
        return ""


def confirm(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        try:
            got = input("%s %s " % (prompt, dim("[%s]" % hint))).strip().lower()
        except EOFError:
            return default
        if not got:
            return default
        if got in ("y", "yes"):
            return True
        if got in ("n", "no"):
            return False


def choose(prompt: str, options: list, default: int = 0) -> int:
    """Numbered menu. options is a list of (label, detail). Returns the index."""
    print(prompt)
    for i, (label, detail) in enumerate(options, 1):
        mark = cyan(">") if i - 1 == default else " "
        print(" %s %d) %s%s" % (mark, i, label, dim("  " + detail) if detail else ""))
    while True:
        got = ask("Choose 1-%d" % len(options), str(default + 1))
        if got.isdigit() and 1 <= int(got) <= len(options):
            return int(got) - 1
