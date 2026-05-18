"""
editorial.py — shared editorial constants and validation for AI Espresso.

Source of truth for layout labels, voice rotation, hook/copy gates, and
story-selection guardrails. Policy strings here are imported by the agent,
loop, and renderer; do not duplicate them elsewhere.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

from constitution import FAILURE_PRIMARY_RE

# ── Rotating masthead voice (deterministic per edition date) ──────────

VOICE_CHARACTERS: list[str] = [
    "Tony Stark",
    "Steve Rogers",
    "Natasha Romanoff",
    "Thor",
    "Bruce Banner",
    "Dumbledore",
    "Professor McGonagall",
    "Hermione Granger",
    "Professor Snape",
    "Jon Snow",
    "Tyrion Lannister",
    "Arya Stark",
    "Luke Skywalker",
    "Princess Leia",
    "Yoda",
    "Obi-Wan Kenobi",
    "Woody",
    "Buzz Lightyear",
    "Elsa",
    "Simba",
]

# Persona slot → orange category label on news cards (variant_c).
# These are editorial *audience* slots from the agent, NOT the "Try this prompt" tile.
# "beginner" = accessible to non-engineers — never use "TRY THIS" here (that tag is
# reserved for the fourth prompt card only).
SLOT_LABELS: dict[str, tuple[str, str]] = {
    "business": ("MARKET", "market"),
    "beginner": ("EVERYDAY", "everyday"),
    "engineer": ("BUILD", "build"),
    "cross": ("INDUSTRY", "industry"),
}

# ── Hook / preheader limits ───────────────────────────────────────────

HOOK_MAX_WORDS = 25
HOOK_MAX_CHARS = 140
HOOK_DANGLING_ENDINGS = frozenset(
    {
        "your", "the", "a", "an", "to", "for", "with", "and", "or", "in", "on", "at", "of", "by",
        "now", "is", "are", "was", "were", "has", "have", "can", "will", "just", "also", "so",
        "its", "their", "our", "that", "when", "who", "which",
    }
)

# ── Copy-style bans (REWRITE_SYSTEM constitution) ─────────────────────

COPY_BANNED_WORDS_RE = re.compile(
    r"\b("
    r"redefine|rewrite|unlock|empower|transform|leverage|strategic|"
    r"deployment|synergy|paradigm|playbook|landscape|ecosystem"
    r")\b",
    re.I,
)

# Narrow failure-as-primary backstop (shared with constitution.py ship gate).
HEADLINE_HARD_SKIP_RE = FAILURE_PRIMARY_RE

# Minimum article HTML text after a successful article fetch.
MIN_VERIFIED_BODY_CHARS = 120
# Paywalled Tier-1 RSS summaries are shorter (e.g. WSJ ~115 chars) but trusted.
MIN_RSS_SUMMARY_CHARS = 80


def voice_for_date(date_iso: str) -> str:
    """Pick today's character voice from day-of-year (stable per date)."""
    d = dt.date.fromisoformat(date_iso)
    return VOICE_CHARACTERS[d.timetuple().tm_yday % len(VOICE_CHARACTERS)]


def slot_label(slot: str) -> tuple[str, str]:
    """Return (display label, css class suffix) for a persona slot."""
    return SLOT_LABELS.get((slot or "").lower(), ("NEWS", "news"))


def _count_words(text: str) -> int:
    return len(re.findall(r"\b[\w']+\b", text or ""))


def _beats_complete(hook: str) -> list[str]:
    """Each period-separated clause should read as a complete thought."""
    reasons: list[str] = []
    for clause in re.split(r"\.\s+", hook.strip()):
        clause = clause.strip().rstrip(".")
        if not clause:
            continue
        last = clause.split()[-1].lower().rstrip("—-")
        if last in HOOK_DANGLING_ENDINGS:
            reasons.append(f"truncated clause ends on '{last}': {clause!r}")
    return reasons


def validate_hook(hook: str) -> list[str]:
    reasons: list[str] = []
    hook = (hook or "").strip()
    if not hook:
        return ["hook is empty"]
    if _count_words(hook) > HOOK_MAX_WORDS:
        reasons.append(f"hook too long ({_count_words(hook)} words; max {HOOK_MAX_WORDS})")
    if len(hook) > HOOK_MAX_CHARS:
        reasons.append(f"hook too long ({len(hook)} chars; max {HOOK_MAX_CHARS})")
    reasons.extend(_beats_complete(hook))
    return reasons


def candidate_has_verified_body(candidate: dict[str, Any]) -> bool:
    body = (candidate.get("body") or "").strip()
    if len(body) >= MIN_VERIFIED_BODY_CHARS:
        return True
    if candidate.get("body_source") == "rss_summary" and len(body) >= MIN_RSS_SUMMARY_CHARS:
        return True
    return False


def validate_pick_has_body(candidate: dict[str, Any]) -> list[str]:
    """Process gate: Editor must read_candidate before pick/ship (not a topic filter)."""
    if candidate_has_verified_body(candidate):
        return []
    if candidate.get("paywall"):
        return [
            "no verified text for this paywalled story — call read_candidate "
            "(uses RSS summary when the article URL is blocked)"
        ]
    return [
        "no verified article body — call read_candidate until fetch succeeds, or pick another story"
    ]


