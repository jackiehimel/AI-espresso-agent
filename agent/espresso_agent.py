"""
AI Espresso — daily news agent for the Solvd AI Garage.

Pipeline (in order):
  1. Load tiered source allow-list from sources.yaml
  2. Fetch candidate headlines from every enabled source
     - Tier 1: vendor blogs + major paper AI desks (paywalled = headline only)
     - Tier 2: high-quality curated analysis
     - Tier 3: aggregators used as a sanity check (we DO NOT quote them; we
       only use them to detect stories that Tiers 1-2 missed, then go back
       to the primary source)
     - Tier 4: cross-industry vertical, only on rotation days
  3. Load archive (past 30d of editions) for dedupe
  4. Rank candidates against the Solvd-engineer relevance rubric using an LLM
  5. Apply mix logic: 1 business/leadership, 1 beginner-friendly, 1 engineer-focused
     (+ Tier 4 cross-industry slot on rotation days, displacing one of the three)
  6. Write punchy news-flash headlines + ~50-word blurbs
  7. Generate a "try this prompt" matched to today's stories
  8. Generate a daily-question seed
  9. Write edition JSON to data/editions/YYYY-MM-DD.json
 10. Append to data/archive.jsonl for future dedupe

Usage:
    python espresso_agent.py                 # run today's edition
    python espresso_agent.py --date 2026-05-15
    python espresso_agent.py --dry-run       # don't write files
    python espresso_agent.py --skip-fetch    # use last fetch cache (dev)
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import yaml
import httpx
import subprocess
from bs4 import BeautifulSoup

from prompt_tile import build_prompt_tile as _build_prompt_tile_llm

from editorial import (
    HEADLINE_HARD_SKIP_RE,
    validate_edition_stories,
    validate_hook,
)

# ───────────────────────────────────────────────────────────────────────
# Config
# ───────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
EDITIONS_DIR = DATA_DIR / "editions"
CACHE_DIR = DATA_DIR / ".cache"
ARCHIVE_FILE = DATA_DIR / "archive.jsonl"
SOURCES_FILE = ROOT / "sources.yaml"

for d in (DATA_DIR, EDITIONS_DIR, CACHE_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Anthropic public API model id. Override with ESPRESSO_MODEL env var if you
# want to point at a newer snapshot without editing code.
CLAUDE_MODEL = os.environ.get("ESPRESSO_MODEL", "claude-sonnet-4-5")
HTTP_TIMEOUT = 15.0
USER_AGENT = "AI-Espresso/1.0 (+internal Solvd AI Garage; contact jackie)"

# Browser-grade headers for prestige outlets that 403 our default UA. We
# only fetch their section-listing HTML for headlines — we never bypass
# article paywalls. This is the same shape a logged-out Chrome browser
# sends when someone visits the section index directly.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

# LLM backend: Anthropic SDK. ANTHROPIC_API_KEY must be set in the
# environment (locally in a .env or as a GitHub Actions secret).
#
# The sandbox-only `pplx llm extract` CLI path was removed when we moved
# off Perplexity-managed infra. If you want to keep the old behavior for
# local hacking, see render_images.py for the shape the fallback used to
# have.
USE_ANTHROPIC = bool(os.environ.get("ANTHROPIC_API_KEY"))
if USE_ANTHROPIC:
    from anthropic import Anthropic


# ───────────────────────────────────────────────────────────────────────
# Data classes
# ───────────────────────────────────────────────────────────────────────

@dataclass
class Source:
    name: str
    tier: int
    url: str
    kind: str = "html"
    enabled: bool = True
    paywall: bool = False
    aggregator: bool = False
    research: bool = False
    vertical: str | None = None
    weight: float = 1.0
    # Sources that aggressively bot-block (NYT, WSJ, FT, Bloomberg, Reuters,
    # The Information, etc.) need browser-grade headers to serve their
    # section-listing HTML. We still respect their paywall on individual
    # articles — we only ever read the listing pages for headlines + URLs.
    prestige: bool = False
    # When url is a mirror RSS host (e.g. githubusercontent.com), search_domain
    # keeps search_news allow-list aligned with the real publisher.
    search_domain: str | None = None


@dataclass
class Candidate:
    headline: str          # the raw headline from the source
    url: str               # primary URL
    source_name: str
    tier: int
    blurb: str = ""        # RSS summary / excerpt when paywalled
    paywall: bool = False  # article URL usually 403; trust RSS summary only
    vertical: str | None = None
    aggregator: bool = False
    fingerprint: str = ""  # for dedupe

    def __post_init__(self):
        if not self.fingerprint:
            self.fingerprint = fingerprint_of(self.headline, self.url)


@dataclass
class Story:
    """A story selected for publication."""
    slot: str              # "business" | "beginner" | "engineer" | "cross_industry"
    headline: str          # punchy news-flash rewrite
    blurb: str             # ~50 words, AI-fluent angle
    why_it_matters: str    # 1 line: "what a Solvd person can take away"
    source_name: str
    source_url: str
    tier: int
    original_headline: str
    fingerprint: str


@dataclass
class Edition:
    date: str              # ISO YYYY-MM-DD
    stories: list[Story] = field(default_factory=list)
    try_this_prompt: dict = field(default_factory=dict)
    daily_question: str = ""
    generated_at: str = ""
    model: str = CLAUDE_MODEL
    notes: list[str] = field(default_factory=list)


# ───────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────

def fingerprint_of(headline: str, url: str) -> str:
    """Stable hash for dedupe — title-normalized + URL host."""
    norm = re.sub(r"[^a-z0-9 ]+", "", headline.lower()).strip()
    host = urlparse(url).netloc.lower()
    return hashlib.sha1(f"{host}|{norm}".encode()).hexdigest()[:16]


def load_sources() -> tuple[list[Source], dict]:
    with open(SOURCES_FILE) as f:
        cfg = yaml.safe_load(f)
    sources = [Source(**{k: v for k, v in s.items()}) for s in cfg["sources"]]
    return [s for s in sources if s.enabled], cfg.get("rules", {})


# extra outlets allowed for search_news but not in the daily fetch list.
# kept narrow on purpose — anything not here gets filtered out so the
# editor cannot pick up SEO listicles like kleap.co "top 4 ai tools"
# (which is how the 2026-05-21 sample edition got polluted).
# add a domain here only if it would survive the screenshot test.
SEARCH_ALLOWLIST_EXTRA = {
    # primary-source extensions
    "arxiv.org", "github.com", "huggingface.co",
    # quality press not always in the daily fetch
    "theverge.com", "techcrunch.com", "wired.com", "theatlantic.com",
    "nytimes.com", "wsj.com", "ft.com", "economist.com", "bloomberg.com",
    "reuters.com", "apnews.com", "axios.com", "semafor.com",
    "404media.co", "platformer.news", "stratechery.com",
    "theinformation.com", "restofworld.org",
    # AI-native publications
    "importai.substack.com", "thezvi.substack.com",
}


# allow-list union: all configured source domains + the curated extras.
# returned as a set of registrable domains (last two labels) so subdomains
# like ai.googleblog.com match a google.com entry.
def search_allowed_domains() -> set[str]:
    sources, _ = load_sources()
    allowed: set[str] = set(SEARCH_ALLOWLIST_EXTRA)
    for s in sources:
        raw = (s.search_domain or s.url).strip()
        if not raw:
            continue
        if "://" not in raw:
            raw = f"https://{raw}"
        host = urlparse(raw).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if host in {"raw.githubusercontent.com", "github.com"}:
            continue
        allowed.add(host)
        # also add the registrable form (foo.bar.com → bar.com)
        parts = host.split(".")
        if len(parts) >= 2:
            allowed.add(".".join(parts[-2:]))
    return allowed


# domain check used by search_news. accepts either a bare domain (foo.com)
# or a full URL. returns True if the URL's host or its registrable parent
# is in the allow-list.
def is_search_domain_allowed(url_or_host: str, allowed: set[str]) -> bool:
    host = urlparse(url_or_host).netloc.lower() if "://" in url_or_host else url_or_host.lower()
    if host.startswith("www."):
        host = host[4:]
    if host in allowed:
        return True
    parts = host.split(".")
    for i in range(len(parts) - 1):
        if ".".join(parts[i:]) in allowed:
            return True
    return False


def cache_path(url: str) -> Path:
    return CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".html")


def fetch_url(url: str, use_cache: bool = False, prestige: bool = False) -> str | None:
    """Fetch HTML. Returns None on failure (we never fail the whole edition).

    For prestige=True sources (paywalled paper sites that 403 a bot UA),
    use browser-grade headers. We never bypass article paywalls — only
    the publicly-served section-listing HTML.
    """
    p = cache_path(url)
    if use_cache and p.exists():
        return p.read_text(errors="replace")

    headers = BROWSER_HEADERS if prestige else {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,*/*",
    }
    try:
        with httpx.Client(
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
            headers=headers,
        ) as client:
            r = client.get(url)
            r.raise_for_status()
            p.write_text(r.text, errors="replace")
            return r.text
    except Exception as e:
        # If a non-prestige fetch failed with 401/403, retry once with
        # browser headers — some sites add bot-blocks unpredictably.
        msg = str(e)
        if not prestige and ("401" in msg or "403" in msg):
            print(f"  ↻ retrying with browser headers: {url}", file=sys.stderr)
            try:
                with httpx.Client(
                    timeout=HTTP_TIMEOUT,
                    follow_redirects=True,
                    headers=BROWSER_HEADERS,
                ) as client:
                    r = client.get(url)
                    r.raise_for_status()
                    p.write_text(r.text, errors="replace")
                    return r.text
            except Exception as e2:
                print(f"  ! fetch failed (after retry): {url} — {e2}", file=sys.stderr)
                return None
        print(f"  ! fetch failed: {url} — {e}", file=sys.stderr)
        return None


def _rss_item_summary(item) -> str:
    """Plain-text RSS description/summary (paywalled outlets ship story context here)."""
    raw = ""
    for tag in ("description", "summary"):
        el = item.find(tag)
        if el:
            raw = el.get_text(" ", strip=True)
            if raw:
                break
    if not raw:
        encoded = item.find(
            lambda t: getattr(t, "name", None) and str(t.name).endswith("encoded")
        )
        if encoded:
            raw = encoded.get_text(" ", strip=True)
    if not raw:
        return ""
    return BeautifulSoup(raw, "html.parser").get_text(" ", strip=True)


def extract_rss_candidates(xml: str, source: Source, max_n: int = 8) -> list[Candidate]:
    """Extract headlines + links from an RSS / Atom feed."""
    soup = BeautifulSoup(xml, "xml")
    out: list[Candidate] = []
    seen: set[str] = set()
    # RSS uses <item><title><link>; Atom uses <entry><title><link href=...>
    for item in soup.find_all(["item", "entry"]):
        title_el = item.find("title")
        link_el = item.find("link")
        if not title_el:
            continue
        title = " ".join(title_el.get_text(" ", strip=True).split())
        if not title or len(title) < 12:
            continue
        # link: in RSS this is text content; in Atom it's an href attribute
        href = ""
        if link_el:
            href = link_el.get("href") or link_el.get_text(strip=True)
        if not href:
            guid_el = item.find("guid")
            if guid_el:
                href = guid_el.get_text(strip=True)
        if not href or not href.startswith("http"):
            continue
        if href in seen:
            continue
        seen.add(href)
        blurb = _rss_item_summary(item)
        out.append(Candidate(
            headline=title,
            url=href,
            source_name=source.name,
            tier=source.tier,
            blurb=blurb,
            paywall=source.paywall,
            vertical=source.vertical,
            aggregator=source.aggregator,
        ))
        if len(out) >= max_n:
            break
    return out


def extract_candidates(html: str, source: Source, max_n: int = 8) -> list[Candidate]:
    """Extract headlines + links from a source page.

    For RSS/Atom sources (kind=='rss'), parse the feed XML.
    For HTML sources, use a heuristic: <article>/<h2>/<h3> elements with anchors.
    """
    if source.kind == "rss":
        return extract_rss_candidates(html, source, max_n=max_n)
    soup = BeautifulSoup(html, "html.parser")
    out: list[Candidate] = []
    seen_urls: set[str] = set()

    selectors = [
        "article a", "h1 a", "h2 a", "h3 a",
        "li a[href]",
    ]
    for sel in selectors:
        for a in soup.select(sel):
            text = " ".join(a.get_text(" ", strip=True).split())
            href = a.get("href") or ""
            if not text or len(text) < 20 or len(text) > 220:
                continue
            if href.startswith("#") or href.startswith("mailto:"):
                continue
            full = urljoin(source.url, href)
            if full in seen_urls:
                continue
            # filter junk anchors
            if any(skip in text.lower() for skip in ("subscribe", "sign in", "log in", "newsletter", "menu", "cookie")):
                continue
            seen_urls.add(full)
            out.append(Candidate(
                headline=text,
                url=full,
                source_name=source.name,
                tier=source.tier,
                paywall=source.paywall,
                vertical=source.vertical,
                aggregator=source.aggregator,
            ))
            if len(out) >= max_n:
                break
        if len(out) >= max_n:
            break
    return out


def load_archive(days: int = 30) -> set[str]:
    """Return the set of fingerprints used in editions within the lookback window."""
    if not ARCHIVE_FILE.exists():
        return set()
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    seen: set[str] = set()
    with open(ARCHIVE_FILE) as f:
        for line in f:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("date", "") >= cutoff:
                for fp in rec.get("fingerprints", []):
                    seen.add(fp)
    return seen


def append_archive(edition: Edition) -> None:
    rec = {
        "date": edition.date,
        "fingerprints": [s.fingerprint for s in edition.stories],
        "headlines": [s.headline for s in edition.stories],
    }
    with open(ARCHIVE_FILE, "a") as f:
        f.write(json.dumps(rec) + "\n")


# ───────────────────────────────────────────────────────────────────────
# LLM: ranking + rewriting
# ───────────────────────────────────────────────────────────────────────

# DEPRECATED — local dev / emergency only. Production and CI must use
# mode="agent" (Scout → Editor tool_use loop in espresso_loop.py).
#
# RANKING_SYSTEM is NOT kept in sync with _EDITORIAL_RUBRIC in
# espresso_loop.py. Do not treat rank_and_select as a second product
# editorial brain. Never set ESPRESSO_ALLOW_DETERMINISTIC_FALLBACK in
# .github/workflows/daily-edition.yml or any scheduled/production job.
#
# Emergency use only:
#   ESPRESSO_ALLOW_DETERMINISTIC_FALLBACK=1 python espresso_agent.py --mode agent
#   python espresso_agent.py --mode deterministic   # explicit legacy pipeline
RANKING_SYSTEM = """\
You are the editorial brain behind AI Espresso, an internal daily news
brief for Solvd — a ~3,000-person software services company. The readers
are a wide mix: engineers, consultants, account leaders, designers,
recruiters, sales reps, interns, execs. The job is to get every single
one of them genuinely EXCITED about AI today.

