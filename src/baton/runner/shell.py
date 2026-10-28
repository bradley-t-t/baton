"""Reading shell scripts: fenced blocks, logical lines, simple commands and their
executables. Used for the allow list and the command cap."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

FENCE = re.compile(r"```(?:bash|sh|shell|zsh|fish|powershell|pwsh|ps1)\s*\n(.*?)```", re.S | re.I)
BARE_FENCE = re.compile(r"```\s*\n(.*?)```", re.S)

PREFIXES = {"sudo", "command", "time", "nice", "nohup", "env"}

# Shell keywords / punctuation that are not executables for the allow-list scan.
KEYWORDS = {
    "if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done",
    "case", "esac", "in", "select", "function", "time", "coproc", "!", "{", "}",
    "[[", "]]", "(", ")", ":", "true", "false",
}


def extract_fences(text: str) -> list:
    """Bodies of shell fenced blocks (prefer tagged fences; else bare)."""
    blocks = list(FENCE.finditer(text or ""))
    if not blocks:
        blocks = list(BARE_FENCE.finditer(text or ""))
    return [m.group(1) for m in blocks]


def logical_lines(body: str) -> list:
    """Join lines ending in \\; skip blanks and # comments."""
    lines = []
    buf = ""
    for raw in (body or "").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not buf and (not stripped or stripped.startswith("#")):
            continue
        if line.endswith("\\") and not line.endswith("\\\\"):
            buf += line[:-1]
            continue
        buf += line
        s = buf.strip()
        buf = ""
        if s and not s.startswith("#"):
            lines.append(s)
    if buf.strip() and not buf.strip().startswith("#"):
        lines.append(buf.strip())
    return lines


def strip_fences(text: str) -> str:
    t = FENCE.sub("", text or "")
    t = BARE_FENCE.sub("", t)
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def first_word(cmd: str) -> str:
    """The executable token after sudo/env/... prefixes and VAR=val assignments."""
    try:
        parts = shlex.split(cmd, posix=True)
    except ValueError:
        # Unbalanced quotes: fall back to a rough first token so allow-list still works.
        parts = re.split(r"\s+", cmd.strip(), maxsplit=8)
    if not parts:
        return ""
    # Control-structure lines are not executables for the allow-list.
    head = Path(parts[0].lstrip("./")).name
    if head in {"for", "while", "until", "if", "elif", "else", "fi", "do", "done",
                "case", "esac", "then", "select", "function"}:
        return ""
    i = 0
    while i < len(parts):
        tok = parts[i]
        if not tok:
            i += 1
            continue
        # VAR=value prefix
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok) and "/" not in tok.split("=", 1)[0]:
            i += 1
            continue
        base = tok.lstrip("./")
        name = Path(base).name
        if name in PREFIXES or tok.startswith("-"):
            if name == "env":
                i += 1
                while i < len(parts) and "=" in parts[i] and not parts[i].startswith("-"):
                    i += 1
                continue
            i += 1
            continue
        if name in KEYWORDS:
            i += 1
            continue
        return name
    return ""


# Words that open or close a compound command; what follows them is the command.
LEADING = {"do", "then", "else", "elif", "if", "while", "until", "{", "}", "!", "time",
           "done", "fi", "esac"}
BACKTICK = re.compile(r"`([^`]*)`")
# fd duplication (2>&1, >&2, <&0, 2>&-) and &> / &>> redirects. Their & is not a
# command separator, so it is taken out before splitting.
FD_DUP = re.compile(r"(\d*)([<>])&(\d+|-)")
BOTH_REDIRECT = re.compile(r"&(>>?)")


def simple_commands(line: str) -> list:
    """Every simple command in one logical line, for the allow list and the cap.

    Splits at && || | ; & and at ( ) so chains, loop bodies, subshells and $(...)
    are each checked, not just the first word of the line. Commands inside
    backticks are checked too. A for/select/case header runs nothing and is
    skipped. Text that can't be tokenized is returned whole, which fails closed
    unless its first word is allowed.
    """
    out = []
    for inner in BACKTICK.findall(line):
        out.extend(simple_commands(inner))
    line = BACKTICK.sub(" x ", line)
    line = BOTH_REDIRECT.sub(r" \1", FD_DUP.sub(" ", line))
    try:
        lex = shlex.shlex(line, posix=True, punctuation_chars=";&|()")
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        return out + [line]
    seg = []
    def flush():
        while seg and seg[0] in LEADING:
            seg.pop(0)
        if seg and seg[0] == "[":
            seg[0] = "test"  # [ is test; [[ ]] is shell syntax that runs nothing itself
        if seg and seg[0] not in ("for", "select", "case", "in", "[["):
            out.append(shlex.join(seg))
        seg.clear()
    for tok in toks:
        if tok and set(tok) <= set(";&|()"):
            flush()
        else:
            seg.append(tok)
    flush()
    return out


def writes_file(line: str) -> bool:
    """Whether a line redirects output into a file (anything but /dev/null or an fd)."""
    try:
        lex = shlex.shlex(line, posix=True, punctuation_chars=";&|()<>")
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        return ">" in line
    for i, tok in enumerate(toks):
        if tok and set(tok) <= set("<>&|") and ">" in tok:
            nxt = toks[i + 1] if i + 1 < len(toks) else ""
            if nxt == "/dev/null" or (tok.endswith("&") and nxt.isdigit()):
                continue
            return True
    return False


def script_commands(body: str) -> list:
    """All simple commands in a script, each with a real executable as its first word."""
    out = []
    for line in logical_lines(body):
        out.extend(c for c in simple_commands(line) if first_word(c))
    return out
