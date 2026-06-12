"""
AI Espresso — Hybrid Discovery Agent.

Pipeline:
  1. Rank ALL candidate headlines+blurbs in one LLM call (no lossy funnel)
  2. Pre-fetch article bodies for the top 20 candidates
  3. Agentic Editor loop: pick 4 or 6 stories using tools (pick, search_news,
     read_candidate, ship_edition). Deterministic validation gates only.
  4. Fallback: if budget exhausted, trim to an even count and force-ship.

No LLM Critic. No finalization contract. No forced convergence.
The Editor's judgment is final; deterministic gates catch structural issues.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from card_config import ALLOWED_STORY_COUNTS, largest_allowed_even_count
from constitution import constitution_prompt_block
from dedupe_gate import ArchiveIndex, exact_repeat_reason, semantic_repeats
from freshness import infer_date_from_text, infer_date_from_url

_CONSTITUTION_PROMPT = constitution_prompt_block()


# ───────────────────────────────────────────────────────────────────────
# Trace
# ───────────────────────────────────────────────────────────────────────

@dataclass
class TraceEvent:
    ts: float
    role: str
    kind: str
    name: str = ""
    args: dict | None = None
    result_summary: str = ""
    tool_use_id: str = ""


# ───────────────────────────────────────────────────────────────────────
# Editorial rubric (shared by ranker + editor)
# ───────────────────────────────────────────────────────────────────────

_EDITORIAL_RUBRIC = """\
AUDIENCE — any Solvd employee (engineer, consultant, sales, designer,
intern). Both a 'wow really?' architecture move and a practical
try-it-today feature count.

NORTH STAR — get people excited about AI. Every story should leave the
reader thinking "I want to try that" or "I didn't know AI could do that."

THE EDITORIAL TEST:
  Would any Solvd employee screenshot this and forward it because AI feels
  cool, useful, or surprising in a GOOD way? If no → reject / downweight.

SUBJECT-LINE TEST:
  Would they open this among 50 newsletters? Need a concrete noun + verb:
  what shipped, scaled, or became possible.

SHOW, DON'T TELL — headline must SHOW the news (concrete subject + action).
  Good: "ChatGPT can now look at your bank account"
  Bad: "PwC expands strategic Claude deployment"

HARD EXCLUSIONS (never pick):
  • True crime, predators, child safety, abuse
  • AI doomer / existential risk / extinction
  • Self-harm / suicide content involving AI
  • Mass surveillance / privacy horror as primary angle
  • Pure geopolitics where AI chips are just a prop

DOWNWEIGHT:
  • 'AI hallucination ruined X' cautionary tales
  • Pure layoffs / 'AI coming for your job' framing
  • Deepfake scandals (not detection-product launches)
  • HBR / McKinsey think pieces
  • Bare vendor launches with no news hook

PRIORITIZE (80+):
  • Shipped features people can try this week
  • Model drops with concrete capability hooks
  • Developer-facing changes with backlash or stakes
  • Platform wars with specific shipped artifacts
  • Surprising real-world AI applications

SOURCE PREFERENCE — when two candidates cover the same launch, prefer
the primary source. The primary is the company that shipped the thing.
Examples:
  Apple Newsroom > The Verge for Apple announcements
  Google AI Blog / blog.google > TechCrunch for Google launches
  Anthropic News > Wired for Anthropic releases
  Amazon News (aboutamazon.com) > TechCrunch for Amazon features
EXCEPTION — investigative reports (404 Media exposés, The Information
scoops, Platformer columns, Stratechery analysis) ARE primary; keep the
original source. Press coverage of an embargoed launch can also be
primary if the company hasn't posted yet.