The one editorial test that overrides everything else:

  Would a non-technical person screenshot this and send it to a friend?
  Would they say "wait, AI can do THAT now?" out loud?

If the answer is no, skip the story. Trade-press business updates and
rollout announcements fail this test by default.

──────────────── FAVOR (these score 70+) ────────────────

  A) Capability leaps and demos — something that was impossible or
     awkward last month and is suddenly easy. Glasses you write in
     midair. Models that watch a video and explain it. Agents that
     run while your laptop is closed. Drug discovery in hours.

  B) Human outcomes — someone built / saved / earned / lost / hired /
     fired / shipped a thing because of AI. Personal-finance bot reads
     your bank account. Kid passes the bar. Farmer talks to a
     tractor. Indie dev makes $10k from a weekend project.

  C) Cultural moments — lawsuits, celebrities, viral debates, labor
     stories, deepfake panics, AI-related drama on social. Power moves
     from recognizable names (Altman, Musk, Zuck, Sundar, Dario,
     Mira, Sergey, Demis) with real stakes attached.

  D) New consumer / prosumer AI products people can actually try this
     week. Apps. Glasses. Headphones. Plugins. Mobile features. The
     more tangible the better.

  E) Capability surprises in unexpected fields — homework, gardening,
     legal aid, cooking, dating apps, language learning, accessibility,
     hospitals. "AI showed up in [field you didn't expect]."

