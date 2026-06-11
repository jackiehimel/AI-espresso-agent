"""
prompt_tile.py — daily "Try this prompt" generation for AI Espresso.

One LLM call per edition using PROMPT_TILE_TEMPLATE from editorial.py.
Snack-size, voice-forward prompts — not tied to that day's stories.
Falls back to a curated bank on LLM failure.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from editorial import PROMPT_TILE_SYSTEM, PROMPT_TILE_TEMPLATE

EDITIONS_DIR = Path(__file__).resolve().parent / "data" / "editions"

PROMPT_TILE_MAX_CHARS = 520
PROMPT_TILE_MIN_CHARS = 120
PROMPT_TILE_MAX_WORDS = 65
PROMPT_TILE_MIN_WORDS = 25
PROMPT_SIMILARITY_LOOKBACK = 14
PROMPT_SIMILARITY_THRESHOLD = 0.40

BRACKET_PLACEHOLDER_RE = re.compile(r"\[[^\]]+\]")
GENERIC_ARCHETYPE_RE = re.compile(
    r"explain .{0,40}plain english|pros and cons|eli5|paste \[|summarize (this|the) article",
    re.I,
)
BULLET_LAUNDRY_RE = re.compile(r"return:\s*(\d+\s+)?(bullet|bullets|items)", re.I)
PROFANITY_RE = re.compile(
    r"\b(fuck|shit|damn|hell|ass|bullshit|crap)\b",
    re.I,
)
TASK_VERB_RE = re.compile(
    r"\b("
    r"review|draft|explain|compare|decide|rewrite|debug|help|turn|build|write|read|"
    r"produce|flag|find|list|give|mark|tell|say|ship|pick|choose|imagine|pretend|"
    r"walk|show|think|describe|act"
    r")\b",
    re.I,
)

# ── Curated fallback bank ─────────────────────────────────────────────
# Used when LLM generation fails all retries. Each prompt is genuinely
# useful, covers a distinct category, and matches the AI Espresso voice.

FALLBACK_BANK: list[dict] = [
    {
        "title": "The second-opinion doctor",
        "kicker": "",
        "prompt": (
            "I'm stuck between two options and I keep going in circles. I'll describe both "
            "below. Don't ask clarifying questions. Pick one, commit to it like you'd bet your "
            "own money, then tell me the one scenario where you'd switch to the other."
        ),
        "tool_hint": "When analysis paralysis has eaten your whole afternoon.",
    },
    {
        "title": "The lazy genius",
        "kicker": "",
        "prompt": (
            "I have a tedious recurring task I'll describe below. Don't automate it — that's the "
            "obvious answer. Instead give me: one way to eliminate it entirely, one way to do it "
            "in a third of the time, and one argument for why it's secretly more valuable than I think."
        ),
        "tool_hint": "Before you spend a weekend automating a 10-minute chore.",
    },
    {
        "title": "The pre-mortem",
        "kicker": "",
        "prompt": (
            "I'll describe a project I'm about to start. Pretend it's six months from now and it "
            "failed spectacularly. Write me the post-mortem: what went wrong, which warning sign "
            "we ignored, and the one conversation we should have had this week instead."
        ),
        "tool_hint": "Run this before kickoff, not after the deadline.",
    },
    {
        "title": "The one-pager test",
        "kicker": "",
        "prompt": (
            "I'll describe something complex I'm working on below. Explain it back to me in "
            "exactly five sentences — no jargon, no hedge words, no 'it depends.' If you can't "
            "do it in five, tell me which part is too fuzzy for me to actually ship."
        ),
        "tool_hint": "Forces you to find out if you understand your own project.",
    },
    {
        "title": "The calendar audit",
        "kicker": "",
        "prompt": (
            "Look at the meeting descriptions I'll paste below. For each one, tell me: is this a "
            "decision, an update, or a ritual? Decisions get 25 minutes. Updates become a shared "
            "doc. Rituals get a hard question: what breaks if we stop?"
        ),
        "tool_hint": "Reclaim five hours this week without anyone noticing.",
    },
    {
        "title": "The hiring red flag detector",
        "kicker": "",
        "prompt": (
            "I'll paste a job description below. Find the three things a strong candidate would "
            "read and immediately close the tab. Then rewrite just those three parts so the "
            "posting sounds like a team that actually knows what it wants."
        ),
        "tool_hint": "Before you wonder why your pipeline is empty.",
    },
    {
        "title": "The difficult conversation script",
        "kicker": "",
        "prompt": (
            "I need to have a hard conversation I'll describe below. Write me the opening two "
            "sentences — direct, respectful, impossible to misread. Then give me the one question "
            "I should ask right after so they talk more than I do."
        ),
        "tool_hint": "Rehearse the first 30 seconds. The rest follows.",
    },
    {
        "title": "The scope knife",
        "kicker": "",
        "prompt": (
            "I'll describe a feature or project below. Cut it in half — not by removing quality, "
            "but by finding the version that proves whether the idea works in a week instead of "
            "a quarter. Tell me what you kept, what you cut, and why the cut parts can wait."
        ),
        "tool_hint": "When everything feels equally important and nothing ships.",
    },
    {
        "title": "The reverse interview",
        "kicker": "",
        "prompt": (
            "I'll describe my current role and what I'm working on below. Write me five questions "
            "I should be asking my manager but probably aren't — the ones that reveal whether this "
            "job is going somewhere or just keeping me busy."
        ),
        "tool_hint": "Before your next 1:1 turns into another status update.",
    },
    {
        "title": "The strategy smell test",
        "kicker": "",
        "prompt": (
            "I'll paste a strategy or plan below. Find every sentence that sounds like a decision "
            "but is actually a wish. Replace each one with the specific bet it's making and the "
            "thing we'd have to stop doing to make that bet real."
        ),
        "tool_hint": "When the strategy deck is 40 slides and zero trade-offs.",
    },
]


def recent_prompt_bodies(n: int = PROMPT_SIMILARITY_LOOKBACK) -> list[str]:
    bodies: list[str] = []
    paths = sorted(EDITIONS_DIR.glob("*.json"), reverse=True)
    for path in paths:
        if len(bodies) >= n:
            break
        try:
            data = json.loads(path.read_text())
            prompt = (data.get("try_this_prompt") or {}).get("prompt") or ""
            prompt = _normalize_prompt_for_compare(prompt)
            if prompt:
                bodies.append(prompt)
        except (json.JSONDecodeError, OSError):
            continue
    return bodies


def recent_prompt_titles(n: int = 7) -> list[str]:
    titles: list[str] = []
    paths = sorted(EDITIONS_DIR.glob("*.json"), reverse=True)
    for path in paths:
        if len(titles) >= n:
            break
        try:
            data = json.loads(path.read_text())
            tile = data.get("try_this_prompt") or {}
            title = (tile.get("title") or "").strip()
            if title and title != "Try this prompt":
                titles.append(title)
        except (json.JSONDecodeError, OSError):
            continue
    return titles


def _normalize_prompt_for_compare(text: str) -> str:
    text = (text or "").lower()
    text = re.sub(r"\[[^\]]+\]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _too_similar(prompt: str, recent: list[str]) -> bool:
    norm = _normalize_prompt_for_compare(prompt)
    if not norm:
        return False
    for old in recent:
        if SequenceMatcher(None, norm, old).ratio() >= PROMPT_SIMILARITY_THRESHOLD:
            return True
    return False


def _clean_prompt_text(prompt: str) -> str:
    lines = [ln.strip() for ln in prompt.splitlines() if ln.strip()]
    kept: list[str] = []
    for ln in lines:
        if re.match(r"^\s*\d+[\).]\s+", ln):
            ln = re.sub(r"^\s*\d+[\).]\s+", "", ln)
        if ln.lower().startswith(("give me:", "give me", "provide:")):
            continue
        kept.append(ln)
    return "\n".join(kept).strip() if kept and any(ln.startswith("**") for ln in kept) else re.sub(
        r"\s+", " ", " ".join(kept)
    ).strip()


def validate_prompt_tile(tile: dict, recent: list[str] | None = None) -> list[str]:
    prompt = _clean_prompt_text((tile.get("prompt") or "").strip())
    reasons: list[str] = []
    if not prompt:
        reasons.append("prompt is empty")
    if len(prompt) < PROMPT_TILE_MIN_CHARS:
        reasons.append(f"too short ({len(prompt)} chars; min {PROMPT_TILE_MIN_CHARS})")
    if len(prompt) > PROMPT_TILE_MAX_CHARS:
        reasons.append(f"too long ({len(prompt)} chars; max {PROMPT_TILE_MAX_CHARS})")
    word_count = len(prompt.split())
    if word_count < PROMPT_TILE_MIN_WORDS:
        reasons.append(f"too short ({word_count} words; min {PROMPT_TILE_MIN_WORDS})")
    if word_count > PROMPT_TILE_MAX_WORDS:
        reasons.append(f"too long ({word_count} words; max {PROMPT_TILE_MAX_WORDS})")
    if BRACKET_PLACEHOLDER_RE.search(prompt):
        reasons.append("no [bracket] placeholders — use natural cues like below or I'm about to")
    if GENERIC_ARCHETYPE_RE.search(prompt):
        reasons.append("generic beginner prompt (plain-English explainer, pros/cons, etc.)")
    if BULLET_LAUNDRY_RE.search(prompt):
        reasons.append('avoid "Return:" bullet laundry lists')
    if PROFANITY_RE.search(prompt):
        reasons.append("profanity not allowed")
    if re.search(r"\*\*[^*]+\*\*", prompt):
        reasons.append("avoid **section** headers — use plain prose")
    if not TASK_VERB_RE.search(prompt[:240]):
        reasons.append("no clear task verb in opening")
    if re.search(r"^\s*\d+[\).]\s", prompt, re.MULTILINE):
        reasons.append("contains numbered list")
    lower = prompt.lower()
    for bad in ("ai-generated", "ai generated", "deepfake", "slop", "human or machine"):
        if bad in lower:
            reasons.append(f"forbidden phrase: {bad}")
    title = (tile.get("title") or "").strip()
    if not title or len(title) < 8:
        reasons.append("title missing or too short")
    elif not title.lower().startswith("the "):
        reasons.append('title must be a named tool starting with "The "')
    tool_hint = (tile.get("tool_hint") or "").strip()
    if not tool_hint:
        reasons.append("tool_hint is empty")
    elif len(tool_hint) < 12:
        reasons.append("tool_hint too short")
    elif len(tool_hint) > 160:
        reasons.append("tool_hint too long")
    kicker = (tile.get("kicker") or "").strip()
    if kicker and len(kicker) > 120:
        reasons.append("kicker too long — leave empty or one short line")
    if recent is not None and _too_similar(prompt, recent):
        reasons.append("too similar to a recent edition prompt")
    return reasons


def normalize_prompt_tile(tile: dict) -> dict:
    prompt = _clean_prompt_text((tile.get("prompt") or "").strip())
    return {
        "title": (tile.get("title") or "Try this prompt").strip(),
        "kicker": (tile.get("kicker") or "").strip(),
        "prompt": prompt,
        "tool_hint": (tile.get("tool_hint") or "Works in Claude, ChatGPT, or Gemini.").strip(),
    }


def build_prompt_tile(client: Any, call_llm_json: Any, stories: list[Any]) -> dict:
    """Generate the fourth card via one LLM call (stories unused; kept for call-site compat)."""
    del stories
    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "kicker": {"type": "string"},
            "prompt": {"type": "string"},
            "tool_hint": {"type": "string"},
        },
        "required": ["title", "prompt", "tool_hint"],
    }
    recent = recent_prompt_bodies()
    titles = recent_prompt_titles()
    recent_block = ""
    if titles:
        recent_block = (
            "RECENT TITLES (do NOT repeat these topics or titles):\n"
            + "\n".join(f"• {t}" for t in titles)
        )
    template = PROMPT_TILE_TEMPLATE.replace("{recent_block}", recent_block)
    last_reasons: list[str] = []

    for attempt in range(3):
        try:
            correction = (
                "\n\nREJECTED — fix:\n" + "\n".join(f"  • {r}" for r in last_reasons)
                if last_reasons
                else ""
            )
            raw = call_llm_json(
                client,
                PROMPT_TILE_SYSTEM,
                template + correction,
                schema,
                max_tokens=2000,
            )
            tile = normalize_prompt_tile(raw)
            last_reasons = validate_prompt_tile(tile, recent)
            if not last_reasons:
                print(
                    f"  ✓ prompt tile ({len(tile['prompt'])} chars): {tile['title'][:50]}",
                    file=sys.stderr,
                )
                return tile
            print(f"  ! prompt tile attempt {attempt + 1}: {last_reasons}", file=sys.stderr)
        except Exception as e:
            print(f"  ! prompt tile attempt {attempt + 1}: {e}", file=sys.stderr)
            break

    fallback = _pick_fallback(recent)
    print(
        f"  ! prompt tile LLM failed — using curated fallback: {fallback['title']}",
        file=sys.stderr,
    )
    return fallback


def _pick_fallback(recent: list[str]) -> dict:
    """Pick a curated prompt that isn't too similar to recent editions."""
    for tile in FALLBACK_BANK:
        if not _too_similar(tile["prompt"], recent):
            return dict(tile)
    # All similar (unlikely with 10 in the bank) — deterministic rotation by day
    import datetime as dt
    idx = dt.date.today().timetuple().tm_yday % len(FALLBACK_BANK)
    return dict(FALLBACK_BANK[idx])
