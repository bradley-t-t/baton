"""Reading a reply for the few things a pattern can tell on its own.

Whether a reply refused the request is not one of them: that question goes to the
judge (see judge.py), which reads the reply against what the person asked. What is
left here are flat facts about the text — a tool call that never ran, a Holdback
denial, a message about Baton itself — each one a single pattern.
"""

from __future__ import annotations

import re

# A tool call made for the request that never ran: an approval prompt timed out, or a hook,
# policy or permission rule blocked it.
BLOCKED = re.compile(
    r"\btimed out after \d+\s*s\b.{0,200}?(?:asked the user|no answer came|approv|confirm|permission)"
    r"|\bThe server asked the user a question\b"
    r"|\b(?:approval|confirmation|permission|elicitation)(?:\s+(?:prompt|request|dialog|form|step)s?)?\s+"
    r"(?:timed out|expired|was never answered|never (?:reached|appeared|came|showed))"
    r"|\b(?:blocked|denied|rejected)\s+by\s+(?:a\s+|the\s+|your\s+)?(?:[\w.-]+\s+){0,2}(?:hooks?|polic(?:y|ies)|"
    r"auto[- ]?mode|permission rules?|permissions? settings?|classifier|administrator|admin|organization)\b"
    r"|\bPermission (?:for this action|to use \S+) (?:has been|was|is) denied\b"
    r"|\b(?:requires|needs) (?:the user'?s? |your |user )?(?:approval|confirmation) (?:before|to)\b",
    re.I | re.S)

# The person said no to a prompt themselves: that is their answer and is never handed off.
USER_SAID_NO = re.compile(
    r"\bThe user doesn'?t want to proceed\b|\btool use was rejected\b|\buser (?:rejected|declined|denied|cancel+ed)\b"
    r"|\b(?:you|the user|the person) (?:declined|rejected|denied|said no to|cancel+ed)\b", re.I)

# The person's own Holdback hook refused the call and told Claude not to hand it off.
HOLDBACK_HELD = re.compile(
    r"\bHoldback:\s*held\b"
    r"|\bHoldback (?:hook )?blocked\b"
    r"|\bnever hand this off\b"
    r"|\bmust not be handed off\b",
    re.I,
)

NOT_RUN = re.compile(
    r"\b(?:didn'?t|did not|never|couldn'?t|could not|wasn'?t|was not|hasn'?t|has not)\s+(?:been\s+)?"
    r"(?:run|ran|execute|executed|go through|went through|happen|apply|applied)\b"
    r"|\bnothing (?:ran|was (?:run|changed|deleted|applied))\b|\bnot (?:run|executed|applied)\b", re.I)

SKIPPED_CHECK = re.compile(
    r"\bI skipped\b"
    r"|\bI (?:did not|didn't) (?:run|use|follow).{0,60}\b(?:check|handoff|hook|tether)\b"
    r"|\bskipped (?:both|the )?(?:baton|handoff|tether|check|hooks?)\b"
    r"|\b(?:would|will) (?:pass|hand).{0,40}\b(?:another agent|handoff|Grok)\b"
    r"|\bI (?:won't|will not|am not going to) (?:post|send|relay).{0,40}\b(?:Grok|handoff)\b",
    re.I,
)

ADMITS_BLOCK = re.compile(r"\btimed? ?out\b|\bblocked\b|\bdenied\b|\bapprov|\bconfirm", re.I)

ABOUT_BATON = re.compile(
    r"(?<![/\w.-])baton (?:hooks?|tool|provider|config|install|check|log|handoff)\b"
    r"|\b(?:why|did|does|is|didn't|doesn't|isn't|wasn't) baton\b(?![/-])"
    r"|\bhand ?off (?:hook|tool|provider|config)\b"
    r"|\b(?:the|this|that) hand ?off\b"
    r"|\bhandback\b",
    re.I,
)


def normalize(text: str) -> str:
    return ((text or "").replace("’", "'").replace("‘", "'").replace("ʼ", "'")
            .replace("“", '"').replace("”", '"'))


def admits_block(reply: str) -> bool:
    """A reply that leaves a blocked action undone: it says so in as many words."""
    t = normalize(reply)
    return bool(NOT_RUN.search(t) or ADMITS_BLOCK.search(t))


def holdback_held(text: str) -> bool:
    """Whether text is a Holdback denial (tool result or Claude reporting it)."""
    return bool(HOLDBACK_HELD.search(normalize(text or "")))


def about_handoff(text: str) -> bool:
    """Whether the person's own message is about Baton itself ("why didn't the handoff
    fire?"). Such a turn is a conversation about the tool, not a task to hand off.

    "baton" is also an everyday word, so it only counts next to a word about the tool
    ("the baton hook", "why didn't baton fire").
    """
    return bool(ABOUT_BATON.search(normalize(text).lower()))


def skipped_check(text: str) -> bool:
    """Whether the reply admits skipping the handoff on purpose."""
    return bool(SKIPPED_CHECK.search(normalize(text or "")))