CROSS-EDITION UNIQUENESS — same topic/vendor+product as last 30 days → reject.
"""

_RANKER_SYSTEM = (
    "You rank AI news candidates for AI Espresso, a daily internal brief "
    "at Solvd (~3,000 people). Score each candidate 0-100 on excitement, "
    "newsworthiness, and reader appeal. Return the top 20.\n\n"
    + _EDITORIAL_RUBRIC
    + _CONSTITUTION_PROMPT + "\n"
)

_EDITOR_SYSTEM = (
    "You are the Editor for AI Espresso. You have pre-ranked candidates "
    "with article bodies. Select exactly 4 or 6 stories for today's edition "
    "using the tools provided. Call ship_edition when ready.\n\n"
    "STRATEGY:\n"
    "  1. Review the ranked candidates (they already have bodies).\n"
    "  2. Pick 4 strong stories. Go to 6 only when the pool is genuinely "
    "strong enough for two more that clear the bar. Never ship 5.\n"
    "  3. If the pool feels thin, use search_news to find alternatives.\n"
    "  4. Call ship_edition. Deterministic gates will validate.\n"
    "  5. If ship fails, fix the issue and try again.\n\n"
    "RULES:\n"
    "  • At least 1 tier-1 source.\n"
    "  • At most 2 stories from the same vendor.\n"
    "  • All picks must have verified article bodies.\n"
    "  • Ship exactly 4 or 6 stories (never 3 or 5). Prefer 4.\n"
    "  • Assign a persona tag to each pick: market, everyday, build, or industry.\n"
    "    These are rendering labels, not constraints on selection.\n\n"
    + _EDITORIAL_RUBRIC
    + _CONSTITUTION_PROMPT + "\n"
)


# ───────────────────────────────────────────────────────────────────────
# LLM wrapper
# ───────────────────────────────────────────────────────────────────────

def _llm_json(system: str, prompt: str, schema: dict, max_tokens: int = 4000) -> Any:
    from espresso_agent import call_llm_json, USE_ANTHROPIC
    if not USE_ANTHROPIC:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    from anthropic import Anthropic
    return call_llm_json(Anthropic(), system, prompt, schema, max_tokens=max_tokens)


# ───────────────────────────────────────────────────────────────────────
# Step 1: Rank all headlines + blurbs in one call
# ───────────────────────────────────────────────────────────────────────

def rank_headlines(
    candidates_payload: list[dict],
    archive_headlines: list[str],
    today: dt.date,
) -> dict:
    prompt = (
        f"Today is {today.isoformat()}.\n\n"
        f"Candidate pool ({len(candidates_payload)} stories):\n"
        f"{json.dumps(candidates_payload, indent=1)}\n\n"
        f"Already covered (last 30 days):\n"
        + "\n".join(f"- {h}" for h in archive_headlines[:20] or ["(none)"])
        + "\n\nScore each 0-100. Return the top 20 ranked by excitement, "
        "with persona tag (market/everyday/build/industry) and 1-line reason."
    )
    schema = {
        "type": "object",
        "properties": {
            "ranked": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "score": {"type": "integer"},
                        "persona": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "score", "persona"],
                },
            },
            "gaps": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["ranked"],
    }
    return _llm_json(_RANKER_SYSTEM, prompt, schema, max_tokens=8000)


# ───────────────────────────────────────────────────────────────────────
# Step 2: Pre-fetch bodies (parallel HTTP, no LLM cost)
# ───────────────────────────────────────────────────────────────────────

def prefetch_bodies(ranked_entries: list[dict], cand_by_id: dict) -> list[dict]:
    from espresso_agent import fetch_url
    from bs4 import BeautifulSoup
    from editorial import MIN_VERIFIED_BODY_CHARS, MIN_RSS_SUMMARY_CHARS

    enriched = []
    for entry in ranked_entries[:20]:
        cid = entry["id"]
        c = cand_by_id.get(cid)
        if c is None:
            continue

        url = getattr(c, "url", "") or entry.get("url", "")
        paywall = getattr(c, "paywall", False)
        prestige = paywall

        body = ""
        body_source = ""
        html = fetch_url(url, use_cache=True, prestige=prestige) if url else None
        if html:
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
                tag.decompose()
            text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
            if len(text) >= MIN_VERIFIED_BODY_CHARS:
                body = text[:2500]
                body_source = "article"

        rss_summary = (getattr(c, "blurb", "") or "").strip()
        if not body and paywall and len(rss_summary) >= MIN_RSS_SUMMARY_CHARS:
            body = rss_summary[:2500]
            body_source = "rss_summary"

        row = dict(entry)
        row["headline"] = getattr(c, "headline", entry.get("headline", ""))
        row["source"] = getattr(c, "source_name", entry.get("source", ""))
        row["url"] = url
        row["tier"] = getattr(c, "tier", entry.get("tier", 2))
        row["blurb"] = rss_summary
        row["paywall"] = paywall
        row["vertical"] = getattr(c, "vertical", None)
        row["published_date"] = getattr(c, "published_date", None)
        row["body"] = body
        row["body_source"] = body_source

        if body:
            enriched.append(row)
            print(f"    body ok ({len(body)} chars): {row['headline'][:60]}", file=sys.stderr)
        else:
            print(f"    body fail: {row['headline'][:60]}", file=sys.stderr)

    print(f"  [prefetch] {len(enriched)}/{len(ranked_entries[:20])} bodies fetched", file=sys.stderr)
    return enriched


# ───────────────────────────────────────────────────────────────────────
# Step 3: Agentic Editor loop
# ───────────────────────────────────────────────────────────────────────

@dataclass
class AgentState:
    today: dt.date
    candidates: list[dict]
    archive_headlines: list[str]
    archive_index: ArchiveIndex | None = None
    picks: dict[str, dict] = field(default_factory=dict)
    vendor_counts: dict[str, int] = field(default_factory=dict)
    extra_candidates: list[dict] = field(default_factory=list)
    next_id: int = 10_000
    tool_calls: int = 0
    hard_budget: int = 20
    shipped: bool = False
    trace: list[TraceEvent] = field(default_factory=list)
    search_calls_used: int = 0


MIN_PICKS = 3
MAX_PICKS = 6
MAX_SEARCH_CALLS = 3


def _detect_vendor(headline: str, url: str, vendor_patterns) -> str | None:
    hay = f" {headline.lower()} {urlparse(url).netloc.lower()} "
    for vendor, needles in vendor_patterns:
        for n in needles:
            if n in hay:
                return vendor
    return None


def _tool_pick(state: AgentState, args: dict, vendor_patterns) -> dict:
    cid = args.get("id")
    reason = args.get("reason", "")
    persona = args.get("persona", "market")

    if cid is None:
        return {"error": "missing id"}
    if len(state.picks) >= MAX_PICKS:
        return {"error": f"already have {MAX_PICKS} picks; call ship_edition"}

    found = next((c for c in state.candidates + state.extra_candidates if c.get("id") == int(cid)), None)
    if not found:
        return {"error": f"no candidate with id={cid}"}

    for slot, existing in state.picks.items():
        if int(existing.get("id", -1)) == int(cid):
            return {"error": "already picked this candidate"}

    body = (found.get("body") or "").strip()
    if len(body) < 80:
        return {"error": "no verified article body; call read_candidate first or pick another"}

    repeat = exact_repeat_reason(found.get("headline", ""), found.get("url", ""), state.archive_index)
    if repeat:
        return {"error": f"{repeat}; pick different news"}

    nv = _detect_vendor(found.get("headline", ""), found.get("url", ""), vendor_patterns)
    if nv and state.vendor_counts.get(nv, 0) >= 2:
        return {"error": f"vendor cap: {nv} already has 2 stories"}

    slot = f"pick_{len(state.picks) + 1}"
    found = dict(found)
    found["pick_reason"] = reason
    found["persona"] = persona
    state.picks[slot] = found
    if nv:
        state.vendor_counts[nv] = state.vendor_counts.get(nv, 0) + 1

    return {
        "ok": True,
        "picked": found.get("headline", ""),
        "persona": persona,
        "pick_count": len(state.picks),
    }


def _tool_unpick(state: AgentState, args: dict, vendor_patterns) -> dict:
    cid = args.get("id")
    if cid is None:
        return {"error": "missing id"}
    for slot, pick in list(state.picks.items()):
        if int(pick.get("id", -1)) == int(cid):
            state.picks.pop(slot)
            nv = _detect_vendor(pick.get("headline", ""), pick.get("url", ""), vendor_patterns)
            if nv and state.vendor_counts.get(nv, 0) > 0:
                state.vendor_counts[nv] -= 1
            return {"ok": True, "removed": pick.get("headline", "")}
    return {"error": f"no pick with id={cid}"}


def _trim_picks_to_even(state: AgentState, vendor_patterns) -> list[str]:
    """Drop the weakest picks until the count is supported (6, 4, or a thin 3).

    Deterministic ship backstop for the budget-exhausted path: an odd pick set is
    trimmed to ``largest_allowed_even_count`` so an odd edition can never ship.
    Non-tier-1, lowest-ranked picks are dropped first so the tier-1 rail
    survives. Returns the headlines removed (empty if no trim was needed).
    """
    target = largest_allowed_even_count(len(state.picks))
    if target >= len(state.picks):
        return []
    # Most expendable first: non-tier-1 before tier-1, then lowest editorial
    # score (search_news picks default to 0).
    ordered = sorted(
        state.picks.items(),
        key=lambda kv: (int(kv[1].get("tier", 99)) == 1, kv[1].get("score", 0)),
    )
    removed = []
    for slot, pick in ordered[: len(state.picks) - target]:
        state.picks.pop(slot, None)
        removed.append(pick.get("headline", ""))
        nv = _detect_vendor(
            pick.get("headline", ""), pick.get("url", ""), vendor_patterns
        )
        if nv and state.vendor_counts.get(nv, 0) > 0:
            state.vendor_counts[nv] -= 1
    return removed


def _tool_read_candidate(state: AgentState, args: dict) -> dict:
    cid = int(args.get("id", -1))
    found = next((c for c in state.candidates + state.extra_candidates if c.get("id") == cid), None)
    if not found:
        return {"error": f"no candidate with id={cid}"}
    if found.get("body"):
        return {"candidate": found, "cached": True}

    from espresso_agent import fetch_url
    from editorial import MIN_VERIFIED_BODY_CHARS, MIN_RSS_SUMMARY_CHARS

    url = found.get("url", "")
    paywall = bool(found.get("paywall"))
    html = fetch_url(url, use_cache=True, prestige=paywall) if url else None

    body = ""
    body_source = ""
    if html:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
        if len(text) >= MIN_VERIFIED_BODY_CHARS:
            body = text[:2500]
            body_source = "article"

    rss_summary = (found.get("blurb") or "").strip()
    if not body and paywall and len(rss_summary) >= MIN_RSS_SUMMARY_CHARS:
        body = rss_summary[:2500]
        body_source = "rss_summary"

    if body:
        found["body"] = body
        found["body_source"] = body_source
        for pool in (state.candidates, state.extra_candidates):
            for i, c in enumerate(pool):
                if c.get("id") == cid:
                    pool[i] = found
                    break
    return {"candidate": found, "body_chars": len(body), "body_source": body_source or "none"}


def _tool_search_news(state: AgentState, args: dict) -> dict:
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "missing query"}
    if state.search_calls_used >= MAX_SEARCH_CALLS:
        return {"error": f"search limit reached (max {MAX_SEARCH_CALLS})"}

    from espresso_agent import search_allowed_domains, is_search_domain_allowed

    allowed = search_allowed_domains()
    hits, err = _perplexity_search(query)
    if hits is None:
        state.search_calls_used += 1
        return {"error": f"search failed: {err}"}

    state.search_calls_used += 1
    new_entries = []
    for hit in hits[:8]:
        url = hit.get("url", "")
        title = (hit.get("title") or "").strip()
        if not title or not url:
            continue
        if not is_search_domain_allowed(url, allowed):
            continue
        url_date = infer_date_from_url(url)
        entry = {
            "id": state.next_id,
            "headline": title,
            "url": url,
            "source": hit.get("domain", "web"),
            "tier": 2,
            "vertical": None,
            "via_search": True,
            "blurb": (hit.get("snippet") or "")[:200],
            "published_date": url_date.isoformat() if url_date else None,
        }
        state.next_id += 1
        state.extra_candidates.append(entry)
        new_entries.append(entry)
        if len(new_entries) >= 5:
            break
    return {"added": len(new_entries), "entries": new_entries}


def _perplexity_search(query: str) -> tuple[list[dict] | None, str | None]:
    from load_env import strip_wrapping_quotes
    api_key = strip_wrapping_quotes(
        os.environ.get("PERPLEXITY_API_KEY") or os.environ.get("PPLX_API_KEY") or ""
    )
    if not api_key:
        return None, "PERPLEXITY_API_KEY not set"
    try:
        api_key.encode("ascii")
    except UnicodeEncodeError:
        return None, "API key contains non-ASCII"

    try:
        resp = httpx.post(
            "https://api.perplexity.ai/search",
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": query, "max_results": 12},
            timeout=45,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        return None, f"Perplexity API: {exc}"

    hits = []
    for row in data.get("results") or []:
        url = (row.get("url") or "").strip()
        title = (row.get("title") or "").strip()
        if not url or not title:
            continue
        domain = urlparse(url).netloc.removeprefix("www.")
        hits.append({"title": title, "url": url, "snippet": (row.get("snippet") or "")[:200], "domain": domain})
    return hits or None, "no results" if not hits else None


def _validate_ship(state: AgentState, enforce_even: bool = True) -> dict:
    errors = []
    n = len(state.picks)
    if n < MIN_PICKS:
        errors.append(f"need {MIN_PICKS}+ picks, have {n}")
    elif enforce_even and n not in ALLOWED_STORY_COUNTS:
        if n == 5:
            errors.append(
                "edition needs exactly 4 or 6 stories, have 5 — add a 6th "
                "strong story or unpick the weakest down to 4"
            )
        elif n == 3:
            errors.append(
                "edition needs exactly 4 or 6 stories, have 3 — add a 4th "
                "strong story"
            )
        else:
            errors.append(f"edition needs exactly 4 or 6 stories, have {n}")

    have_t1 = sum(1 for p in state.picks.values() if int(p.get("tier", 99)) == 1)
    if have_t1 < 1:
        errors.append(f"need 1+ tier-1 pick, have {have_t1}")

    for slot, pick in state.picks.items():
        body = (pick.get("body") or "").strip()
        if len(body) < 80:
            errors.append(f"[{slot}] no verified body")

    from constitution import constitution_violations
    non_load_bearing = 0
    for slot, pick in state.picks.items():
        for reason in constitution_violations(
            pick.get("headline", ""), pick.get("blurb"), source_name=pick.get("source"),
        ):
            if reason.startswith("AI is not load-bearing"):
                non_load_bearing += 1
                continue
            errors.append(f"[{slot}] constitution: {reason}")

    if non_load_bearing > 1:
        errors.append(f"too many non-load-bearing stories ({non_load_bearing} > 1)")

    # Cross-edition dedupe rails (see dedupe_gate.py).
    # Exact layers re-run here because search_news candidates bypass pool dedupe.
    for slot, pick in state.picks.items():
        repeat = exact_repeat_reason(
            pick.get("headline", ""), pick.get("url", ""), state.archive_index,
        )
        if repeat:
            errors.append(f"[{slot}] {repeat} — unpick and choose different news")

    semantic_archive = (
        state.archive_index.semantic_headlines
        if state.archive_index is not None
        else state.archive_headlines
    )
    if semantic_archive:
        slot_by_headline = {
            (p.get("headline") or "").strip(): slot for slot, p in state.picks.items()
        }
        hits = semantic_repeats(list(slot_by_headline), semantic_archive)
        if hits is None:
            print("  [dedupe] semantic ship gate skipped (unavailable)", file=sys.stderr)
        else:
            for hit in hits:
                slot = slot_by_headline.get(hit["pick"], "?")
                errors.append(
                    f"[{slot}] covers the same story as a recent edition "
                    f"(\"{hit['matched']}\", similarity {hit['similarity']}) — "
                    f"unpick and choose different news"
                )

    return {"ok": not errors, "errors": errors, "pick_count": len(state.picks)}


def _tool_ship(state: AgentState, args: dict) -> dict:
    gate = _validate_ship(state)
    if not gate["ok"]:
        return {"shipped": False, "errors": gate["errors"]}
    state.shipped = True
    state.trace.append(TraceEvent(
        ts=time.time(), role="editor", kind="finalize",
        result_summary=f"shipped {gate['pick_count']} stories",
    ))
    return {"shipped": True, "picks": {s: p["headline"] for s, p in state.picks.items()}}


EDITOR_TOOLS: list[dict] = [
    {
        "name": "pick",
        "description": "Select a candidate for the edition.",
        "input_schema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Candidate id"},
                "reason": {"type": "string", "description": "Why this story"},
                "persona": {"type": "string", "enum": ["market", "everyday", "build", "industry"],
                            "description": "Rendering label"},
            },
            "required": ["id", "reason", "persona"],
        },
    },
    {
        "name": "unpick",
        "description": "Remove a previously picked candidate by id.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "name": "read_candidate",
        "description": "Fetch article body for a candidate not in the pre-fetched set.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer"}},
            "required": ["id"],
        },
    },
    {
        "name": "search_news",
        "description": "Web search for stories not in the candidate pool (max 3 per edition).",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "ship_edition",
        "description": "Finalize the edition. Requires exactly 4 or 6 picks with verified bodies.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def _dispatch_tool(name: str, args: dict, state: AgentState, vendor_patterns) -> dict:
    if name == "pick":
        return _tool_pick(state, args, vendor_patterns)
    if name == "unpick":
        return _tool_unpick(state, args, vendor_patterns)
    if name == "read_candidate":
        return _tool_read_candidate(state, args)
    if name == "search_news":
        return _tool_search_news(state, args)
    if name == "ship_edition":
        return _tool_ship(state, args)
    return {"error": f"unknown tool {name!r}"}


def _build_editor_brief(state: AgentState, gaps: list[str]) -> str:
    lines = []
    for c in state.candidates[:20]:
        body_preview = (c.get("body") or "")[:200]
        lines.append(
            f"id={c['id']} score={c.get('score', '?')} [{c.get('persona', '?')}] "
            f"{c['headline']} ({c.get('source', '?')}, t{c.get('tier', '?')})\n"
            f"  blurb: {(c.get('blurb') or '')[:120]}\n"
            f"  body: {body_preview}{'...' if len(body_preview) >= 200 else ''}"
        )
    candidates_block = "\n".join(lines) if lines else "(empty)"

    return (
        f"Today is {state.today.isoformat()}.\n"
        f"Coverage gaps from ranking: {gaps or '(none)'}\n\n"
        f"CANDIDATES (pre-ranked, bodies pre-fetched):\n{candidates_block}\n\n"
        f"Archive (last 30d): {', '.join(state.archive_headlines[:10]) or '(none)'}\n\n"
        "Select exactly 4 or 6 stories using tools. Call ship_edition when ready."
    )


def _run_editor_loop(state: AgentState, vendor_patterns) -> bool:
    from espresso_agent import USE_ANTHROPIC, CLAUDE_MODEL
    if not USE_ANTHROPIC:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    from anthropic import Anthropic

    client = Anthropic()
    model = CLAUDE_MODEL
    messages: list[dict] = [{"role": "user", "content": _build_editor_brief(state, [])}]

    while state.tool_calls < state.hard_budget and not state.shipped:
        try:
            resp = client.messages.create(
                model=model, max_tokens=4000, system=_EDITOR_SYSTEM,
                messages=messages, tools=EDITOR_TOOLS,
            )
        except Exception as e:
            state.trace.append(TraceEvent(
                ts=time.time(), role="editor", kind="error",
                result_summary=f"LLM call failed: {e}",
            ))
            return False

        messages.append({"role": "assistant", "content": resp.content})
        tool_blocks = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        if not tool_blocks:
            if len(state.picks) >= MIN_PICKS:
                return _tool_ship(state, {}).get("shipped", False)
            state.trace.append(TraceEvent(
                ts=time.time(), role="editor", kind="error",
                result_summary="model ended turn without tool_use",
            ))
            return False

        tool_results = []
        for block in tool_blocks:
            state.tool_calls += 1
            name = block.name
            tool_input = block.input if isinstance(block.input, dict) else {}
            state.trace.append(TraceEvent(
                ts=time.time(), role="editor", kind="tool_call",
                name=name, args=tool_input, tool_use_id=block.id,
            ))
            result = _dispatch_tool(name, tool_input, state, vendor_patterns)
            result_str = json.dumps(result)[:600]
            state.trace.append(TraceEvent(
                ts=time.time(), role="editor", kind="tool_result",
                name=name, result_summary=result_str, tool_use_id=block.id,
            ))
            print(f"  [editor {state.tool_calls}] {name}({tool_input}) → {result_str[:120]}", file=sys.stderr)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_str,
            })
        messages.append({"role": "user", "content": tool_results})

    return state.shipped


# ───────────────────────────────────────────────────────────────────────
# Perplexity discovery (parallel to RSS fetch)
# ───────────────────────────────────────────────────────────────────────

def discover_via_search(today: dt.date) -> list[dict]:
    query = f"biggest AI news stories {today.isoformat()} artificial intelligence launches features"
    hits, err = _perplexity_search(query)
    if hits is None:
        print(f"  [discovery] Perplexity search failed: {err}", file=sys.stderr)
        return []
    print(f"  [discovery] Perplexity returned {len(hits)} results", file=sys.stderr)
    return hits


# ───────────────────────────────────────────────────────────────────────
# Public entry point
# ───────────────────────────────────────────────────────────────────────

class AgenticSelectFailed(Exception):
    def __init__(self, message: str, trace: list[dict], meta: dict):
        super().__init__(message)
        self.trace = trace
        self.meta = meta


def write_agent_failure_artifact(today: dt.date, trace: list[dict], meta: dict) -> Path:
    from espresso_agent import EDITIONS_DIR
    out = EDITIONS_DIR / f"{today.isoformat()}.failed.json"
    out.write_text(json.dumps({"date": today.isoformat(), "mode": "failed", "meta": meta, "agent_trace": trace}, indent=2))
    return out


def agentic_select(
    candidates: list,
    archive_fps: set[str],
    rules: dict,
    today: dt.date,
    vendor_patterns,
    archive_headlines: list[str],
    archive_index: ArchiveIndex | None = None,
) -> tuple[list, list[dict], dict]:
    # Dedupe: fingerprint + exact archive match (canonical URL / title)
    fresh = []
    seen_fps = set(archive_fps)
    for c in candidates:
        if c.fingerprint in seen_fps or c.aggregator:
            continue
        if exact_repeat_reason(c.headline, c.url, archive_index):
            continue
        seen_fps.add(c.fingerprint)
        fresh.append(c)

    # Build candidate payload — ALL candidates, no per-source cap
    candidates_payload = []
    cand_by_id: dict[int, Any] = {}
    for i, c in enumerate(fresh):
        cand_by_id[i] = c
        candidates_payload.append({
            "id": i,
            "headline": c.headline,
            "blurb": (c.blurb or "")[:200],
            "source": c.source_name,
            "tier": c.tier,
            "url": c.url,
            "vertical": c.vertical,
        })

    # Perplexity discovery — merge additional candidates
    from espresso_agent import (
        Candidate, fingerprint_of, search_allowed_domains, is_search_domain_allowed,
    )
    allowed_domains = search_allowed_domains()
    discovery_hits = discover_via_search(today)
    for hit in discovery_hits:
        url = hit.get("url", "")
        title = (hit.get("title") or "").strip()
        if not title or not url or not is_search_domain_allowed(url, allowed_domains):
            continue
        fp = fingerprint_of(title, url)
        if fp in seen_fps:
            continue
        if exact_repeat_reason(title, url, archive_index):
            continue
        seen_fps.add(fp)
        new_id = len(candidates_payload)
        cand_by_id[new_id] = Candidate(
            headline=title, url=url, source_name=hit.get("domain", "web"),
            tier=2, blurb=(hit.get("snippet") or "")[:200],
        )
        candidates_payload.append({
            "id": new_id,
            "headline": title,
            "blurb": (hit.get("snippet") or "")[:200],
            "source": hit.get("domain", "web"),
            "tier": 2,
            "url": url,
            "vertical": None,
        })

    print(f"  [ranking] {len(candidates_payload)} candidates (incl. {len(discovery_hits)} from search)", file=sys.stderr)

    # Step 1: Rank all headlines + blurbs
    ranking_result = rank_headlines(candidates_payload, archive_headlines, today)
    ranked = ranking_result.get("ranked", [])
    gaps = ranking_result.get("gaps", [])
    print(f"  [ranking] top 20 returned, gaps: {gaps}", file=sys.stderr)

    # Step 2: Pre-fetch bodies for top 20
    print("  [prefetch] fetching bodies for top 20...", file=sys.stderr)
    enriched = prefetch_bodies(ranked, cand_by_id)

    if len(enriched) < MIN_PICKS:
        raise AgenticSelectFailed(
            f"only {len(enriched)} candidates with bodies (need {MIN_PICKS})",
            [], {"editor_notes": "pre-fetch collapse — too few bodies available"},
        )

    # Hydrate enriched entries with ranking metadata
    for entry in enriched:
        rank_info = next((r for r in ranked if r["id"] == entry["id"]), {})
        entry["score"] = rank_info.get("score", 0)
        entry["persona"] = rank_info.get("persona", "market")

    # Step 3: Agentic Editor loop
    state = AgentState(
        today=today,
        candidates=enriched,
        archive_headlines=archive_headlines,
        archive_index=archive_index,
    )
    state.trace.append(TraceEvent(
        ts=time.time(), role="system", kind="handoff",
        result_summary=f"ranked={len(ranked)}, enriched={len(enriched)}, gaps={gaps}",
    ))

    ok = _run_editor_loop(state, vendor_patterns)

    # Fallback: if budget exhausted, trim to an even count and force-ship.
    # The even-count rail drives the agent; here we deterministically enforce it
    # (and tolerate a thin 3 rather than failing daily delivery).
    if not ok and len(state.picks) >= MIN_PICKS:
        removed = _trim_picks_to_even(state, vendor_patterns)
        if removed:
            state.trace.append(TraceEvent(
                ts=time.time(), role="system", kind="trim_to_even",
                result_summary=(
                    f"trimmed {len(removed)} weakest pick(s) to even count: {removed}"
                ),
            ))
        gate = _validate_ship(state, enforce_even=False)
        if gate["ok"]:
            state.shipped = True
            state.trace.append(TraceEvent(
                ts=time.time(), role="system", kind="fallback_ship",
                result_summary=f"budget exhausted, force-shipping {len(state.picks)} picks",
            ))
            ok = True

    trace_dicts = [asdict(ev) for ev in state.trace]
    meta = {
        "editor_notes": "",
        "working_memory": {},
        "shipped": state.shipped,
        "salvaged": not ok and state.shipped,
        "salvage_reason": "budget_exhausted_force_ship" if not ok and state.shipped else None,
    }

    if state.shipped:
        selected = []
        for slot, entry in state.picks.items():
            cid = entry["id"]
            if cid in cand_by_id:
                cand = cand_by_id[cid]
            else:
                cand = Candidate(
                    headline=entry["headline"], url=entry["url"],
                    source_name=entry.get("source", ""), tier=entry.get("tier", 2),
                )
            cand._agent_slot = entry.get("persona", "market")
            selected.append(cand)
        return selected, trace_dicts, meta

    raise AgenticSelectFailed(
        f"agent loop failed: shipped={ok}, picks={len(state.picks)} after {state.tool_calls} tool calls",
        trace_dicts, meta,
    )
