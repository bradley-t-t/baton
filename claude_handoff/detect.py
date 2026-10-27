"""Decline detection: is a reply Claude's own first-person refusal of the request?

The word scan here is deliberately narrow. A reply that only talks about refusals, policy or
safety as a topic, one that was cut off by a filter or an API error, or one that failed for a
plain technical reason (a missing tool, a network error) is not a decline.
"""

from __future__ import annotations

import re

# Actions a refusal can name: "I can't <act>", "I won't <act>".
_ACT = (r"(?:help|do|run|perform|assist|delete|drop|remove|wipe|purge|erase|clear|truncate|execute|make|carry|"
        r"proceed|write|build|go ahead|send|push|deploy|change|modify|touch|apply|take|follow|"
        r"comply|re-?run|that|this|it|with|pass|create|sign|enter|type|buy|purchase|pay|order|submit|post|publish|"
        r"approve|accept|install|uninstall|kill|trash|overwrite|force|merge|reset|rewrite|transfer|move|share|"
        r"upload|bypass|solve|click|fill|edit|rename|provide|give|disable|enable|email|message|text|reply|"
        r"book|cancel|sell|trade|generate|answer|draft|translate|summarize)")

DECLINE = re.compile(
    r"\bI\s*(?:really\s+|just\s+|still\s+|honestly\s+)?"
    r"(?:can'?t|cannot|can not|won'?t|will not|am unable to|'m unable to|am not able to|'m not able to|"
    r"'m not going to|am not going to|shouldn'?t|should not|'m not willing to|am not willing to|"
    r"'m not allowed to|am not allowed to|'m not permitted to|am not permitted to)\s+"
    r"(?:\w+ly\s+)?(?:be the one to\s+|be able to\s+|go and\s+|actually\s+)?(?:\w+ly\s+)?" + _ACT + r"\b"
    r"|\bI\s*(?:can'?t|cannot|won'?t|will not)\s*[.!]"
    r"|\bI\s*(?:need to|have to|must|'ll have to|'m going to have to|'d have to)\s+(?:respectfully\s+|politely\s+)?(?:decline|refuse|pass)\b"
    r"|\bI(?:'ll| will|'m going to)\s+(?:have to\s+)?pass on\b"
    # A rule, a limit or a moral line of its own, in any wording.
    r"|\bI\s+(?:always\s+|only\s+)?leave\s+(?:that|this|it|those|these)?\s*(?:up\s+)?to you\b"
    r"|\b(?:one of the|among the|the kind of|the sort of)\s+(?:few\s+|only\s+|many\s+)?(?:things|actions|steps|requests|areas|lines|places)\s+"
    r"I\s+(?:don'?t|do not|won'?t|will not|never|can'?t|cannot|leave|keep|stay|refuse|decline|avoid|stop)\b"
    r"|\b(?:against|violates?|breaks?|crosses|outside of)\s+(?:my|our)\s+(?:own\s+)?(?:rules|guidelines|policies|policy|principles|values|ethics|"
    r"limits|boundaries|instructions|safety rules|usage policy)\b"
    r"|\b(?:one of my|my|a)\s+hard\s+(?:limits?|lines?|rules?|stops?)\b"
    r"|\boff[- ]limits\s+(?:for me|to me|even (?:when|if|though) you)"
    r"|\bI\s+(?:don'?t|do not)\s+feel\s+(?:comfortable|right|good)\b"
    r"|\bI\s+(?:don'?t|do not)\s+think\s+I\s+(?:should|can|ought to)\b"
    r"|\bI\s+(?:don'?t|do not|never)\s+(?:do|help with|assist with|touch)\s+(?:that|this|those|these)\b"
    r"|\byou(?:'ll| will)?\s+(?:need|have)\s+to\s+(?:\w+\s+){0,5}?(?:yourself|on your own|manually)\b"
    r"|\bI(?:'m| am)\s+(?:not (?:going|willing|comfortable|allowed|permitted)|unwilling)\b"
    r"|\bI(?:'m| am|'ll| will)?\s+(?:(?:still|just|really|simply|honestly|absolutely|definitely|certainly|ever|"
    r"now|also|permanently)\s+)+(?:not going to|not|won'?t|will not|never|refuse to|decline to)\s+"
    r"(?:\w+ly\s+)?(?:be the one to\s+|be able to\s+)?" + _ACT + r"\b"
    r"|\bI(?:'m| am)\s+(?:still\s+|just\s+|also\s+)?not\s+(?:handing|deleting|doing|running|sending|touching|removing|"
    r"changing|helping|buying|posting|submitting|creating|signing|typing|entering|paying|executing|wiping|dropping|writing)\b"
    r"|\b(?:a|my|the)\s+(?:fixed|hard|firm|bright|red)\s+line\s+(?:for me|I)\b"
    r"|\bone of my\s+(?:fixed|hard|firm|bright|red)\s+lines\b"
    r"|\basking (?:me )?(?:again|twice|harder)\s+(?:won'?t|will not|doesn'?t|does not)\b"
    r"|\bI\s*(?:refuse|decline)\b"
    r"|\bI'?m not comfortable\b|\bI am not comfortable\b"
    r"|\bI'?d rather not\b|\bI would rather not\b|\bI'?d prefer not to\b"
    r"|\bI(?:'?m| am| was| got|'?ve been| have been)\s+(?:being\s+)?(?:blocked|prevented|stopped|barred)\s+from\b"
    r"|\bI(?:'?m| am)\s+blocked\b"
    r"|\b(?:won'?t|doesn'?t|does not|don'?t|do not|wouldn'?t)\s+(?:let|allow|permit)\s+me\s+(?:to\s+)?" + _ACT + r"\b"
    r"|\b(?:block|blocks|blocked|stops|stopped|prevents|prevented|bars)\s+me\s+from\b"
    r"|\b(?:not|isn'?t|aren'?t)\s+something I(?:'?m| am|'ll|'d| would| should)?\s+(?:can|will|able|willing|going|do|help|comfortable)\b",
    re.I)

