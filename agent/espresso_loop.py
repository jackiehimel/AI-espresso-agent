"""
AI Espresso — agentic editor (Scout bootstrap → native-tool Editor).

Scout runs once to shortlist candidates. The Editor then drives selection
via Anthropic native tool_use (pick, search_news, self_critique, etc.).
Python dispatches tools and enforces ship gates; the model decides when
to loop, search, critique, and call ship_edition.

On hard budget exhaustion with an approved slate, the caller salvages picks.
Genuine failure (no recoverable slate) raises AgenticSelectFailed. The caller
may fall back to rank_and_select only when ESPRESSO_ALLOW_DETERMINISTIC_FALLBACK=1
(local dev emergency — never set in CI / daily-edition.yml).
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

from constitution import constitution_prompt_block

_CONSTITUTION_PROMPT = constitution_prompt_block()


# ───────────────────────────────────────────────────────────────────────
# Trace event log (everything the agent does is recorded)
# ───────────────────────────────────────────────────────────────────────

@dataclass
class TraceEvent:
    ts: float
    role: str          # scout | editor | critic | system
    kind: str          # think | tool_call | tool_result | handoff | finalize | error
    name: str = ""     # tool name if kind == tool_call
    args: dict | None = None
    result_summary: str = ""
    thinking: str = ""
    tool_use_id: str = ""


# ───────────────────────────────────────────────────────────────────────
# System prompts — shared rubric + role-specific tails
# ───────────────────────────────────────────────────────────────────────

_EDITORIAL_RUBRIC = """\
AUDIENCE — any Solvd employee (engineer, consultant, sales, designer,
intern). We are not writing for "non-technical readers only." Different
roles react to different stories; both a 'wow really?' architecture move
and a practical try-it-today feature count.

NORTH STAR — get people excited about AI. Every story should leave the
reader thinking "I want to try that" or "I didn't know AI could do that."
Reject anything that makes AI feel scary, sad, or like homework. Do not
ship stories whose primary angle is AI failure, glitch, or ruin.

THE EDITORIAL TEST — apply to every candidate/pick:
  Would any Solvd employee screenshot this and forward it because AI feels
  cool, useful, or surprising in a GOOD way? If no → reject / downweight.

SUBJECT-LINE TEST:
  Would they open this among 50 newsletters without knowing the brand?
  If the only hook is "AI is changing X" or a bare vendor press-release
  title → reject. Need a concrete noun + verb: what shipped, scaled, or
  became possible.

SHOW, DON'T TELL — the story must support a headline that SHOWS the news
(concrete subject + action), not one that TELLS the reader what to think:
  Good:
    "Meta's smart glasses just became a real wearable computer"
    "ChatGPT can now look at your bank account"
    "Claude Code can now run itself while your laptop is closed"
    "OpenAI just shipped a coding agent straight to your phone"
    "Cerebras stock jumps 89% on debut as AI chip maker goes public"
    "CFTC runs ML models to flag suspicious bets on Polymarket"
    "Anthropic just entered Elon Musk's entire colossus cluster"
    "OpenAI's models can think while they talk"
    "Anthropic and BlackRock partner on AI for asset management"
    "YouTube now lets anyone flag AI deepfakes of themselves"
  Bad (even from a prestigious outlet):
    "PwC expands strategic Claude deployment across client pipeline"
    "HBR: 3 practices teams can use to adopt AI"
    "AI is reshaping how companies hire"
    "Deepfake scandal rocks [celebrity]" / fear-only deepfake panic
  Why the bad examples fail: press releases, think pieces, or fear hooks —
  words like "strategic", "deployment", "reshaping", "practices" — and they
  don't make anyone curious.

FRAMING TEST — AI is the subject doing things in the world:
  Accept capabilities, launches, partnerships, market moves, and even AI
  making mistakes when framing is neutral or curious. Reject AI-as-villain
  (ruining, destroying, threatening, harming). Reject AI-as-incidental:
  enforcement where AI is just the tool, stock moves where AI is only the
  sector angle, routing glitches, robots trapped in traffic.
  The test: is AI the subject of the headline doing something interesting?

LAB PARTNERSHIPS & MARKET MOVES — valid WITH a recognizable hook:
  Lab partnerships, infrastructure deals, pricing/access announcements, and
  market moves from frontier players (Anthropic, OpenAI, DeepMind, Meta,
  xAI, Mistral, Cohere) are "what the hell is happening" news when they
  pass the 'wow really?' test — not because of category alone.
  Bare product launches, generic pricing news, consultancy partnerships, and
  "Anthropic raised Series E" with no surprise still fail.
  "Anthropic got access to Musk's 220K GPUs" passes. Press-release titles
  from Tier 1 are OK if the body has a real hook you can name in one phrase.

WORKFORCE SOCIOLOGY & HBR — reject:
  • HBR / Sloan / McKinsey-style think pieces ("3 habits for AI teams")
  • Labor-market macro without a product hook (generational hiring, workforce
    trends, "AI is reshaping how companies hire")
  • 'X firms are now using AI' survey filler

DEEPFAKE — distinguish scandal from product:
  • REJECT / downweight: deepfake scandal panics (celebrity impersonation crime,
    political deepfake outrage, "deepfakes are destroying trust") — fear framing
  • ACCEPT: deepfake-detection or likeness-protection product features (e.g.
    YouTube opens likeness detection to all creators) — AI capability as subject,
    try-it or scale hook

OUTLETS BY RELIABILITY (prefer higher tier when equally exciting):
  Tier 1 — labs, launch desks (Verge AI, TechCrunch AI, 404, Platformer,
  Information AI, HN, Ars, 9to5Mac, Wired, Engadget, Mashable, Rest of World,
  Product Hunt AI), major desks (NYT/WSJ/FT/Bloomberg/CNBC/BBC tech — often
  paywalled RSS), filtered arXiv cs.AI / Hugging Face.
  Tier 2 — high-signal analysis (Latent Space, Stratechery, Import AI, etc.).
  Fine for one slot, not a full dry edition.
  Tier 3 — aggregators (TLDR AI, Rundown): discover only; never ship an
  aggregator summary when Tier 1 primary exists for the same launch.
  Tier 4 — rotating verticals: at most one cross-industry story per week.

EDITORIAL DNA — mix biased toward fun and useful:
  • Cool capability / practical win: tools to try today, 'wait AI can do THAT?',
    surprising real-world use, productivity wins, AI in unexpected fields.
  • At most ONE 'state of the world' story for substance (big partnership,
    regulation shift, product war) when it still passes the editorial test.

NEWS-HOOK REQUIREMENT — beyond "vendor launched a thing":
  Acceptable hooks: competitive/market move, scale/scarcity, capability
  surprise, try-this-week, power-move from a recognizable figure.
  "Cohere launches Compass" is an ad; "Cohere's search model beats GPT-5.5
  on enterprise RAG" is news. If you cannot name the hook in one phrase → reject.

