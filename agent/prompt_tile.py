"""
prompt_tile.py — daily "Try this prompt" generation for AI Espresso.

One LLM call per edition using PROMPT_TILE_TEMPLATE from editorial.py.
Snack-size, voice-forward prompts — not tied to that day's stories.
"""

from __future__ import annotations

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
INPUT_CUE_RE = re.compile(
    r"\b("
    r"below|what i wrote|what i'm about to|i'm about to|my messy|"
    r"i have \d+ seconds|in \d+[–-]\d+ sentences|in \d+ sentences|"
    r"read this|look at|here'?s|take this|paste|the following|"
    r"what follows|this draft|my draft|my notes|what i just"
    r")\b",
    re.I,
)
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
    r"produce|flag|find|list|give|mark|tell|say|ship"
    r")\b",
    re.I,
)


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
    if not INPUT_CUE_RE.search(prompt):
        reasons.append("missing natural input cue (e.g. below, I'm about to, what I wrote)")
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
                PROMPT_TILE_TEMPLATE + correction,
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

    print("  ! prompt tile generation failed — using minimal fallback", file=sys.stderr)
    return {
        "title": "Try this prompt",
        "kicker": "",
        "prompt": "(prompt generation failed — re-run the edition agent)",
        "tool_hint": "Works in Claude, ChatGPT, or Gemini.",
    }