LEAD_CHARS = 400      # a refusal near the top of the reply is a decline
SHORT_REPLY = 700     # so is a short reply with a refusal in it
DOMINANT_SHARE = 0.3  # or one where refusing sentences are a large share

# "I can't run the tests because pytest isn't installed" is a failure, not a refusal.
TECHNICAL = re.compile(
    r"\b(?:not installed|isn'?t installed|not found|command not found|doesn'?t exist|does not exist|no such file|"
    r"missing|offline|timed? ?out|network|no internet|unreachable|connection|don'?t have access to the|"
    r"not available in this|isn'?t available in this|without (?:the|a|an) (?:file|path|token|key|credentials))\b", re.I)

SAFETY_WORDS = re.compile(
    r"safety|policy|guardrail|destructive|irreversible|comfortable|rules?\b|allowed|permitted|refuse|decline", re.I)

# A reply about its own answer being cut off, filtered or failing is not a refusal of the task.
INTERRUPTED = re.compile(
    r"\b(?:safety|content|output|response|usage)[ -](?:filter|classifier)s?\b|\bclassifiers?\b"
    r"|\bthe filter\b|\bfiltered\b|\bcut (?:off|short)\b|\bpart ?way\b|\bmid-?(?:way|reply|sentence|answer)\b"
    r"|\btruncated\b|\bwithheld\b|\bAPI error\b|\boverloaded\b|\brate[- ]limit|\binternal server error\b"
    r"|\b(?:stopped|blocked|interrupted|ended|killed)\s+my\s+(?:last\s+|previous\s+)?(?:reply|response|answer|message|output)\b"
    r"|\b(?:reply|response|answer|message|output)\s+(?:was|got|has been|is being)\s+(?:stopped|cut|blocked|interrupted|withheld|filtered)\b",
    re.I)

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
# The person's own Holdback hook refused the call and told Claude not to hand it off.
HOLDBACK_HELD = re.compile(
    r"\bHoldback:\s*held\b"
    r"|\bHoldback (?:hook )?blocked\b"
    r"|\bnever hand this off\b"
    r"|\bmust not be handed off\b",
    re.I,
)

