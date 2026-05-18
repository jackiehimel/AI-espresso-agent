"""
Editorial constitution for AI Espresso — narrow deterministic ship backstop.

Catches only empty copy, non-AI hooks, and obvious failure-as-primary frames.
Editorial judgment (sociology, PR, office openings, drama) lives in agent prompts.
"""

from __future__ import annotations

import re

# ── Lexicons (narrow backstop only) ───────────────────────────────────

AI_LEXICON_RE = re.compile(
    r"\b("
    r"AI|artificial intelligence|machine learning|ML\b|LLM|large language model|"
    r"ChatGPT|OpenAI|Claude|Anthropic|Gemini|Copilot|DeepMind|Meta AI|Mistral|"
    r"xAI|Grok|Cohere|model|models|agent|agents|diffusion|GPT|"
    r"Waymo|Codex|Perplexity|Midjourney|Stable Diffusion"
    r")\b",
    re.I,
)

# Obvious failure / ruin as the news hook (not broad negative valence).
FAILURE_PRIMARY_RE = re.compile(
    r"\b("
    r"AI slop|\bslop\b|"
    r"glitch|glitches|"
    r"trapped|become trapped|got stuck|"
    r"fails again|failed again|"
    r"refuses to help|refused to help"
    r")\b",
    re.I,
)


def _text(headline: str, blurb: str | None) -> str:
    parts = [headline or "", blurb or ""]
    return " ".join(p for p in parts if p).strip()


def is_ai_load_bearing(headline: str, blurb: str | None = None) -> bool:
    """True when AI is the news hook and failure is not the primary frame."""
    blob = _text(headline, blurb)
    if not blob:
        return False
    if FAILURE_PRIMARY_RE.search(blob):
        return False
    return bool(AI_LEXICON_RE.search(blob))


def is_celebration_tone(headline: str, blurb: str | None = None) -> bool:
    """True when the primary angle is not obvious AI failure / ruin."""
    blob = _text(headline, blurb)
    if not blob:
        return False
    return not FAILURE_PRIMARY_RE.search(blob)


def passes_constitution(headline: str, blurb: str | None = None) -> bool:
    return is_ai_load_bearing(headline, blurb) and is_celebration_tone(headline, blurb)


def constitution_violations(
    headline: str,
    blurb: str | None = None,
    *,
    source_name: str | None = None,
) -> list[str]:
    """Human-readable rejection reasons; empty list means pass."""
    reasons: list[str] = []
    blob = _text(headline, blurb)
    source_blob = (source_name or "").strip()
    source_implies_ai = bool(source_blob and AI_LEXICON_RE.search(source_blob))

    if not blob:
        return ["empty headline"]

    if FAILURE_PRIMARY_RE.search(blob):
        reasons.append("AI failure is the primary story frame, not the AI news hook")

    if not AI_LEXICON_RE.search(blob) and not source_implies_ai:
        reasons.append("AI is not load-bearing — headline works without an AI news hook")

    if not is_celebration_tone(headline, blurb) and not any("failure" in r for r in reasons):
        reasons.append("primary tone is AI failure/ruin, not celebration")

    seen: set[str] = set()
    out: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def constitution_prompt_block() -> str:
    """Short rubric injected into Scout/Editor/Critic system prompts."""
    return """
CONSTITUTION (ship gate enforces a narrow code backstop — do not contradict):
  • ACCEPT: AI is the news hook + celebration tone (capability, market move, partnership power-move).
  • REJECT in prompts: sociology/HBR, consultancy PR, office openings without a hook, legal drama;
    AI incidental (robot trapped, routing glitch); stories where failure is the news.
  • Code backstop only: empty headline, no AI lexicon, obvious failure-as-primary (slop, glitch, trapped).
  • Market rivalry and talent moves are OK. Critic judges borderline slates; ship gate catches egregious cases.
""".strip()