def validate_story_copy(story: dict[str, Any]) -> list[str]:
    """Check rewritten story fields against COPY_STYLE bans."""
    reasons: list[str] = []
    blob = " ".join(
        str(story.get(k) or "")
        for k in ("headline", "blurb", "why_it_matters", "original_headline")
    )
    m = COPY_BANNED_WORDS_RE.search(blob)
    if m:
        reasons.append(f"banned copy word: {m.group(0)}")
    hl = story.get("headline") or story.get("original_headline") or ""
    if HEADLINE_HARD_SKIP_RE.search(hl):
        reasons.append("headline uses failure-as-primary frame (constitution backstop)")
    return reasons


def validate_edition_stories(stories: list[dict[str, Any]]) -> list[str]:
    """Aggregate validation failures for an edition's story list."""
    reasons: list[str] = []
    for s in stories:
        slot = s.get("slot", "?")
        for r in validate_story_copy(s):
            reasons.append(f"[{slot}] {r}")
    return reasons


# ── Try-this-prompt card (fourth tile) ────────────────────────────────
# Generated fresh each edition via prompt_tile.py (not tied to daily stories).

PROMPT_TILE_STYLE_EXAMPLES = [
    {
        "title": "The skeptical reviewer",
        "prompt": (
            "You're a skeptical staff engineer who's seen this movie before. "
            "I'm about to describe an idea in 3–6 sentences. Don't encourage me. "
            "Find the weakest assumption, the part that only works in a demo, and the question "
            "I'm avoiding. End with one sentence I can actually ship this week anyway."
        ),
        "tool_hint": "When you're excited about something and need friction, not validation.",
    },
    {
        "title": "The elevator betrayal",
        "prompt": (
            "I have 90 seconds before a VP stops pretending to listen. Turn my messy explanation "
            "below into: one hook sentence, three concrete nouns, and a closing line that sounds "
            "like a decision already made — not a request for more meetings."
        ),
        "tool_hint": "Rehearse out loud before the real call.",
    },
    {
        "title": "The meeting that shouldn't exist",
        "prompt": (
            "Read what I'm about to send. If this could be a two-line Slack message, write those "
            "two lines. If it actually needs a meeting, give me a 4-line agenda where the first "
            "line is the decision we're making at the end."
        ),
        "tool_hint": "Before you hit Send on the calendar invite.",
    },
]

PROMPT_TILE_SYSTEM = (
    "You write the daily 'Try this prompt' for AI Espresso — Solvd's internal AI brief. "
    "Readers already use Claude/ChatGPT. Give one copy-paste prompt block that does a sharp, "
    "useful mental move in one sitting (friction, rehearsal, truth-check, tighten prose, kill a "
    "useless meeting). Warm, slightly irreverent colleague voice — never corporate filler. "
    "No profanity. No bracket placeholders like Paste [topic]. Respond with JSON only."
)

PROMPT_TILE_TEMPLATE = """\
Write one "Try this prompt" card for AI Espresso.

Do NOT tie the prompt to today's news, a product launch, or a specific industry.
Do NOT use bracket placeholders (no "Paste [X]", no "[topic]", no "[paste notes]").
Do NOT use profanity or insult the reader.

STYLE (match these examples — same voice, new idea each day):

Example A — title: "The skeptical reviewer"
prompt: You're a skeptical staff engineer who's seen this movie before. I'm about to describe an idea in 3–6 sentences. Don't encourage me. Find the weakest assumption, the part that only works in a demo, and the question I'm avoiding. End with one sentence I can actually ship this week anyway.
tool_hint: When you're excited about something and need friction, not validation.

Example B — title: "The elevator betrayal"
prompt: I have 90 seconds before a VP stops pretending to listen. Turn my messy explanation below into: one hook sentence, three concrete nouns, and a closing line that sounds like a decision already made — not a request for more meetings.
tool_hint: Rehearse out loud before the real call.

Example C — title: "The meeting that shouldn't exist"
prompt: Read what I'm about to send. If this could be a two-line Slack message, write those two lines. If it actually needs a meeting, give me a 4-line agenda where the first line is the decision we're making at the end.
tool_hint: Before you hit Send on the calendar invite.

BAR — never ship:
• "Explain … in plain English", generic pros/cons, "summarize this article"
• "Return:" followed by a long bullet laundry list
• Paste [anything] or any [square-bracket] input slot
• Teaching what AI is or how to prompt in general

REQUIREMENTS:
• title: starts with "The " — short memorable name (like the examples)
• prompt: single copy-paste block; second person or natural "I … below / I'm about to …";
  implies where the reader adds their text without [brackets]; about 30–55 words
• tool_hint: one line — when you'd actually use this (not which app to open)
• kicker: always empty string ""

Return JSON only:
{{ "title": "...", "kicker": "", "prompt": "...", "tool_hint": "..." }}
"""