USER_SAID_NO = re.compile(
    r"\bThe user doesn'?t want to proceed\b|\btool use was rejected\b|\buser (?:rejected|declined|denied|cancel+ed)\b"
    r"|\b(?:you|the user|the person) (?:declined|rejected|denied|said no to|cancel+ed)\b", re.I)

NOT_RUN = re.compile(
    r"\b(?:didn'?t|did not|never|couldn'?t|could not|wasn'?t|was not|hasn'?t|has not)\s+(?:been\s+)?"
    r"(?:run|ran|execute|executed|go through|went through|happen|apply|applied)\b"
    r"|\bnothing (?:ran|was (?:run|changed|deleted|applied))\b|\bnot (?:run|executed|applied)\b", re.I)


def normalize(text: str) -> str:
    return ((text or "").replace("\u2019", "'").replace("\u2018", "'").replace("\u02bc", "'")
            .replace("\u201c", '"').replace("\u201d", '"'))


def own_words(text: str) -> str:
    """The reply without what it quotes: code, block quotes and "quoted" text are someone
    else's words, so 'the old reply said "I won't"' is not this reply refusing."""
    t = re.sub(r"```.*?(?:```|$)", " ", text, flags=re.S)
    t = re.sub(r"`[^`\n]*`", " ", t)
    t = re.sub(r"(?m)^\s*>.*$", " ", t)
    return re.sub(r'"[^"\n]{0,400}"', '""', t)


def _technical(text: str, m) -> bool:
    s = max(text.rfind(c, 0, m.start()) for c in ".!?\n") + 1
    e = re.search(r"[.!?\n]", text[m.end():])
    sentence = text[s:m.end() + (e.start() if e else len(text))]
    return bool(TECHNICAL.search(sentence)) and not SAFETY_WORDS.search(sentence)


def is_decline(text: str) -> bool:
    """Whether a reply is the assistant's own explicit, first-person refusal of the request."""
    t = normalize(text).strip()
    if not t or INTERRUPTED.search(t):
        return False
    t = own_words(t).strip()
    hits = [m for m in DECLINE.finditer(t) if not _technical(t, m)]
    if not hits:
        return False
    if hits[0].start() < LEAD_CHARS or len(t) <= SHORT_REPLY:
        return True
    sentences = [s for s in re.split(r"(?<=[.!?])\s+|\n+", t) if s.strip()]
    refusing = sum(1 for s in sentences if any(not _technical(s, m) for m in DECLINE.finditer(s)))
    return bool(sentences) and refusing / len(sentences) >= DOMINANT_SHARE


def admits_block(reply: str) -> bool:
    """A reply that leaves a blocked action undone: it says so, or declines."""
    t = normalize(reply)
    return bool(NOT_RUN.search(t) or is_decline(t)
                or re.search(r"\btimed? ?out\b|\bblocked\b|\bdenied\b|\bapprov|\bconfirm", t, re.I))


def holdback_held(text: str) -> bool:
    """Whether text is a Holdback denial (tool result or Claude reporting it)."""
    return bool(HOLDBACK_HELD.search(normalize(text or "")))


def about_handoff(text: str) -> bool:
    """Whether the person's own message is about the handoff tool itself ("why didn't the
    handoff fire?"). Such a turn is a conversation about the tool, not a task to hand off.

    Pathnames like /tmp/claude-handoff-test-delete-me.txt must not match: require that
    "claude-handoff" is not a path segment (no / before it) and not a longer hyphenated
    filename (no -word after it).
    """
    t = normalize(text).lower()
    return bool(re.search(
        r"(?<![/\w.-])claude[- ]handoff\b(?!-[\w])"
        r"|\bhand ?off (?:hook|tool|provider|config)\b"
        r"|\b(?:the|this|that) hand ?off\b"
        r"|\bhandback\b",
        t,
    ))


def matches_any(text: str, patterns) -> bool:
    for p in patterns or []:
        try:
            if re.search(p, text or "", re.I):
                return True
        except re.error:
            continue
    return False