HARD EXCLUSIONS — never pick / score below 10:
  • True crime, predators, child safety, abuse, vigilantism, sting ops
  • AI doomer / existential risk / extinction / superalignment
  • AI incidental to a darker hook (crime/drama is the story)
  • Self-harm, eating-disorder, suicide-related AI chatbot content
  • Mass surveillance / privacy horror as primary angle
  • Pure geopolitics / tariffs where AI chips are just a prop

DOWNWEIGHT — unless the angle is genuinely valuable:
  • 'AI hallucination ruined this output' / lawyer caught using ChatGPT
  • Pure layoffs / 'AI is coming for your job' framing
  • Deepfake scandals (not detection-product launches — see above)

REJECT OUTRIGHT (below 20 for Scout):
  • Bare vendor launches with no news hook
  • Procurement / generic enterprise rollout news
  • Raw funding rounds with no product angle
  • Enterprise spinouts / new AI consulting arms / JV announcements
  • National free-access pilots with no new capability
  • Job-displacement scare without a positive try-it hook

PRIORITIZE (80+ for Scout): Rundown/Verge-style — shipped features, model
drops, mobile agents, billing backlash, memory across sessions, wearables
with dev APIs, finance tools you can connect today, coding-agent updates.

CROSS-EDITION UNIQUENESS — same topic / vendor+product / launch as the
last 30 days → reject. No repeats even with a reframed angle. Use
check_archive when unsure.
"""

_SCOUT_ROLE = """\
You are the Scout for AI Espresso. Survey the candidate pool, identify the
strongest 12-15 stories, and flag coverage gaps. You do NOT make final picks.
"""

_SCOUT_TAIL = """\
SCORING — apply the rubric above with numeric scores:
  below 20 = reject outright; below 40 = downweight; 80+ = prioritize.

PERSONA SPREAD — tag each story for the Editor's slot routing (readers
never see labels):
  - "business"  — leadership / consulting / strategy
  - "beginner"  — fun or useful for any Solvd employee (not engineers-only)
  - "engineer"  — technically meaty
  - "cross"     — non-IT industry doing something interesting with AI

Flag GAPS — if the pool is heavy on AI-failure stories, sociology, or
missing fun/useful angles, note it so the Editor can search_news.
"""

_EDITOR_ROLE = """\
You are the Editor for AI Espresso. Decide today's edition by calling tools
until you are ready to ship_edition. Persona slots (business, beginner,
engineer, cross) are internal routing only.
"""

_EDITOR_TAIL = """\
WORKING MEMORY — use update_memory for pool_quality, coverage_gaps, and
decisions. Read the working_memory block each turn.

DEFAULT STRATEGY:
  1. Review the Scout shortlist. Try shortlist picks before searching.
  2. read_candidate FIRST for every candidate you might pick. Only pick with
     a real article body (not fetch failed). Never pick from headline alone.
  3. pick one story per needed slot (usually 3). Each pick must pass the rubric.
  4. self_critique when you have a full slate.
  5. If revise → unpick flagged slots, search_news or read_candidate, re-pick.
  6. When self_critique approves → ship_edition.

WEAK POOL DAYS — ship 2 stories ONLY if you:
  • set pool_quality in working_memory to mention "weak"
  • call note_weak_pool with reason and which slot you skip
  • pick exactly 2 stories (not zero after note_weak_pool)
  • self_critique → ship_edition when approved
If you unpick everything to reset, re-pick immediately in the same pass.

WHEN TO USE search_news (max 2 per edition):
  • Scout gaps + shortlist feels dry/academic after first picks
  • After self_critique revise when pool lacks fun/useful angles
  • Do NOT search before trying the shortlist first

RULES:
  • Vendor cap: at most 2 stories per vendor (pick tool enforces).
  • Tier 1 minimum: at least one Tier 1 source (ship_edition enforces).
  • Mix vibes — not all model launches or all cautionary AI-failure stories.
  • ship_edition fails without self_critique approve (no shortcuts).

Use tools in any order that makes editorial sense. You control the loop.
"""

_CRITIC_ROLE = """\
You are the Critic for AI Espresso. The Editor has handed you picks.
Approve or send back with specific feedback. Judge the STORY, not raw
feed titles — rewrite fixes formatting; the topic must survive the rubric.
"""

_CRITIC_TAIL = """\
ROLE-SPECIFIC CHECKS:
  • Engineer slot: if the only headline path needs unexplained ML jargon
    ("diffusion model in N steps"), REVISE for plain-English capability.
  • Source mix: at least one Tier 1 primary. Flag if two+ picks are Tier 3
    summaries when Tier 1 coverage of the same launch exists.
  • Lawsuit / trial / founder feud as PRIMARY angle → REVISE unless the
    story is a shipped product or capability (legal drama as backdrop only).

HARD REJECT — send back if ANY pick matches hard exclusions above, repeats
a topic from Recent editions (last 30d), or uses AI-as-villain / incidental
framing.

REVISE if:
  • Two+ stories from the same vendor ONLY when both are weak/redundant
    (same product line). Two OpenAI picks OK if distinct (Codex mobile vs
    ChatGPT finance).
  • Academic paper needs topic explained vs launch/product/event story.
  • All three picks share the same vibe (all launches, all drama, all research).
  • verified: false — Editor must read_candidate or swap. Paywalled Tier-1
    with body_source rss_summary OK if verified is true.
  • Workforce sociology, hiring demographics, generational labor trends, or
    consultancy think pieces — even from Tier 1.
  • Weak-pool waiver is NOT permission for filler, unverified picks, or
    "best of a bad sociology pool." Revise with search_news before mediocrity.

APPROVE if:
  • Any Solvd employee would be more curious about AI, not less.
  • Three different vendors (or 2 + non-vendor story).
  • Majority fun, useful, or 'wow really?' — market rivalry counts as positive.
  • Every story passes subject-line + show-don't-tell; you can name each hook.
  • Slate could be rewritten as Rundown/Verge-style headlines without stretching.

The deterministic ship gate enforces the constitution in code. Approve
plausibly on-vibe slates; REVISE only clear violations. Obvious failures
(Waymo glitch, AI slop cautionary) → REVISE.

RESPONSE FORMAT:
  {"verdict": "approve" OR "revise",
   "reason": "<short explanation>",
   "issues": ["<slot>: <what to fix>", ...]}
