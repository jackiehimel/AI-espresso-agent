"""
Editorial constitution for AI Espresso — deterministic backstop for Scout/Editor/Critic.

Venu alignment: celebrate AI, "wow really?", AI must be the news hook (not incidental).
See docs/superpowers/specs/2026-05-18-ai-espresso-venu-alignment-design.md
"""

from __future__ import annotations

import re
from typing import Pattern

# ── Lexicons ──────────────────────────────────────────────────────────

AI_LEXICON_RE = re.compile(
    r"\b("
    r"AI|artificial intelligence|machine learning|ML\b|LLM|large language model|"
    r"ChatGPT|OpenAI|Claude|Anthropic|Gemini|Copilot|DeepMind|Meta AI|Mistral|"
    r"xAI|Grok|Cohere|model|models|agent|agents|diffusion|GPT|"
    r"Waymo|Codex|Perplexity|Midjourney|Stable Diffusion"
    r")\b",
    re.I,
)

# Primary story frame is mechanical/AI failure (tokens may be present).
FAILURE_PRIMARY_RE = re.compile(
    r"\b("
    r"glitch|glitches|trapped|stuck|cul-de-sac|fails again|failed again|"
    r"refuses to help|refused to help|breaking workflow|breaks workflow|"
    r"never-ending|AI slop|\bslop\b|ruined|ruins|hallucination ruined|"
    r"strains corporate|bug bounty spam|safety controls are not|"
    r"navigation system fail|system fails again|become trapped|got stuck"
    r")\b",
    re.I,
)

# AV / routing incidents where AI is not the news hook.
INCIDENTAL_FAILURE_RE = re.compile(
    r"(driverless cars become|cars become trapped|become trapped in|"
    r"trapped in atlanta|trapped in a suburb)",
    re.I,
)

# Corporate / expansion without an AI-news hook.
NO_HOOK_RE = re.compile(
    r"\b(opens?\s+(a\s+)?(new\s+)?office|office expansion|"
    r"announces?\s+office|headquarters in|opens?\s+singapore)\b",
    re.I,
)

# Workforce sociology, consultancy PR, doom (overlap editorial.py).
HARD_REJECT_PATTERNS: list[Pattern[str]] = [
    re.compile(r"\b(strategic deployment|expanded rollout|3 practices|habits for AI teams)\b", re.I),
    re.compile(r"\b(reshaping how companies hire|generational labor|workforce trends)\b", re.I),
    re.compile(r"\bPwC expands\b", re.I),
    INCIDENTAL_FAILURE_RE,
    re.compile(r"\bAI slop\b", re.I),
    re.compile(
        r"(musk|elon).*(trial|lawsuit|court)|(trial|lawsuit).*(musk|openai|altman)|"
        r"founder\s+breakup|nonprofit\s+trial",
        re.I,
    ),
]

# AI failure / ruin tone — not broad negative valence (market rivalry OK).
AI_FAILURE_TONE_RE = re.compile(
    r"\b("
    r"AI slop|\bslop\b|ruined|ruins|trapped|glitch|glitches|"
    r"refuses to help|breaking workflow|fails again|failed again|"
    r"safety controls are not|hallucination ruined|"
    r"strains corporate hacking|bug bounty"
    r")\b",
    re.I,
)

LAYOFF_SCARE_RE = re.compile(
    r"\b(AI is coming for your job|job displacement|mass layoffs? due to AI)\b",
    re.I,
)


def _text(headline: str, blurb: str | None) -> str:
    parts = [headline or "", blurb or ""]
    return " ".join(p for p in parts if p).strip()


def is_ai_load_bearing(headline: str, blurb: str | None = None) -> bool:
    """True when AI is the reason the story exists (news hook), not incidental."""
    blob = _text(headline, blurb)
    if not blob:
        return False

    for pat in HARD_REJECT_PATTERNS:
        if pat.search(blob):
            return False

    if FAILURE_PRIMARY_RE.search(blob) or INCIDENTAL_FAILURE_RE.search(blob):
        return False

    if not AI_LEXICON_RE.search(blob):
        return False

    if NO_HOOK_RE.search(blob):
        return False

    return True


def is_celebration_tone(headline: str, blurb: str | None = None) -> bool:
    """True when primary angle celebrates AI capability/market moves — not AI failure/ruin."""
    blob = _text(headline, blurb)
    if not blob:
        return False

    if AI_FAILURE_TONE_RE.search(blob):
        return False
    if LAYOFF_SCARE_RE.search(blob):
        return False
    for pat in HARD_REJECT_PATTERNS:
        if pat.search(blob) and pat is not INCIDENTAL_FAILURE_RE:
            if pat.pattern.startswith(r"\b(strategic"):
                return False
    if re.search(r"\b(strategic deployment|reshaping how companies hire)\b", blob, re.I):
        return False

    return True


def passes_constitution(headline: str, blurb: str | None = None) -> bool:
    return is_ai_load_bearing(headline, blurb) and is_celebration_tone(headline, blurb)


def constitution_violations(
    headline: str,
    blurb: str | None = None,
    *,
    source_name: str | None = None,
) -> list[str]:
    """Human-readable rejection reasons; empty list means pass."""
    del source_name  # reserved; no source-based carve-out
    reasons: list[str] = []
    blob = _text(headline, blurb)

    if not blob:
        return ["empty headline"]

    for pat in HARD_REJECT_PATTERNS:
        if pat.search(blob):
            reasons.append("matches hard-reject pattern (sociology, PR, or incidental failure)")
            break

    if not is_ai_load_bearing(headline, blurb):
        if FAILURE_PRIMARY_RE.search(blob) or INCIDENTAL_FAILURE_RE.search(blob):
            reasons.append("AI failure is the primary story frame, not the AI news hook")
        elif not AI_LEXICON_RE.search(blob):
            reasons.append("AI is not load-bearing — headline works without an AI news hook")
        elif NO_HOOK_RE.search(blob):
            reasons.append("no AI news hook (corporate/expansion filler)")
        else:
            reasons.append("not AI load-bearing")

    if not is_celebration_tone(headline, blurb):
        if reasons and "celebration" not in " ".join(reasons):
            reasons.append("primary tone is AI failure/ruin, not celebration")
        elif not any("failure" in r or "celebration" in r for r in reasons):
            reasons.append("primary tone is AI failure/ruin, not celebration")

    # Deduplicate while preserving order
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
CONSTITUTION (ship gate enforces this in code — do not contradict):
  • ACCEPT: AI is the news hook + celebration tone (capability, market move, partnership power-move).
  • REJECT: AI failure/glitch/ruin as primary angle; AI incidental (robot trapped, routing glitch);
    sociology/HBR; consultancy PR; stories with AI tokens but failure is the news.
  • Market rivalry and talent moves are OK. Competitive "rivals teamed up" is OK.
  • Critic: approve borderline on-vibe slates; obvious failures → REVISE. Ship gate is the backstop.
""".strip()