──────────────── NO FORCED LEAD STORY ────────────────

DO NOT force one story to be a "big news" headline every day. Some
days will have a paradigm-shifting story (model launch, huge funding,
capability milestone) and it belongs first. Other days will be quieter
— three solid practical/cool stories is fine. NEVER promote a mediocre
story to the lead just because the lead slot exists. The reader should
feel like they got 3 things worth knowing, not 1 forced headline + 2
fillers.

──────────────── HARD EXCLUDE (score BELOW 10) ────────────────

These are non-negotiable. Score below 10 no matter how AI-prominent:
  - True crime, predators, child safety, abuse, vigilantism, sting ops
  - AI doomer / existential risk / extinction / superalignment takes
  - Self-harm / suicide / eating-disorder content involving AI
  - Stories where AI is incidental to a darker hook (the crime/drama
    is the real story, AI is just window dressing)
  - Mass surveillance / spying / privacy horror (car tracks your face,
    boss watches you blink, insurance hikes your premium). Even if
    technically true, these make AI feel scary, not exciting.
  - Pure geopolitics / tariffs / trade war stories where AI is a prop.
  - 'AI hallucination ruined this professional output' cautionary tales
  - Lawyer caught using ChatGPT (we've seen it a hundred times)
  - Deepfake scandal stories
  - 'AI is coming for your job' framing without a positive angle

These are HARD excludes — NOT 'use sparingly.' The reader should never
open an edition and feel anxious about AI's existence.

──────────────── CROSS-EDITION UNIQUENESS ────────────────

You will be shown headlines from editions in the last 30 days. Score
ANY candidate that covers the same topic, same vendor+product, or same
launch as a recent edition BELOW 20. Repeats are not allowed even with
a reframed angle.

──────────────── NEWS-HOOK REQUIREMENT ────────────────

Every story needs a HOOK beyond "vendor launched a thing." A bare
product launch with no stakes reads like an ad and fails the editorial
test. Acceptable hooks:
  • Backlash, drama, or tension (developers revolt over billing)
  • Scale or scarcity (largest funding round, model too powerful to
    release, deployed to 10k employees overnight)
  • Capability surprise (passes the bar, beats specialists at task X,
    finds disease 3 years early)
  • Tangible thing the reader can use this week (Codex on your phone,
    Routines in Claude Code, Pulse morning briefing)
  • Power-move headline (recognizable figure does the unexpected)

A "Cohere launches Compass for search" headline with no stakes is an
ad. A "Cohere's new search model just beat GPT-5.5 on enterprise RAG"
headline is news. If you can't articulate the hook in one phrase, the
story probably reads like an ad — skip it.

──────────────── REJECT (score below 30) ────────────────

  - Bare vendor product launches with no news hook. "X launches Y for
    Z" with no backlash, scale, surprise, or stakes. These read like
    press releases. Skip unless the launch itself is paradigm-shifting.
  - Pure procurement / pricing news. "X now costs $Y." "Vendor adjusts
    enterprise tier." UNLESS the change is genuinely newsworthy and
    affects how thousands of developers work (e.g. a major billing
    overhaul triggering visible developer backlash counts; a small
    tier rename does not).
  - Generic enterprise rollout announcements with no capability or
    human angle. "Acme Corp signs deal to use Claude." Skip unless
    the deal itself is the story (e.g. Anthropic + every Fortune 50).
  - "X firms are now using AI" survey / report stories. Trade-press
    filler.
  - HBR / Sloan / McKinsey-style think pieces — "3 habits for AI
    teams", "5 ways CIOs should think about agents." These read like
    homework. Skip them.
  - Raw funding round announcements without a product, capability,
    or human angle. "Startup raises $40M" is not a story unless the
    capability or scale is itself remarkable.
  - Pure hype with no shipped artifact. Vapor demos. Slideware.
  - Research-only stories with no near-term, perceivable implication
    for a normal person.
  - Stories from Tier 3 aggregators directly. Use them only to detect
    that a story exists; cite the primary Tier 1/2 source.

──────────────── PERSONA SPREAD ────────────────

Try to land each edition's 3 stories across DIFFERENT personas, not
three variants of the same vibe:

  - One for the consumer / everyone ("what AI can do for me today")
  - One for the builder / engineer ("what AI can do for the work")
  - One for the business / culture watcher ("what AI just did to the
    industry / the lawsuit / the celebrity / the market")

If two finalists feel like the same persona, drop the weaker one and
replace it from another slot.

──────────────── NEWSLETTER CALIBRATION (score 85+) ────────────────

Stories that SHOULD win most days — the vibe of The Rundown, TLDR AI,
and good consumer-tech desks:
  • Shipped product / feature people can try this week (Codex on mobile,
    ChatGPT connects to bank accounts, Claude Routines, Ray-Ban neural
    writing opens to developers, managed agents with memory)
  • New model drops with a concrete capability hook (GPT-5.x, DeepSeek
    V4 preview, "too powerful to release" drama)
  • Developer-facing changes with backlash or stakes (billing overhaul,
    API pricing revolt, Copilot "code red" to catch up)
  • Power moves & platform wars (OpenAI vs Apple, Google × Anthropic
    compute deals) WHEN there is a specific shipped artifact or dollar
    figure — not vague "partnership" filler

Stories that should score BELOW 25 even if Tier 1:
  • "Spinning out" / new enterprise services / consulting JV / white-glove
    implementation arm (reads like IR, not news)
  • National free-subscription pilots framed as infrastructure (entire
    country gets ChatGPT) unless there is a wild capability angle
  • White-collar displacement without a try-it-yourself hook ("AI writes
    mortgages, brokers worried", "lawyers replaced")
  • Generic "firm deploys AI" or survey filler

Vendor mix: two stories from the same lab is FINE when they are clearly
different products (e.g. ChatGPT finance + Codex mobile). Never swap in
a weaker non-vendor story just to avoid repeating OpenAI.

──────────────── TONAL TARGETS ────────────────

When unsure between two stories, pick the one that:
  • a 22-year-old intern would forward to their group chat;
  • a salesperson would casually reference on a client call;
  • or a non-engineer parent would actually understand and remember.

You are NOT writing for an IT trade publication. You are writing the
thing readers actually look forward to opening every morning.
"""

# Headlines matching these are removed before ranking (deterministic guard).
_HEADLINE_HARD_SKIP = HEADLINE_HARD_SKIP_RE

# Prefer headlines that read like newsletter product news (deterministic tie-break).
_SHIPPED_PRODUCT = re.compile(
    r"(launch|ship|debut|drop|preview|roll(?:s|ing)?\s+out|now available|"
    r"brings?.+to mobile|personal finance|connect.+bank|codex|gpt-5|"
    r"claude.+(memory|agent|routine)|managed agent|neural|ray-ban|"
    r"billing|developer backlash|code red)",
    re.I,
)

RANK_PROMPT_TEMPLATE = """\
Today is {date}.

I have {n} candidate stories from the last 24-48 hours. Score each
0-100 on the AI Espresso editorial filter described in your system
prompt. Then assign each a slot:
  - "business"     — relevant to leadership / account / consulting roles
  - "beginner"     — accessible to a non-engineer; consumer-friendly product news
                     (display label EVERYDAY — not the daily prompt card)
  - "engineer"     — technically meaty enough that a senior engineer cares
  - "cross"        — non-IT industry adopting AI (healthcare, supply chain,
                     SMB, manufacturing, finance ops, etc.)
  - "skip"         — reject

Already covered in the last 30 days (skip anything matching these):
{archive_titles}

Candidates (JSON):
{candidates_json}

Respond with a JSON object containing a `rankings` array of objects with
fields: id (int), score (0-100 int), slot (string), reason (string).
"""


REWRITE_SYSTEM = """\
You write headlines and blurbs for AI Espresso, an internal Solvd
daily brief read by engineers, consultants, sales, designers, recruiters,
and interns. The vibe is closer to a great consumer-tech newsletter than
to a corporate trade publication.

HEADLINES (max ~12 words, verbs forward when possible):
  Good — the kind we want:
    "Meta's smart glasses just became a real wearable computer"
    "ChatGPT can now look at your bank account"
    "Claude Code can now run itself while your laptop is closed"
    "OpenAI just shipped a coding agent straight to your phone"
    "Anthropic just entered Elon Musk's entire colossus cluster"
  Bad — the kind we never want:
    "PwC expands strategic Claude deployment across client pipeline"
    "HBR: 3 practices teams can use to adopt AI"
    "Anthropic launches Claude tier between Pro and Enterprise"
  Why the bad examples are bad: they read like a press release, they
  contain words like "strategic", "deployment", "tier", and they don't
  make anyone curious.

BLURBS (~30-55 words, 1-2 sentences):
  Plain English. Concrete and specific. Lead with what's new and what
  someone can DO with it, not who announced it. No buzzwords. No
  "strategic", "unlock", "empower", "transform", "leverage".
  If the source is paywalled, the excerpt may be only the RSS summary
  (body_source rss_summary). Write only what's in that excerpt + headline —
  never fabricate quotes, numbers, or "research shows" claims.

WHY-IT-MATTERS (1 line, max 20 words):
  Plain language. What a normal reader — engineer or not — should take
  away. Skip the meta. NEVER "this is interesting" or "this matters
  because." Just say the thing.

ENGINEER SLOT — headline must work for non-ML readers:
  Say what became possible in plain English. Avoid unexplained terms like
  "diffusion model", "few-step", "autoregressive", "distillation".
  Good: "Researchers made AI video you can steer in real time"
  Bad: "New diffusion model generates interactive video in 4 steps"

JSON: In headline/blurb/why_it_matters strings, never use unescaped "
characters — use single quotes inside strings or rephrase.
"""

REWRITE_PROMPT_TEMPLATE = """\
Rewrite this story for AI Espresso.

Slot: {slot}
Source: {source_name} (Tier {tier})
Source URL: {url}
Original headline: {original_headline}
Excerpt (may be empty): {blurb}

Respond with a JSON object containing exactly three fields: headline,
blurb, why_it_matters.
"""


DAILY_QUESTION_TEMPLATE = """\
Write ONE short, opinionated daily question for AI Espresso readers
(Solvd employees). It should provoke a one-sentence answer they'd
actually want to submit. Inspired by NYT Games' daily Connections /
Wordle mechanic — low effort, high curiosity.

Examples of the right register:
  "How do you define AI fluency?"
  "What's one task you tried to automate this week and failed?"
  "Which AI tool would you NOT give to a junior engineer, and why?"

Today's themes (don't quote them, but stay tonally connected):
{themes}

Respond with a JSON object containing exactly one field: question.
"""


def call_llm_json(
    client: Any,
    system: str,
    prompt: str,
    schema: dict,
    max_tokens: int = 2000,
) -> Any:
    """Call Claude and parse a JSON response that matches `schema`.

    The `schema` argument is appended to the system prompt as a strict-output
    instruction. Anthropic doesn't enforce JSON schemas server-side (unlike
    OpenAI's response_format), so we ask the model to emit JSON matching the
    schema and then parse it. Stripping ```json fences guards against the
    occasional markdown-wrapped response.

    Raises if ANTHROPIC_API_KEY is missing — the daily cron will fail loudly
    rather than silently fall back to a broken codepath.
    """
    if not USE_ANTHROPIC or client is None:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to your .env locally or as "
            "a repo secret in GitHub Actions before running the agent."
        )

    schema_hint = (
        "\n\nReturn ONLY a JSON object matching this schema (no prose, no "
        "markdown fences):\n" + json.dumps(schema)
    )
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system + schema_hint,
        messages=[{"role": "user", "content": prompt}],
    )
    text = resp.content[0].text.strip()
    # Strip ```json ... ``` if the model wrapped it anyway.
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        # Save the bad output for postmortem before raising.
        if os.environ.get("ESPRESSO_DEBUG"):
            (DATA_DIR / ".cache" / f"bad_llm_{int(time.time()*1000)}.txt").write_text(text)
        raise RuntimeError(f"LLM returned non-JSON: {e}\nFirst 400 chars: {text[:400]}")


# ───────────────────────────────────────────────────────────────────────
# Main pipeline
# ───────────────────────────────────────────────────────────────────────

def fetch_all_candidates(sources: list[Source], use_cache: bool = False) -> list[Candidate]:
    candidates: list[Candidate] = []
    for s in sources:
        print(f"  fetching T{s.tier}: {s.name}", file=sys.stderr)
        html = fetch_url(s.url, use_cache=use_cache, prestige=s.prestige or s.paywall)
        if html is None:
            continue
        cands = extract_candidates(html, s, max_n=6)
        candidates.extend(cands)
        time.sleep(0.4)  # be polite
    print(f"  → {len(candidates)} total candidates", file=sys.stderr)
    return candidates


# Vendor detection — used to cap how many stories we pick from a single
# AI lab / product family per edition. The order matters: more specific
# keywords win over generic ones (e.g. "ray-ban" → meta before any generic
# meta match would matter).
VENDOR_PATTERNS: list[tuple[str, list[str]]] = [
    ("openai",     ["openai", "chatgpt", "gpt-4", "gpt-5", "gpt4", "gpt5", "sora", "codex", "dall-e", "dall\u00b7e"]),
    ("anthropic",  ["anthropic", "claude"]),
    ("google",     ["google", "gemini", "deepmind", "alphabet", "bard", "pixel", "waymo"]),
    ("meta",       ["meta ", "llama", "ray-ban", "ray ban", "rayban", "zuckerberg", "instagram", "whatsapp"]),
    ("microsoft",  ["microsoft", "copilot", "azure ai", "satya"]),
    ("apple",      ["apple intelligence", "siri", "apple's ", "iphone ", "macbook", "ios "]),
    ("nvidia",     ["nvidia", "jensen huang", "cuda"]),
    ("xai",        ["xai", "grok", "musk"]),
    ("mistral",    ["mistral"]),
    ("perplexity", ["perplexity"]),
    ("cohere",     ["cohere"]),
    ("amazon",     ["amazon ", "alexa", "aws ", "anthropic\u2019s aws"]),
]


def detect_vendor(candidate: Candidate) -> str | None:
    """Return a vendor slug (openai/anthropic/google/...) or None.

    Match against headline + URL host. First pattern wins.
    """
    hay = f" {candidate.headline.lower()} " + " " + urlparse(candidate.url).netloc.lower()
    for vendor, needles in VENDOR_PATTERNS:
        for n in needles:
            if n in hay:
                return vendor
    return None


def rank_and_select(
    client: Any,
    candidates: list[Candidate],
    archive_fps: set[str],
    rules: dict,
    today: dt.date,
) -> list[Story]:
    # First-pass dedupe by fingerprint
    fresh: list[Candidate] = []
    seen_fps: set[str] = set(archive_fps)
    for c in candidates:
        if c.fingerprint in seen_fps:
            continue
        # Aggregators contribute as signal only — drop them from final selection
        if c.aggregator:
            continue
        seen_fps.add(c.fingerprint)
        fresh.append(c)

    # Pull recent archive headlines for the LLM dedupe sanity check
    archive_titles = recent_archive_headlines(20)

    # Score and slot via LLM
    # Pre-filter: keep at most 4 candidates per source, prefer T1/T2 first.
    fresh.sort(key=lambda c: (c.tier, c.source_name))
    per_source: dict[str, int] = {}
    capped: list[Candidate] = []
    for c in fresh:
        n = per_source.get(c.source_name, 0)
        if n >= 4:
            continue
        per_source[c.source_name] = n + 1
        capped.append(c)
        if len(capped) >= 40:
            break
    candidates_payload = [
        {"id": i, "headline": c.headline, "source": c.source_name,
         "tier": c.tier, "vertical": c.vertical}
        for i, c in enumerate(capped)
    ]
    prompt = RANK_PROMPT_TEMPLATE.format(
        date=today.isoformat(),
        n=len(candidates_payload),
        archive_titles="\n".join(f"- {t}" for t in archive_titles) or "(none)",
        candidates_json=json.dumps(candidates_payload, indent=2),
    )
    ranking_schema = {
        "type": "object",
        "properties": {
            "rankings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer"},
                        "score": {"type": "integer"},
                        "slot": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["id", "score", "slot"],
                },
            }
        },
        "required": ["rankings"],
    }
    raw = call_llm_json(client, RANKING_SYSTEM, prompt, ranking_schema, max_tokens=8000)
    rankings = raw["rankings"] if isinstance(raw, dict) else raw
    # Index rankings — note `capped` is the ordering used for `id`
    rmap = {r["id"]: r for r in rankings if isinstance(r, dict)}
    # Replace `fresh` with `capped` since IDs reference capped order
    fresh = capped
    # Apply mix logic
    is_rotation_day = today.weekday() in rules.get("tier4_rotation_days", [1, 4])
    needed_slots = ["business", "beginner", "engineer"]
    if is_rotation_day:
        needed_slots = ["business", "beginner", "cross"]  # cross displaces engineer on rotation

    selected: dict[str, Candidate] = {}
    vendor_counts: dict[str, int] = {}
    diversity_skips: list[str] = []  # for surface-area in edition notes
    VENDOR_CAP = rules.get("vendor_cap", 2)  # at most 2 stories per vendor

    # Sort candidates by score desc within their declared slot
    ranked = sorted(
        [(rmap[i], fresh[i]) for i in rmap if rmap[i].get("slot") != "skip"],
        key=lambda x: x[0].get("score", 0),
        reverse=True,
    )
    for r, c in ranked:
        slot = r.get("slot")
        if slot not in needed_slots or slot in selected:
            continue
        # Vendor-diversity guard
        vendor = detect_vendor(c)
        if vendor and vendor_counts.get(vendor, 0) >= VENDOR_CAP:
            diversity_skips.append(
                f"skipped {vendor}: {c.headline[:80]}"
            )
            continue
        selected[slot] = c
        if vendor:
            vendor_counts[vendor] = vendor_counts.get(vendor, 0) + 1
        if len(selected) == 3:
            break

    # Fallback: fill any missing slots with the best remaining candidates.
    # Diversity cap still applies, but we relax it as a last resort if no
    # other candidate exists.
    if len(selected) < 3:
        for relax_diversity in (False, True):
            for r, c in ranked:
                if c.fingerprint in {s.fingerprint for s in selected.values()}:
                    continue
                vendor = detect_vendor(c)
                if (
                    not relax_diversity
                    and vendor
                    and vendor_counts.get(vendor, 0) >= VENDOR_CAP
                ):
                    continue
                for slot in needed_slots:
                    if slot not in selected:
                        selected[slot] = c
                        if vendor:
                            vendor_counts[vendor] = vendor_counts.get(vendor, 0) + 1
                        break
                if len(selected) == 3:
                    break
            if len(selected) == 3:
                break

    # Tier 1 minimum check
    if rules.get("tier1_minimum", 1) >= 1 and not any(c.tier == 1 for c in selected.values()):
        # try to swap in a Tier 1 candidate
        for r, c in ranked:
            if c.tier == 1:
                # replace lowest-tier in selected
                worst_slot = max(selected.keys(), key=lambda s: selected[s].tier)
                old = selected[worst_slot]
                old_vendor = detect_vendor(old)
                if old_vendor and vendor_counts.get(old_vendor, 0) > 0:
                    vendor_counts[old_vendor] -= 1
                selected[worst_slot] = c
                new_vendor = detect_vendor(c)
                if new_vendor:
                    vendor_counts[new_vendor] = vendor_counts.get(new_vendor, 0) + 1
                break

    # Stash diversity-skip notes on the function so the caller can include
    # them in edition.notes. We attach to the candidates list via a sentinel
    # — simpler: just store on a module-level for the caller to read.
    rank_and_select._last_diversity_skips = diversity_skips  # type: ignore[attr-defined]
    rank_and_select._last_vendor_counts = vendor_counts  # type: ignore[attr-defined]

    # Rewrite each into a Story
    stories: list[Story] = []
    for slot in needed_slots:
        if slot not in selected:
            continue
        c = selected[slot]
        rewrite_prompt = REWRITE_PROMPT_TEMPLATE.format(
            slot=slot,
            source_name=c.source_name,
            tier=c.tier,
            url=c.url,
            original_headline=c.headline,
            blurb=c.blurb or "(none)",
        )
        rewrite_schema = {
            "type": "object",
            "properties": {
                "headline": {"type": "string"},
                "blurb": {"type": "string"},
                "why_it_matters": {"type": "string"},
            },
            "required": ["headline", "blurb", "why_it_matters"],
        }
        rw = call_llm_json(client, REWRITE_SYSTEM, rewrite_prompt, rewrite_schema, max_tokens=5000)
        stories.append(Story(
            slot=slot,
            headline=rw["headline"],
            blurb=rw["blurb"],
            why_it_matters=rw["why_it_matters"],
            source_name=c.source_name,
            source_url=c.url,
            tier=c.tier,
            original_headline=c.headline,
            fingerprint=c.fingerprint,
        ))
    return stories


def recent_archive_headlines(n: int) -> list[str]:
    if not ARCHIVE_FILE.exists():
        return []
    out: list[str] = []
    with open(ARCHIVE_FILE) as f:
        lines = f.readlines()
    for line in reversed(lines[-15:]):
        try:
            rec = json.loads(line)
            out.extend(rec.get("headlines", []))
        except json.JSONDecodeError:
            continue
        if len(out) >= n:
            break
    return out[:n]


def build_prompt_tile(client: Any, stories: list[Story]) -> dict:
    return _build_prompt_tile_llm(client, call_llm_json, stories)


def build_daily_question(client: Any, stories: list[Story]) -> str:
    themes = ", ".join(s.headline for s in stories)
    schema = {
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    }
    try:
        resp = call_llm_json(
            client,
            REWRITE_SYSTEM,
            DAILY_QUESTION_TEMPLATE.format(themes=themes),
            schema,
            max_tokens=4000,
        )
        return resp["question"]
    except Exception as e:
        print(f"  ! daily-question generation failed: {e}", file=sys.stderr)
        return "What's one thing in today's edition you want to try this week?"


def write_edition(edition: Edition, dry_run: bool = False) -> Path:
    out = EDITIONS_DIR / f"{edition.date}.json"
    payload = {
        "date": edition.date,
        "generated_at": edition.generated_at,
        "model": edition.model,
        "stories": [asdict(s) for s in edition.stories],
        "try_this_prompt": edition.try_this_prompt,
        "daily_question": edition.daily_question,
        "notes": edition.notes,
    }
    trace = getattr(edition, "agent_trace", None)
    if trace:
        payload["agent_trace"] = trace
    copy_issues = validate_edition_stories(payload["stories"])
    if copy_issues:
        print(f"  ! edition copy validation: {copy_issues}", file=sys.stderr)
        payload["notes"] = list(edition.notes) + [f"copy_warnings: {copy_issues}"]

    if not dry_run:
        out.write_text(json.dumps(payload, indent=2))
        if os.environ.get("ESPRESSO_SKIP_ARCHIVE") != "1":
            append_archive(edition)
    return out


def run(date: dt.date, dry_run: bool = False, use_cache: bool = False, mode: str = "agent") -> Path:
    sources, rules = load_sources()
    print(f"[espresso] {date} — {len(sources)} enabled sources", file=sys.stderr)

    candidates = fetch_all_candidates(sources, use_cache=use_cache)
    archive_fps = load_archive(days=rules.get("dedupe_window_days", 30))
    print(f"[espresso] {len(archive_fps)} archived fingerprints (dedupe window)", file=sys.stderr)

    client = Anthropic() if USE_ANTHROPIC else None
    agent_trace: list[dict] = []
    agent_meta: dict = {}
    agent_used = False
    if mode == "agent":
        try:
            from espresso_loop import agentic_select
            archive_titles = recent_archive_headlines(30)
            selected_cands, agent_trace, agent_meta = agentic_select(
                candidates=candidates,
                archive_fps=archive_fps,
                rules=rules,
                today=date,
                vendor_patterns=VENDOR_PATTERNS,
                archive_headlines=archive_titles,
            )
            agent_used = True
            # Rewrite each selected Candidate into a Story using the slot the agent assigned
            stories = []
            for c in selected_cands:
                slot = getattr(c, "_agent_slot", "business")
                rewrite_prompt = REWRITE_PROMPT_TEMPLATE.format(
                    slot=slot,
                    source_name=c.source_name,
                    tier=c.tier,
                    url=c.url,
                    original_headline=c.headline,
                    blurb=c.blurb or "(none)",
                )
                rewrite_schema = {
                    "type": "object",
                    "properties": {
                        "headline": {"type": "string"},
                        "blurb": {"type": "string"},
                        "why_it_matters": {"type": "string"},
                    },
                    "required": ["headline", "blurb", "why_it_matters"],
                }
                rw = call_llm_json(client, REWRITE_SYSTEM, rewrite_prompt, rewrite_schema, max_tokens=5000)
                stories.append(Story(
                    slot=slot,
                    headline=rw["headline"],
                    blurb=rw["blurb"],
                    why_it_matters=rw["why_it_matters"],
                    source_name=c.source_name,
                    source_url=c.url,
                    tier=c.tier,
                    original_headline=c.headline,
                    fingerprint=c.fingerprint,
                ))
            print(f"[espresso] AGENT MODE: selected {len(stories)} stories via {len(agent_trace)} trace events", file=sys.stderr)
        except Exception as e:
            from espresso_loop import AgenticSelectFailed, write_agent_failure_artifact

            if isinstance(e, AgenticSelectFailed):
                fail_path = write_agent_failure_artifact(date, e.trace, e.meta)
                agent_trace = e.trace
                print(
                    f"[espresso] agent failed (no recoverable slate): {e}; trace at {fail_path}",
                    file=sys.stderr,
                )
            else:
                print(f"[espresso] agent mode failed: {e}", file=sys.stderr)
                agent_trace.append({
                    "role": "system",
                    "kind": "error",
                    "result_summary": f"agent mode crashed: {e}",
                })

            # Dev-only: see RANKING_SYSTEM deprecation block above.
            if os.environ.get("ESPRESSO_ALLOW_DETERMINISTIC_FALLBACK") == "1":
                print("[espresso] ESPRESSO_ALLOW_DETERMINISTIC_FALLBACK=1 — rank_and_select", file=sys.stderr)
                stories = rank_and_select(client, candidates, archive_fps, rules, date)
            else:
                raise
    else:
        stories = rank_and_select(client, candidates, archive_fps, rules, date)
    print(f"[espresso] selected {len(stories)} stories", file=sys.stderr)

    edition_notes: list[str] = []
    if agent_used:
        if agent_meta.get("salvaged"):
            edition_notes.append(
                f"agent ship_edition not invoked; salvaged approved slate "
                f"({agent_meta.get('salvage_reason')})"
            )
        editor_notes = (agent_meta.get("editor_notes") or "").strip()
        if editor_notes:
            edition_notes.append(editor_notes)

    edition = Edition(
        date=date.isoformat(),
        stories=stories,
        try_this_prompt=build_prompt_tile(client, stories),
        daily_question=build_daily_question(client, stories),
        generated_at=dt.datetime.utcnow().isoformat() + "Z",
        model=CLAUDE_MODEL,
        notes=edition_notes,
    )
    edition.notes.append(f"mode: {'agent' if agent_used else 'deterministic'}")
    if not any(s.tier == 1 for s in stories):
        edition.notes.append("⚠ No Tier 1 story found — manual review recommended.")

    # Surface vendor-diversity activity (deterministic mode only)
    if not agent_used:
        diversity_skips = getattr(rank_and_select, "_last_diversity_skips", [])
        vendor_counts = getattr(rank_and_select, "_last_vendor_counts", {})
        if vendor_counts:
            edition.notes.append(
                "vendor mix: " + ", ".join(f"{v}={n}" for v, n in sorted(vendor_counts.items()))
            )
        if diversity_skips:
            edition.notes.append(
                f"diversity cap activated — skipped {len(diversity_skips)} candidate(s) over vendor cap"
            )
            for skip in diversity_skips[:5]:
                edition.notes.append(f"  · {skip}")

    if agent_used and agent_meta.get("working_memory"):
        agent_trace.append({
            "role": "system",
            "kind": "memory",
            "result_summary": "final working memory",
            "working_memory": agent_meta["working_memory"],
        })
    edition.agent_trace = agent_trace  # type: ignore[attr-defined]
    path = write_edition(edition, dry_run=dry_run)
    print(f"[espresso] wrote {path}", file=sys.stderr)
    return path


# ───────────────────────────────────────────────────────────────────────
# CLI
# ───────────────────────────────────────────────────────────────────────

def main():
    from load_env import load_env_file
    load_env_file()

    p = argparse.ArgumentParser(description="AI Espresso — daily news agent")
    p.add_argument("--date", default=None, help="ISO date, defaults to today")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--use-cache", action="store_true", help="use cached source HTML")
    p.add_argument(
        "--mode",
        default="agent",
        choices=["agent", "deterministic"],
        help=(
            "agent (Scout→Editor→Critic tool_use loop; production default) or "
            "deterministic (legacy rank_and_select — local dev emergency only, "
            "not used in daily-edition.yml)"
        ),
    )
    args = p.parse_args()

    date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    run(date, dry_run=args.dry_run, use_cache=args.use_cache, mode=args.mode)


if __name__ == "__main__":
    main()
