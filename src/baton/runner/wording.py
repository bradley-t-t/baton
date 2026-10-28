"""Make the provider's sentence read as done.

The provider writes its prose before the script runs, and the person sees it only after
the script worked, so "Deleting x." has to become "Deleted x.". This covers the common
verbs and openers; a line it can't make read as finished is dropped, and with nothing
left the caller says "Done.".
"""

from __future__ import annotations

import re

# base form -> past form, for the verbs file and shell tasks use.
PAST = {
    "add": "added", "append": "appended", "archive": "archived", "change": "changed",
    "check": "checked", "chmod": "chmodded", "clean": "cleaned", "clear": "cleared",
    "compress": "compressed", "convert": "converted", "copy": "copied",
    "create": "created", "delete": "deleted", "download": "downloaded", "edit": "edited",
    "empty": "emptied", "erase": "erased", "extract": "extracted", "find": "found",
    "fix": "fixed", "generate": "generated", "get": "got", "install": "installed",
    "kill": "killed", "list": "listed", "make": "made", "modify": "modified",
    "move": "moved", "open": "opened", "purge": "purged", "put": "put", "read": "read",
    "rename": "renamed", "remove": "removed", "replace": "replaced", "reset": "reset",
    "restart": "restarted", "run": "ran", "save": "saved", "set": "set", "start": "started",
    "stop": "stopped", "touch": "touched", "uninstall": "uninstalled", "unzip": "unzipped",
    "update": "updated", "wipe": "wiped", "write": "wrote", "zip": "zipped",
}
# -ing forms that don't follow "drop the e, add ing".
ING_EXTRA = {"getting": "get", "putting": "put", "running": "run", "setting": "set",
             "resetting": "reset", "stopping": "stop", "unzipping": "unzip",
             "zipping": "zip", "chmodding": "chmod"}


def _ing(base: str) -> str:
    if base.endswith("e") and not base.endswith("ee"):
        return base[:-1] + "ing"
    return base + "ing"


ING = {_ing(b): b for b in PAST}
ING.update(ING_EXTRA)

ADVERBS = r"(?:(?:\w+ly|also|now|then|just|first)\s+)*"
# "I'll", "Let me", "This will" ... followed by a verb in its base form.
FUTURE = re.compile(r"^(?:I\s+will|I'll|I\s+am\s+going\s+to|I'm\s+going\s+to|Let\s+me|"
                    r"Let's|We\s+will|We'll|This\s+will|These\s+commands\s+will|"
                    r"The\s+script\s+will|It\s+will)\s+(" + ADVERBS + r")(\w+)\b(.*)$",
                    re.I)
LEAD_ING = re.compile(r"^(?:I'm\s+|I\s+am\s+|Now\s+)?(" + ADVERBS + r")(\w+ing)\b(.*)$", re.I)
# Courtesy words in front of the real sentence.
OPENER = re.compile(r"^(?:sure|okay|ok|alright|all\s+right|got\s+it|done|no\s+problem)"
                    r"\b[\s,.!:-]*", re.I)
# Lines that only introduce the commands.
INTRO = re.compile(r"^(?:here(?:'s|\s+is|\s+are)|run\s+(?:this|these|the\s+following)|"
                   r"the\s+following|below|use\s+(?:this|these)|try\s+(?:this|these)|"
                   r"this\s+(?:script|command|block)|these\s+commands)\b", re.I)
STILL_PENDING = re.compile(r"\b(?:will|I'll|we'll|going\s+to|let\s+me|about\s+to)\b", re.I)
AND_ING = re.compile(r"(\b(?:and|then)\s+|,\s*)(\w+ing)\b")


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _later_ings(rest: str) -> str:
    """"... and emptying the Trash" -> "... and emptied the Trash"."""
    def fix(m):
        base = ING.get(m.group(2).lower())
        return m.group(1) + PAST[base] if base else m.group(0)
    return AND_ING.sub(fix, rest)


def _line(line: str) -> str:
    """The line as a finished statement, or "" when it can't be."""
    s = line.strip().lstrip("-*• ").strip()
    while True:
        t = OPENER.sub("", s, count=1).strip()
        if t == s:
            break
        s = t
    if not s or s.endswith(":") or INTRO.match(s):
        return ""
    m = FUTURE.match(s)
    if m:
        adverbs, verb, rest = m.groups()
        past = PAST.get(verb.lower())
        if not past:
            return ""
        s = _cap(adverbs + past + _later_ings(rest))
    else:
        m = LEAD_ING.match(s)
        if m:
            adverbs, verb, rest = m.groups()
            base = ING.get(verb.lower())
            if not base:
                return ""  # some other "-ing" opener: it still reads as in progress
            s = _cap(adverbs + PAST[base] + _later_ings(rest))
    if STILL_PENDING.search(s):
        return ""
    s = re.sub(r"(?:\s+now)?[\s.…]*$", "", s)
    s = _cap(s)
    return s + "." if s and s[-1] not in "!?" else s


def finished(prose: str) -> str:
    """The provider's prose as it should read after the script worked; "" for none."""
    lines = [_line(x) for x in (prose or "").splitlines()]
    return "\n".join(x for x in lines if x)