"""

SCOUT_SYSTEM = _SCOUT_ROLE + _EDITORIAL_RUBRIC + _SCOUT_TAIL + _CONSTITUTION_PROMPT + "\n"
EDITOR_SYSTEM = _EDITOR_ROLE + _EDITORIAL_RUBRIC + _EDITOR_TAIL + _CONSTITUTION_PROMPT + "\n"
CRITIC_SYSTEM = _CRITIC_ROLE + _EDITORIAL_RUBRIC + _CRITIC_TAIL + _CONSTITUTION_PROMPT + "\n"


# ───────────────────────────────────────────────────────────────────────
# LLM call wrapper — delegates to espresso_agent.call_llm_json so the
# whole agent goes through a single Anthropic-SDK code path.
# ───────────────────────────────────────────────────────────────────────

def llm_json(system: str, prompt: str, schema: dict, max_tokens: int = 4000) -> Any:
    """One-shot structured output via Claude.

    Builds a fresh Anthropic client per call. We could cache the client at
    module scope, but call volume here is tiny (one Editor turn at a time)
    and per-call construction keeps test isolation clean.
    """
    from espresso_agent import call_llm_json, USE_ANTHROPIC
    if not USE_ANTHROPIC:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. The Editor loop requires Anthropic "
            "credentials — add it to your .env or repo secrets."
        )
    from anthropic import Anthropic
    client = Anthropic()
    return call_llm_json(client, system, prompt, schema, max_tokens=max_tokens)


# ───────────────────────────────────────────────────────────────────────
# Tool implementations (the Editor's hand)
# ───────────────────────────────────────────────────────────────────────

def _default_working_memory() -> dict:
    return {
        "pool_quality": "",
        "coverage_gaps": [],
        "critique_history": [],
        "editor_notes": "",
        "decisions": [],
    }


@dataclass
class AgentState:
    """Mutable state the Editor manipulates through tools."""
    today: dt.date
    needed_slots: list[str]
    shortlist: list[dict]              # [{id, headline, source, tier, url, vertical, persona}]
    candidates_by_id: dict[int, Any]   # id -> Candidate
    archive_headlines: list[str]
    vendor_counts: dict[str, int] = field(default_factory=dict)
    picks: dict[str, dict] = field(default_factory=dict)  # slot -> shortlist entry
    extra_candidates: list[dict] = field(default_factory=list)  # added via search_news
    next_id: int = 10_000              # ids for search-added candidates
    last_critic_verdict: dict | None = None
    working_memory: dict = field(default_factory=_default_working_memory)
    tool_calls: int = 0
    soft_budget: int = 25
    hard_budget: int = 40
    shipped: bool = False
    trace: list[TraceEvent] = field(default_factory=list)
    search_calls_used: int = 0
    pick_turn_by_slot: dict[str, int] = field(default_factory=dict)
    archive_checked_after_pick: bool = False


def _detect_vendor(headline: str, url: str, vendor_patterns) -> str | None:
    hay = f" {headline.lower()} {urlparse(url).netloc.lower()} "
    for vendor, needles in vendor_patterns:
        for n in needles:
            if n in hay:
                return vendor
    return None


# read_candidate — fetch article body so editor can judge depth, not just
#   headline + blurb. previously returned shortlist row unchanged (no-op).
# behavior:
#   - cache hit on disk: instant return
#   - cache miss: GET with normal user-agent, falls back to browser headers
#     on 401/403 (same retry path fetch_url already uses)
#   - body trimmed + tag-stripped to ~2500 chars to keep editor prompt small
#   - paywalled sources: article URL often 403; fall back to RSS summary on candidate
#   - fetch failure with no RSS text returns body: null so editor can route
def tool_read_candidate(state: AgentState, args: dict) -> dict:
    cid = int(args.get("id", -1))
    found = next((c for c in state.shortlist + state.extra_candidates if c["id"] == cid), None)
    if not found:
        return {"error": f"no candidate with id={cid}"}

    # already enriched on a prior turn — return cached body
    if found.get("body"):
        return {"candidate": found, "cached": True}

    from editorial import MIN_RSS_SUMMARY_CHARS, MIN_VERIFIED_BODY_CHARS
    from espresso_agent import fetch_url

    url = found.get("url", "")
    paywall = bool(found.get("paywall"))
    prestige = paywall or bool(found.get("prestige"))
    html = fetch_url(url, use_cache=True, prestige=prestige) if url else None

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

    if not body:
        note = "fetch failed"
        if paywall and rss_summary:
            note = f"fetch failed; rss summary too short ({len(rss_summary)} chars)"
        elif paywall:
            note = "fetch failed; no rss summary on candidate"
        return {"candidate": found, "body": None, "note": note}

    found = dict(found)
    found["body"] = body
    found["body_source"] = body_source
    # write back into shortlist/extra so pick() sees verified text
    for pool in (state.shortlist, state.extra_candidates):
        for i, c in enumerate(pool):
            if c.get("id") == cid:
                pool[i] = found
                break
    return {
        "candidate": found,
        "body_chars": len(body),
        "body_source": body_source,
    }


# search_news — web search escape hatch when shortlist + archive gap
# isn't covered by daily fetch. results filtered through allow-list so
# editor cannot accept SEO listicles (kleap.co, etc).
# limit: 2 calls per edition.
# Backends: pplx CLI (Python 3.12+) or Perplexity Sonar API (PERPLEXITY_API_KEY).


def _domain_from_url(url: str) -> str:
    netloc = urlparse(url).netloc or ""
    return netloc.removeprefix("www.")


def _pplx_cli_hits(query: str) -> tuple[list[dict] | None, str | None]:
    import shutil

    if not shutil.which("pplx"):
        return None, None
    try:
        p = subprocess.run(
            ["pplx", "search", "web", query],
            capture_output=True, text=True, timeout=45,
        )
        if p.returncode != 0:
            err = (p.stderr or p.stdout or "").strip()
            return None, err[-200:] if err else "pplx search failed"
        for line in p.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict) and "hits" in obj:
                    return obj.get("hits") or [], None
            except json.JSONDecodeError:
                continue
        return None, "no hits from pplx"
    except subprocess.TimeoutExpired:
        return None, "search timed out"


def _perplexity_api_hits(query: str) -> tuple[list[dict] | None, str | None]:
    """Ranked web results via Perplexity Search API (not Sonar chat completions)."""
    from load_env import strip_wrapping_quotes

    api_key = strip_wrapping_quotes(
        os.environ.get("PERPLEXITY_API_KEY") or os.environ.get("PPLX_API_KEY") or ""
    )
    if not api_key:
        return None, None
    try:
        api_key.encode("ascii")
    except UnicodeEncodeError:
        return None, "Perplexity API key contains non-ASCII characters — re-copy from Perplexity dashboard into .env without smart quotes"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = httpx.post(
            "https://api.perplexity.ai/search",
            headers=headers,
            json={"query": query, "max_results": 12},
            timeout=45,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        return None, f"Perplexity Search API: {exc}"
    hits = []
    for row in data.get("results") or []:
        url = (row.get("url") or "").strip()
        title = (row.get("title") or "").strip()
        if not url or not title:
            continue
        hits.append({
            "title": title,
            "url": url,
            "snippet": (row.get("snippet") or "")[:200],
            "domain": _domain_from_url(url),
        })
    if not hits:
        return None, "no hits from Perplexity Search API"
    return hits, None


def _apply_search_hits(state: AgentState, hits: list[dict], allowed: list[str]) -> dict:
    from espresso_agent import is_search_domain_allowed

    new_entries = []
    rejected = []
    for hit in hits[:12]:
        url = hit.get("url", "")
        title = (hit.get("title") or "").strip()
        if not title or not url:
            continue
        if not is_search_domain_allowed(url, allowed):
            rejected.append(hit.get("domain") or url)
            continue
        entry = {
            "id": state.next_id,
            "headline": title,
            "url": url,
            "source": hit.get("domain", "web"),
            "tier": 2,
            "vertical": None,
            "persona": "unknown",
            "via_search": True,
            "snippet": (hit.get("snippet") or "")[:200],
        }
        state.next_id += 1
        state.extra_candidates.append(entry)
        new_entries.append(entry)
        if len(new_entries) >= 6:
            break
    return {
        "added": len(new_entries),
        "entries": new_entries,
        "rejected_domains": rejected[:5],
    }


_SEARCH_AUTH_FAILURE_MARKERS = (
    "non-ASCII",
    "401",
    "403",
    "Unauthorized",
    "Forbidden",
    "API key",
    "PERPLEXITY_API_KEY",
    "pplx CLI not on PATH",
    "credentials",
)


def _is_search_auth_failure(err_text: str) -> bool:
    low = err_text.lower()
    return any(m.lower() in low for m in _SEARCH_AUTH_FAILURE_MARKERS)


def tool_search_news(state: AgentState, args: dict) -> dict:
    query = (args.get("query") or "").strip()
    if not query:
        return {"error": "missing query"}
    limit = _search_call_limit(state)
    if _search_calls_used(state) >= limit:
        return {"error": f"search_news limit reached (max {limit} per edition)"}

    import shutil
    from espresso_agent import search_allowed_domains

    allowed = search_allowed_domains()
    hits, err = _pplx_cli_hits(query)
    if hits is None:
        api_hits, api_err = _perplexity_api_hits(query)
        if api_hits is not None:
            hits = api_hits
        else:
            parts = []
            if err:
                parts.append(f"pplx: {err}")
            elif not shutil.which("pplx"):
                parts.append("pplx CLI not on PATH")
            if api_err:
                parts.append(api_err)
            elif not (os.environ.get("PERPLEXITY_API_KEY") or os.environ.get("PPLX_API_KEY")):
                parts.append("set PERPLEXITY_API_KEY (or install pplx-cli on Python 3.12+)")
            msg = "search failed — " + "; ".join(parts)
            if _is_search_auth_failure(msg):
                return {"error": msg, "auth_failure": True}
            state.search_calls_used += 1
            return {"error": msg}
    state.search_calls_used += 1
    return _apply_search_hits(state, hits, allowed)


def tool_check_archive(state: AgentState, args: dict) -> dict:
    state.archive_checked_after_pick = True
    headline = (args.get("headline") or "").lower()
    if not headline:
        return {"error": "missing headline"}
    # Token overlap fuzzy match
    h_tokens = set(re.findall(r"[a-z0-9]+", headline))
    matches = []
    for old in state.archive_headlines:
        o_tokens = set(re.findall(r"[a-z0-9]+", old.lower()))
        if not o_tokens:
            continue
        overlap = len(h_tokens & o_tokens) / max(len(h_tokens | o_tokens), 1)
        if overlap >= 0.45:
            matches.append({"archived_headline": old, "overlap": round(overlap, 2)})
    return {"matches": matches, "is_duplicate": bool(matches)}


def tool_pick(state: AgentState, args: dict, vendor_patterns) -> dict:
    slot = args.get("slot")
    cid = args.get("id")
    reason = args.get("reason", "")
    if slot not in state.needed_slots:
        return {"error": f"invalid slot {slot!r}; needed: {state.needed_slots}"}
    if cid is None:
        return {"error": "missing id"}
    found = next((c for c in state.shortlist + state.extra_candidates if c["id"] == int(cid)), None)
    if not found:
        return {"error": f"no candidate with id={cid}"}
    for existing_slot, existing_pick in state.picks.items():
        if existing_slot == slot:
            continue
        same_id = int(existing_pick.get("id", -1)) == int(found["id"])
        same_url = (existing_pick.get("url") or "").strip().lower() == (
            found.get("url") or ""
        ).strip().lower()
        same_headline = (existing_pick.get("headline") or "").strip().lower() == (
            found.get("headline") or ""
        ).strip().lower()
        if same_id or same_url or same_headline:
            return {
                "error": (
                    "duplicate story across slots is not allowed; pick a distinct "
                    "candidate for this slot"
                ),
                "conflict_slot": existing_slot,
            }
    from editorial import validate_pick_has_body

    pick_issues = validate_pick_has_body(found)
    if pick_issues:
        return {"error": "; ".join(pick_issues), "candidate_id": cid}
    # Bookkeeping: if slot was previously picked, decrement old vendor
    if slot in state.picks:
        old = state.picks[slot]
        ov = _detect_vendor(old["headline"], old["url"], vendor_patterns)
        if ov and state.vendor_counts.get(ov, 0) > 0:
            state.vendor_counts[ov] -= 1
    nv = _detect_vendor(found["headline"], found["url"], vendor_patterns)
    if nv and state.vendor_counts.get(nv, 0) >= 2:
        return {"error": f"vendor cap exceeded — {nv} already has 2 stories. Pick a different vendor."}
    found = dict(found)
    found["pick_reason"] = reason
    state.picks[slot] = found
    state.pick_turn_by_slot[slot] = state.tool_calls
    state.archive_checked_after_pick = False
    if nv:
        state.vendor_counts[nv] = state.vendor_counts.get(nv, 0) + 1
    return {
        "ok": True,
        "slot": slot,
        "picked": found,
        "reason": reason,
        "current_picks": {s: p["headline"] for s, p in state.picks.items()},
        "vendor_counts": dict(state.vendor_counts),
    }


def tool_unpick(state: AgentState, args: dict, vendor_patterns) -> dict:
    slot = args.get("slot")
    if slot not in state.picks:
        return {"error": f"nothing picked for slot {slot!r}"}
    pick_turn = state.pick_turn_by_slot.get(slot)
    if (
        pick_turn is not None
        and pick_turn == state.tool_calls - 1
        and not state.archive_checked_after_pick
    ):
        return {
            "error": (
                "you just picked this slot; commit to it and call self_critique, "
                "or read_candidate / search_news for new information before unpicking. "
                "Don't pick-then-unpick without new signal."
            ),
        }
    old = state.picks.pop(slot)
    ov = _detect_vendor(old["headline"], old["url"], vendor_patterns)
    if ov and state.vendor_counts.get(ov, 0) > 0:
        state.vendor_counts[ov] -= 1
    state.pick_turn_by_slot.pop(slot, None)
    return {"ok": True, "unpicked": old}


def _search_calls_used(state: AgentState) -> int:
    return state.search_calls_used


def _search_call_limit(state: AgentState) -> int:
    # Weak pools need extra discovery room; keep normal days tighter.
    return 4 if _weak_pool_waiver(state) else 3


def _weak_pool_waiver(state: AgentState) -> bool:
    pool = (state.working_memory.get("pool_quality") or "").lower()
    notes = (state.working_memory.get("editor_notes") or "").strip()
    return "weak" in pool and bool(notes)


def _min_picks_required(state: AgentState) -> int:
    return 2 if _weak_pool_waiver(state) else len(state.needed_slots)


def _tier1_counts(state: AgentState, need_t1: int) -> tuple[int, bool]:
    have = sum(1 for p in state.picks.values() if int(p.get("tier", 99)) == 1)
    return have, have >= need_t1


def _compress_shortlist_brief(state: AgentState) -> str:
    lines = []
    for c in state.shortlist[:15]:
        lines.append(
            f"id={c['id']} score={c.get('score', '?')} "
            f"[{c.get('persona', '?')}] {c['headline']} ({c['source']}, t{c.get('tier', '?')})"
        )
    for c in state.extra_candidates[:8]:
        lines.append(f"id={c['id']} [search] {c['headline']} ({c['source']})")
    return "\n".join(lines) if lines else "(empty)"


def validate_ship_gates(state: AgentState, rules: dict) -> dict:
    """Deterministic ship_edition checks. Returns {ok, errors, warnings}."""
    need_t1 = rules.get("tier1_minimum", 1)
    min_picks = _min_picks_required(state)
    have_t1, tier1_ok = _tier1_counts(state, need_t1)
    critic_ok = (state.last_critic_verdict or {}).get("verdict") == "approve"
    pick_count = len(state.picks)
    picks_ok = pick_count >= min_picks
    missing = [s for s in state.needed_slots if s not in state.picks]

    errors = []
    if not picks_ok:
        errors.append(
            f"need {min_picks} pick(s), have {pick_count}; missing slots: {missing}"
        )
    elif not _weak_pool_waiver(state) and missing:
        errors.append(f"unfilled required slots: {missing}")
    if not tier1_ok:
        errors.append(f"need {need_t1} tier-1 pick(s), have {have_t1}")
    if not critic_ok:
        verdict = (state.last_critic_verdict or {}).get("verdict", "none")
        errors.append(
            f"self_critique must approve before ship (last verdict: {verdict})"
        )
    if pick_count == 2 and not _weak_pool_waiver(state):
        errors.append(
            "2-story edition requires note_weak_pool and pool_quality mentioning 'weak'"
        )

    from editorial import validate_pick_has_body

    for slot, pick in state.picks.items():
        for issue in validate_pick_has_body(pick):
            errors.append(f"[{slot}] {issue}")

    from constitution import constitution_violations

    for slot, pick in state.picks.items():
        for reason in constitution_violations(
            pick.get("headline", ""),
            pick.get("blurb"),
            source_name=pick.get("source"),
        ):
            errors.append(f"[{slot}] constitution: {reason}")

    return {"ok": not errors, "errors": errors, "tier1_count": have_t1, "pick_count": pick_count}


def tool_update_memory(state: AgentState, args: dict) -> dict:
    key = (args.get("key") or "").strip()
    value = args.get("value")
    if not key:
        return {"error": "missing key"}
    allowed = {"pool_quality", "coverage_gaps", "editor_notes", "decisions"}
    if key not in allowed:
        return {"error": f"invalid key {key!r}; allowed: {sorted(allowed)}"}
    if key == "decisions" and isinstance(value, str):
        state.working_memory.setdefault("decisions", []).append(value)
    elif key == "coverage_gaps" and isinstance(value, list):
        state.working_memory["coverage_gaps"] = value
    else:
        state.working_memory[key] = value
    return {"ok": True, "working_memory": state.working_memory}


def tool_note_weak_pool(state: AgentState, args: dict) -> dict:
    reason = (args.get("reason") or "").strip()
    adjustments = (args.get("adjustments") or "").strip()
    if not reason:
        return {"error": "missing reason"}
    note = reason
    if adjustments:
        note = f"{reason} Adjustments: {adjustments}"
    state.working_memory["editor_notes"] = note
    if "weak" not in (state.working_memory.get("pool_quality") or "").lower():
        state.working_memory["pool_quality"] = (
            (state.working_memory.get("pool_quality") or "").strip() + " weak pool"
        ).strip()
    min_picks = _min_picks_required(state)
    result: dict = {"ok": True, "editor_notes": note}
    if len(state.picks) < min_picks:
        # Room to pick + critique + ship after documenting a thin day.
        state.hard_budget = min(state.hard_budget + 12, 56)
        result["budget_extended_to"] = state.hard_budget
        result["required_next"] = [
            f"pick {min_picks} stories into slots",
            "self_critique",
            "ship_edition",
        ]
        result["reminder"] = (
            f"Slate has {len(state.picks)} pick(s); need {min_picks} before shipping. "
            "Do not spend remaining budget on unpick/read loops."
        )
    return result


def tool_self_critique(state: AgentState, args: dict) -> dict:
    from editorial import MIN_VERIFIED_BODY_CHARS, candidate_has_verified_body

    min_picks = _min_picks_required(state)
    if len(state.picks) < min_picks:
        return {
            "error": f"need {min_picks} pick(s) before self_critique; have {len(state.picks)}",
            "missing_slots": [s for s in state.needed_slots if s not in state.picks],
        }
    unverified = [
        f"{slot}: no verified article body (read_candidate before pick)"
        for slot, p in state.picks.items()
        if not candidate_has_verified_body(p)
    ]
    if unverified:
        verdict = {
            "verdict": "revise",
            "reason": "One or more picks lack fetched article text; cannot approve unverified stories.",
            "issues": unverified,
        }
        state.last_critic_verdict = verdict
        return verdict

    picks_payload = []
    for s, p in state.picks.items():
        body = (p.get("body") or "").strip()
        picks_payload.append({
            "slot": s,
            "headline": p["headline"],
            "source": p["source"],
            "url": p["url"],
            "tier": p["tier"],
            "body_chars": len(body),
            "body_source": p.get("body_source") or ("article" if body else "none"),
            "verified": candidate_has_verified_body(p),
            "paywall": bool(p.get("paywall")),
            "editor_reason": (p.get("pick_reason") or "")[:300],
            "body_excerpt": body[:400] + ("…" if len(body) > 400 else ""),
        })
    pool_brief = _compress_shortlist_brief(state)
    weak = _weak_pool_waiver(state)
    edition_mode = (
        f"Documented weak-pool edition: {len(state.picks)} stories (minimum {min_picks}). "
        "You may approve 2 picks ONLY if they are verified product/capability news — "
        "not workforce think pieces or fetch-failed filler. If the pool is thin, "
        "revise and tell the Editor to search_news rather than approving sociology."
        if weak
        else f"Standard 3-story edition: {len(state.picks)} picks."
    )
    prompt = (
        f"Today is {state.today.isoformat()}. {edition_mode}\n\n"
        f"The Editor picked:\n\n"
        f"{json.dumps(picks_payload, indent=2)}\n\n"
        f"Other shortlist options:\n{pool_brief}\n\n"
        f"Working memory: {json.dumps(state.working_memory, indent=2)}\n\n"
        f"Recent editions (last 30d):\n"
        + "\n".join(f"- {h}" for h in (state.archive_headlines[:10] or ["(none)"]))
        + "\n\nReturn verdict, reason, and issues array (empty if approve)."
    )
    schema = {
        "type": "object",
        "properties": {
            "verdict": {"type": "string"},
            "reason": {"type": "string"},
            "issues": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["verdict", "reason"],
    }
    try:
        verdict = llm_json(CRITIC_SYSTEM, prompt, schema, max_tokens=6000)
    except Exception as e:
        return {
            "error": f"self_critique LLM failed: {e}",
            "hint": "fix picks or retry self_critique",
        }
    state.last_critic_verdict = verdict
    history = state.working_memory.setdefault("critique_history", [])
    history.append({
        "verdict": verdict.get("verdict"),
        "reason": (verdict.get("reason") or "")[:200],
    })
    state.working_memory["critique_history"] = history[-5:]
    state.trace.append(TraceEvent(
        ts=time.time(), role="critic", kind="handoff",
        result_summary=f"{verdict.get('verdict')}: {verdict.get('reason', '')[:120]}",
    ))
    return verdict


def tool_ship_edition(state: AgentState, args: dict, rules: dict) -> dict:
    gate = validate_ship_gates(state, rules)
    if not gate["ok"]:
        min_picks = _min_picks_required(state)
        critic_only_block = all("self_critique must approve" in e for e in gate["errors"])
        if (
            critic_only_block
            and _weak_pool_waiver(state)
            and len(state.picks) >= min_picks
        ):
            state.shipped = True
            state.trace.append(TraceEvent(
                ts=time.time(), role="system", kind="finalize",
                result_summary=(
                    "weak-pool critic override: shipped complete slate after repeated revise "
                    "with no deterministic gate errors"
                ),
            ))
            return {
                "shipped": True,
                "override": "weak_pool_critic_override",
                "picks": {s: p["headline"] for s, p in state.picks.items()},
            }
        if (state.last_critic_verdict or {}).get("verdict") == "approve":
            state.last_critic_verdict = None
            state.trace.append(TraceEvent(
                ts=time.time(), role="system", kind="constitution_gate_overrule",
                result_summary="ship_edition blocked: " + "; ".join(gate["errors"][:3]),
            ))
        return {"shipped": False, "errors": gate["errors"]}
    state.shipped = True
    state.trace.append(TraceEvent(
        ts=time.time(), role="editor", kind="finalize",
        result_summary=(
            f"shipped {gate['pick_count']} picks; "
            f"tier1={gate['tier1_count']}; critic=approve"
        ),
    ))
    return {"shipped": True, "picks": {s: p["headline"] for s, p in state.picks.items()}}


# ───────────────────────────────────────────────────────────────────────
# Scout — produces the shortlist
# ───────────────────────────────────────────────────────────────────────

def run_scout(
    today: dt.date,
    candidates_payload: list[dict],
    archive_headlines: list[str],
) -> dict:
    """Returns: {shortlist: [{id, headline, ..., score, persona, why}], gaps: [str]}"""
    prompt = (
        f"Today is {today.isoformat()}.\n\n"
        f"Candidate pool ({len(candidates_payload)} items):\n"
        f"{json.dumps(candidates_payload, indent=2)}\n\n"
        f"Already covered (last 30 days):\n"
        + "\n".join(f"- {h}" for h in archive_headlines[:20] or ["(none)"])
        + "\n\nReturn the strongest 12-15 stories ranked, with persona tag "
        "and 1-line `why`. Then list 0-3 GAPS — kinds of stories the pool "
        "is missing today (e.g. \"no consumer/lifestyle angle\", \"all model releases, no human outcomes\")."
    )
    schema = {
        "type": "object",
        "properties": {
            "shortlist": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "score": {"type": "integer"},
                        "persona": {"type": "string"},
                        "why": {"type": "string"},
                    },
                    "required": ["id", "score", "persona"],
                },
            },
            "gaps": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["shortlist"],
    }
    return llm_json(SCOUT_SYSTEM, prompt, schema, max_tokens=12000)


# ───────────────────────────────────────────────────────────────────────
# Native tool schemas + dispatch
# ───────────────────────────────────────────────────────────────────────

EDITOR_TOOLS: list[dict] = [
    {
        "name": "read_candidate",
        "description": "Fetch article body for a shortlist candidate id.",
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "integer", "description": "Candidate id"}},
            "required": ["id"],
        },
    },
    {
        "name": "search_news",
        "description": "Web search beyond the daily fetch (max 2 per edition).",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "check_archive",
        "description": "Fuzzy-check headline against last 30 days of editions.",
        "input_schema": {
            "type": "object",
            "properties": {"headline": {"type": "string"}},
            "required": ["headline"],
        },
    },
    {
        "name": "pick",
        "description": "Assign a candidate to a slot.",
        "input_schema": {
            "type": "object",
            "properties": {
                "slot": {"type": "string", "enum": ["business", "beginner", "engineer", "cross"]},
                "id": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["slot", "id", "reason"],
        },
    },
    {
        "name": "unpick",
        "description": "Remove the pick from a slot.",
        "input_schema": {
            "type": "object",
            "properties": {"slot": {"type": "string"}},
            "required": ["slot"],
        },
    },
    {
        "name": "update_memory",
        "description": "Update working memory (pool_quality, coverage_gaps, editor_notes, decisions).",
        "input_schema": {
            "type": "object",
            "properties": {
                "key": {
                    "type": "string",
                    "enum": ["pool_quality", "coverage_gaps", "editor_notes", "decisions"],
                },
                "value": {},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "note_weak_pool",
        "description": "Document a weak news day; required before shipping 2 stories.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "adjustments": {"type": "string"},
            },
            "required": ["reason"],
        },
    },
    {
        "name": "self_critique",
        "description": "Review current picks against the editorial rubric.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "ship_edition",
        "description": "Finalize and end the loop after self_critique approves.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def dispatch_tool(
    name: str,
    tool_input: dict,
    state: AgentState,
    vendor_patterns,
    rules: dict,
) -> dict:
    if (
        (state.last_critic_verdict or {}).get("verdict") == "approve"
        and not state.shipped
        and name != "ship_edition"
    ):
        return {
            "error": "critic approved — call ship_edition now; other tools are locked",
        }

    if name == "read_candidate":
        return tool_read_candidate(state, tool_input)
    if name == "search_news":
        return tool_search_news(state, tool_input)
    if name == "check_archive":
        return tool_check_archive(state, tool_input)
    if name == "pick":
        return tool_pick(state, tool_input, vendor_patterns)
    if name == "unpick":
        return tool_unpick(state, tool_input, vendor_patterns)
    if name == "update_memory":
        return tool_update_memory(state, tool_input)
    if name == "note_weak_pool":
        return tool_note_weak_pool(state, tool_input)
    if name == "self_critique":
        return tool_self_critique(state, tool_input)
    if name == "ship_edition":
        return tool_ship_edition(state, tool_input, rules)
    return {"error": f"unknown tool {name!r}"}


def _budget_warning(state: AgentState) -> str | None:
    if state.tool_calls >= state.soft_budget:
        return (
            f"soft budget warning: {state.tool_calls}/{state.hard_budget} tool calls used"
        )
    return None


def _build_initial_brief(state: AgentState, gaps: list[str]) -> str:
    if gaps and not state.working_memory.get("coverage_gaps"):
        state.working_memory["coverage_gaps"] = gaps
    critic_line = ""
    if state.last_critic_verdict:
        critic_line = f"\nLast self_critique: {json.dumps(state.last_critic_verdict)}\n"
    return (
        f"Today is {state.today.isoformat()}.\n"
        f"Needed slots: {state.needed_slots}\n"
        f"Scout coverage gaps: {gaps or '(none)'}\n\n"
        f"SHORTLIST:\n{_compress_shortlist_brief(state)}\n\n"
        f"Working memory: {json.dumps(state.working_memory, indent=2)}\n"
        f"Current picks: {json.dumps({s: p['headline'] for s, p in state.picks.items()})}\n"
        f"Vendor counts: {json.dumps(state.vendor_counts)}\n"
        f"{critic_line}\n"
        "Select today's edition using tools. Call ship_edition when ready."
    )


def _anthropic_client():
    from espresso_agent import USE_ANTHROPIC, CLAUDE_MODEL
    if not USE_ANTHROPIC:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. The Editor loop requires Anthropic credentials."
        )
    from anthropic import Anthropic
    return Anthropic(), CLAUDE_MODEL


def _run_tool_agent_loop(
    client,
    model: str,
    messages: list[dict],
    state: AgentState,
    vendor_patterns,
    rules: dict,
) -> bool:
    """One native tool_use loop until ship or hard budget."""
    while state.tool_calls < state.hard_budget and not state.shipped:
        try:
            resp = client.messages.create(
                model=model,
                max_tokens=8000,
                system=EDITOR_SYSTEM,
                messages=messages,
                tools=EDITOR_TOOLS,
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
            result = dispatch_tool(name, tool_input, state, vendor_patterns, rules)
            warn = _budget_warning(state)
            if warn:
                result = {**result, "budget_warning": warn}
            result_str = json.dumps(result)[:800]
            state.trace.append(TraceEvent(
                ts=time.time(), role="editor", kind="tool_result",
                name=name, result_summary=result_str, tool_use_id=block.id,
            ))
            print(
                f"  [agent turn {state.tool_calls}] {name}({tool_input}) → {result_str[:120]}",
                file=sys.stderr,
            )
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_str,
            })

        messages.append({"role": "user", "content": tool_results})
    return state.shipped


def run_tool_agent(state: AgentState, vendor_patterns, rules: dict, gaps: list[str]) -> bool:
    """Native tool_use loop. Returns True when ship_edition succeeds."""
    client, model = _anthropic_client()
    messages: list[dict] = [{"role": "user", "content": _build_initial_brief(state, gaps)}]

    shipped = _run_tool_agent_loop(client, model, messages, state, vendor_patterns, rules)

    # Weak-pool recovery: note_weak_pool with an empty slate then budget exhaustion
    # (seen in production runs) — grant one short lap to pick + ship.
    min_picks = _min_picks_required(state)
    if (
        not shipped
        and _weak_pool_waiver(state)
        and len(state.picks) < min_picks
        and any(t.name == "note_weak_pool" for t in state.trace if t.kind == "tool_call")
    ):
        extra = 10
        state.hard_budget = state.tool_calls + extra
        state.trace.append(TraceEvent(
            ts=time.time(), role="system", kind="handoff",
            result_summary=(
                f"weak-pool recovery lap (+{extra} tool calls, budget now {state.hard_budget})"
            ),
        ))
        messages.append({
            "role": "user",
            "content": (
                "Recovery lap: weak-pool edition is documented but the slate is incomplete. "
                f"Pick exactly {min_picks} stories with pick(), then self_critique, then "
                "ship_edition. Do not unpick unless swapping a story. No more read_candidate "
                "unless required for one pick."
            ),
        })
        shipped = _run_tool_agent_loop(client, model, messages, state, vendor_patterns, rules)

    # Finalization recovery: if the slate is complete but we ran out of budget
    # before the final self_critique -> ship_edition cycle, grant one short lap.
    if (
        not shipped
        and len(state.picks) >= min_picks
        and state.tool_calls >= state.hard_budget
    ):
        extra = 6
        state.hard_budget = state.tool_calls + extra
        state.trace.append(TraceEvent(
            ts=time.time(), role="system", kind="handoff",
            result_summary=(
                f"finalization recovery lap (+{extra} tool calls, budget now {state.hard_budget})"
            ),
        ))
        messages.append({
            "role": "user",
            "content": (
                "Finalization lap: slate is complete. Do not unpick. "
                "Call self_critique once, then ship_edition if approved."
            ),
        })
        shipped = _run_tool_agent_loop(client, model, messages, state, vendor_patterns, rules)

    if state.tool_calls >= state.hard_budget and not shipped:
        state.trace.append(TraceEvent(
            ts=time.time(), role="system", kind="error",
            result_summary=f"hard tool budget exhausted ({state.tool_calls})",
        ))
    return shipped


# ───────────────────────────────────────────────────────────────────────
# Public entry point
# ───────────────────────────────────────────────────────────────────────


class AgenticSelectFailed(Exception):
    """No recoverable agent slate; caller should persist trace and fail CI."""

    def __init__(self, message: str, trace: list[dict], meta: dict):
        super().__init__(message)
        self.trace = trace
        self.meta = meta


def write_agent_failure_artifact(today: dt.date, trace: list[dict], meta: dict) -> Path:
    """Persist trace when agent cannot produce a constitution-valid edition."""
    from espresso_agent import EDITIONS_DIR

    out = EDITIONS_DIR / f"{today.isoformat()}.failed.json"
    payload = {
        "date": today.isoformat(),
        "mode": "failed",
        "meta": meta,
        "agent_trace": trace,
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def _resolve_picks_to_candidates(
    state: AgentState,
    needed_slots: list[str],
    cand_by_id: dict[int, Any],
) -> list:
    from espresso_agent import Candidate

    selected = []
    for slot in needed_slots:
        if slot not in state.picks:
            continue
        entry = state.picks[slot]
        cid = entry["id"]
        if cid in cand_by_id:
            cand = cand_by_id[cid]
        else:
            cand = Candidate(
                headline=entry["headline"],
                url=entry["url"],
                source_name=entry["source"],
                tier=entry["tier"],
            )
        cand._agent_slot = slot  # type: ignore[attr-defined]
        selected.append(cand)
    return selected


def _try_salvage_approved_slate(
    state: AgentState,
    rules: dict,
) -> str | None:
    """Return salvage_reason if critic-approved picks pass all ship gates."""
    min_picks = _min_picks_required(state)
    if (state.last_critic_verdict or {}).get("verdict") != "approve":
        return None
    if len(state.picks) < min_picks:
        return None
    gate = validate_ship_gates(state, rules)
    if not gate["ok"]:
        return None
    if state.shipped:
        return None
    if state.tool_calls >= state.hard_budget:
        return "critic_approved_budget_exhausted"
    return "critic_approved_ship_not_called"


def agentic_select(
    candidates: list,             # list[Candidate] from espresso_agent
    archive_fps: set[str],
    rules: dict,
    today: dt.date,
    vendor_patterns,
    archive_headlines: list[str],
) -> tuple[list, list[dict], dict]:
    """Run Scout bootstrap → native-tool Editor.

    Returns (selected_candidates, trace_dicts, meta). On failure raises;
    caller should fall back to deterministic. meta has editor_notes and
    working_memory for edition.notes / observability.
    """
    # First-pass dedupe (same as deterministic)
    fresh = []
    seen_fps = set(archive_fps)
    for c in candidates:
        if c.fingerprint in seen_fps:
            continue
        if c.aggregator:
            continue
        seen_fps.add(c.fingerprint)
        fresh.append(c)
    fresh.sort(key=lambda c: (c.tier, c.source_name))

    # Cap pool: at most 4 per source, 60 total
    per_source = {}
    capped = []
    for c in fresh:
        n = per_source.get(c.source_name, 0)
        if n >= 4:
            continue
        per_source[c.source_name] = n + 1
        capped.append(c)
        if len(capped) >= 60:
            break

    # Slot rules
    is_rotation = today.weekday() in rules.get("tier4_rotation_days", [1, 4])
    needed_slots = ["business", "beginner", "engineer"] if not is_rotation \
        else ["business", "beginner", "cross"]

    # Build candidate dicts for the agent (with ids)
    candidates_payload = []
    cand_by_id = {}
    for i, c in enumerate(capped):
        cand_by_id[i] = c
        candidates_payload.append({
            "id": i,
            "headline": c.headline,
            "source": c.source_name,
            "tier": c.tier,
            "url": c.url,
            "vertical": c.vertical,
        })

    # SCOUT
    print(f"  [scout] surveying {len(candidates_payload)} candidates...", file=sys.stderr)
    scout_result = run_scout(today, candidates_payload, archive_headlines)
    shortlist_ranking = scout_result.get("shortlist", [])
    gaps = scout_result.get("gaps", [])
    # Hydrate shortlist with full info
    shortlist = []
    for entry in shortlist_ranking:
        cid = entry.get("id")
        if cid is None or cid not in cand_by_id:
            continue
        c = cand_by_id[cid]
        shortlist.append({
            "id": cid,
            "headline": c.headline,
            "source": c.source_name,
            "tier": c.tier,
            "url": c.url,
            "vertical": c.vertical,
            "blurb": c.blurb,
            "paywall": c.paywall,
            "score": entry.get("score", 0),
            "persona": entry.get("persona", "unknown"),
            "why": entry.get("why", ""),
        })
    shortlist.sort(key=lambda x: -x.get("score", 0))
    print(f"  [scout] shortlist of {len(shortlist)}, gaps: {gaps}", file=sys.stderr)

    state = AgentState(
        today=today,
        needed_slots=needed_slots,
        shortlist=shortlist[:20],
        candidates_by_id=cand_by_id,
        archive_headlines=archive_headlines,
    )
    state.working_memory["coverage_gaps"] = gaps or []
    state.trace.append(TraceEvent(
        ts=time.time(), role="scout", kind="handoff",
        result_summary=f"shortlist={len(shortlist)}, gaps={gaps}",
    ))

    ok = run_tool_agent(state, vendor_patterns, rules, gaps)
    min_picks = _min_picks_required(state)
    trace_dicts = [asdict(ev) for ev in state.trace]

    salvage_reason = _try_salvage_approved_slate(state, rules)
    if salvage_reason:
        state.trace.append(TraceEvent(
            ts=time.time(), role="system", kind="salvage",
            result_summary=salvage_reason,
        ))
        trace_dicts = [asdict(ev) for ev in state.trace]
        selected = _resolve_picks_to_candidates(state, needed_slots, cand_by_id)
        meta = {
            "editor_notes": (state.working_memory.get("editor_notes") or "").strip(),
            "working_memory": state.working_memory,
            "shipped": False,
            "salvaged": True,
            "salvage_reason": salvage_reason,
        }
        return selected, trace_dicts, meta

    if state.shipped and len(state.picks) >= min_picks:
        selected = _resolve_picks_to_candidates(state, needed_slots, cand_by_id)
        meta = {
            "editor_notes": (state.working_memory.get("editor_notes") or "").strip(),
            "working_memory": state.working_memory,
            "shipped": True,
            "salvaged": False,
            "salvage_reason": None,
        }
        return selected, trace_dicts, meta

    meta = {
        "editor_notes": (state.working_memory.get("editor_notes") or "").strip(),
        "working_memory": state.working_memory,
        "shipped": state.shipped,
        "salvaged": False,
        "salvage_reason": None,
    }
    raise AgenticSelectFailed(
        f"agent loop failed: shipped={ok}, picks={len(state.picks)}/{min_picks} "
        f"after {state.tool_calls} tool calls",
        trace_dicts,
        meta,
    )
