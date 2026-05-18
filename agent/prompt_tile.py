"""
prompt_tile.py — daily "Try this prompt" generation for AI Espresso.

One LLM call per edition using PROMPT_TILE_TEMPLATE from editorial.py,
grounded in that day's picked stories (no rotating template library).
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

PROMPT_TILE_MAX_CHARS = 420
PROMPT_TILE_MIN_CHARS = 80
PROMPT_TILE_MAX_WORDS = 60
PROMPT_TILE_MIN_WORDS = 15
PROMPT_SIMILARITY_LOOKBACK = 14
PROMPT_SIMILARITY_THRESHOLD = 0.40

TASK_VERB_RE = re.compile(
    r"\b(review|draft|explain|compare|decide|rewrite|debug|help|turn|build|write|read|produce|flag)\b",
    re.I,
)
PLACEHOLDER_RE = re.compile(r"\[[^\]]+\]")


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
    if prompt.count("[") > 2:
        reasons.append("too many [placeholder] fields — use one clear input")
    if re.search(r"\*\*[^*]+\*\*", prompt):
        reasons.append("avoid **section** headers — use plain prose")
    if not PLACEHOLDER_RE.search(prompt):
        reasons.append("missing [placeholder] for user input")
    if not TASK_VERB_RE.search(prompt[:200]):
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
    kicker = (tile.get("kicker") or "").strip()
    if not kicker:
        reasons.append("kicker is empty")
    elif "paste" not in kicker.lower():
        reasons.append("kicker should say what to paste and which tool")
    elif len(kicker) > 120:
        reasons.append("kicker too long — keep to one short line")
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
    """Generate the fourth card from today's stories via one LLM call."""
    summaries = "\n".join(
        f"- [{s.slot}] {s.headline} — {s.why_it_matters}" for s in stories
    )
    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "kicker": {"type": "string"},
            "prompt": {"type": "string"},
            "tool_hint": {"type": "string"},
        },
        "required": ["title", "kicker", "prompt", "tool_hint"],
    }
    user = PROMPT_TILE_TEMPLATE.format(story_summaries=summaries)
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
                client, PROMPT_TILE_SYSTEM, user + correction, schema, max_tokens=2000,
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
