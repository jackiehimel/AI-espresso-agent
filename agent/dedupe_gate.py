"""Cross-edition repeat rails — keeps already-covered stories out of new editions.

Three layers, all deterministic, enforced as rails (the Editor tool loop still
makes every editorial decision; these only reject repeats):

  1. Canonical URL — same article URL as an archived story (query/fragment and
     `www.` stripped). Catches e.g. the same press release re-fetched with a
     `?utm_source=rss` suffix.
  2. Normalized title — same headline text as an archived story regardless of
     host. Catches syndicated reprints of identical headlines.
  3. Embedding similarity — semantic match against recently shipped headlines.
     Catches the same event re-reported by a new outlet with new wording
     (e.g. "Google will pay SpaceX $920M per month for compute" vs
     "SpaceX Has $30 Billion Deal to Provide Google With A.I. Computing Power").

Layers 1-2 run at pool ingest and at ship. Layer 3 runs only at ship so that
borderline cases stay visible to the Editor, which must unpick and swap.
Layer 3 fails OPEN (gate skipped with a warning) if the embedding API is
unavailable — daily delivery reliability outranks dedupe strictness.
"""

from __future__ import annotations

import datetime as dt
import math
import os
import re
import sys
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

from load_env import strip_wrapping_quotes

# Gemini is the primary embedding provider (GEMINI_API_KEY already exists in
# CI for illustrations; the OpenAI account is billing-inactive as of Jun 2026).
GEMINI_EMBED_MODEL = "gemini-embedding-001"
GEMINI_EMBED_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_EMBED_MODEL}:batchEmbedContents"
)
GEMINI_EMBED_DIMS = 768
OPENAI_EMBED_MODEL = "text-embedding-3-small"
OPENAI_EMBED_URL = "https://api.openai.com/v1/embeddings"

# Calibrated against the full real archive with gemini-embedding-001 @ 768
# dims (see tests/test_dedupe_gate.py, SemanticCalibrationTests). Simulating
# the gate over all 12 shipped editions: every unambiguous repeat (Apple WWDC
# x3 days, Amazon Alexa merch, SpaceX/Google deal) scores 0.837-0.929, while
# every legitimately distinct pair — including same-vendor pairs like
# "Claude Opus 4.8" vs "Claude for Small Business" — scores <= 0.739. One
# ambiguous reframe (Apple-on-Gemini architecture vs the Siri announcement)
# scores 0.771; 0.80 deliberately lets it through — reframed-angle judgment
# stays with the Editor, the rail only blocks unambiguous repeats.
SEMANTIC_THRESHOLD = 0.80

# How far back the exact layers look. Matches the archive dedupe window.
EXACT_WINDOW_DAYS = 30

# Semantic comparisons use a shorter window: re-reports of the same event
# happen within days; a long window only adds false-positive risk.
SEMANTIC_WINDOW_DAYS = 10

_EMBED_CACHE: dict[str, list[float]] = {}


def canonical_url(url: str) -> str:
    """host+path with scheme, query, fragment, `www.` and trailing `/` removed."""
    if not url:
        return ""
    parsed = urlparse(url.strip().lower())
    host = parsed.netloc.removeprefix("www.")
    if not host:
        return ""
    return f"{host}{parsed.path.rstrip('/')}"


def normalize_title(headline: str) -> str:
    """Same normalization as fingerprint_of's title component."""
    return re.sub(r"[^a-z0-9 ]+", "", (headline or "").lower()).strip()


@dataclass
class ArchiveIndex:
    """Dedupe keys from archived editions within the lookback windows."""

    urls: set[str] = field(default_factory=set)
    title_norms: set[str] = field(default_factory=set)
    # Rewritten + original headlines within SEMANTIC_WINDOW_DAYS, for layer 3.
    # Originals matter: a re-fetched press release matches the archived
    # original (0.93) far more strongly than the archived rewrite (0.75).
    semantic_headlines: list[str] = field(default_factory=list)


def build_archive_index(
    records: list[dict],
    today: dt.date,
    days: int = EXACT_WINDOW_DAYS,
    semantic_days: int = SEMANTIC_WINDOW_DAYS,
) -> ArchiveIndex:
    """Build the dedupe index from compacted archive rows.

    Older rows may lack `urls` / `original_headlines`; both default to empty.
    """
    cutoff = (today - dt.timedelta(days=days)).isoformat()
    semantic_cutoff = (today - dt.timedelta(days=semantic_days)).isoformat()
    index = ArchiveIndex()
    for rec in records:
        date = rec.get("date", "")
        if date < cutoff:
            continue
        for url in rec.get("urls") or []:
            cu = canonical_url(url)
            if cu:
                index.urls.add(cu)
        headlines = (rec.get("headlines") or []) + (rec.get("original_headlines") or [])
        for headline in headlines:
            tn = normalize_title(headline)
            if tn:
                index.title_norms.add(tn)
        if date >= semantic_cutoff:
            index.semantic_headlines.extend(h for h in headlines if h and h.strip())
    return index


def exact_repeat_reason(headline: str, url: str, index: ArchiveIndex | None) -> str | None:
    """Layer 1 + 2: return a human-readable reason if this story was already shipped."""
    if index is None:
        return None
    cu = canonical_url(url)
    if cu and cu in index.urls:
        return f"already shipped in a recent edition (same article URL: {cu})"
    tn = normalize_title(headline)
    if tn and tn in index.title_norms:
        return "already shipped in a recent edition (same headline)"
    return None


def _embed_gemini(texts: list[str], api_key: str) -> list[list[float]]:
    resp = httpx.post(
        GEMINI_EMBED_URL,
        headers={"x-goog-api-key": api_key},
        json={"requests": [
            {
                "model": f"models/{GEMINI_EMBED_MODEL}",
                "content": {"parts": [{"text": t}]},
                "outputDimensionality": GEMINI_EMBED_DIMS,
            }
            for t in texts
        ]},
        timeout=30,
    )
    resp.raise_for_status()
    return [row["values"] for row in resp.json()["embeddings"]]


def _embed_openai(texts: list[str], api_key: str) -> list[list[float]]:
    resp = httpx.post(
        OPENAI_EMBED_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": OPENAI_EMBED_MODEL, "input": texts},
        timeout=30,
    )
    resp.raise_for_status()
    rows = sorted(resp.json()["data"], key=lambda r: r["index"])
    return [row["embedding"] for row in rows]


def _embed(texts: list[str]) -> list[list[float]] | None:
    """Batch-embed; returns None when no embedding API key is configured."""
    gemini_key = strip_wrapping_quotes(os.environ.get("GEMINI_API_KEY", ""))
    openai_key = strip_wrapping_quotes(os.environ.get("OPENAI_API_KEY", ""))
    if not gemini_key and not openai_key:
        return None
    missing = [t for t in dict.fromkeys(texts) if t not in _EMBED_CACHE]
    if missing:
        if gemini_key:
            vectors = _embed_gemini(missing, gemini_key)
        else:
            vectors = _embed_openai(missing, openai_key)
        for text, vec in zip(missing, vectors):
            _EMBED_CACHE[text] = vec
    return [_EMBED_CACHE[t] for t in texts]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def semantic_repeats(
    pick_headlines: list[str],
    archive_headlines: list[str],
    threshold: float = SEMANTIC_THRESHOLD,
) -> list[dict] | None:
    """Layer 3: flag picks semantically matching recently shipped headlines.

    Returns a list of {"pick", "matched", "similarity"} hits ([] when clean).
    Returns None when the gate is unavailable (no key / API failure) so the
    caller can fail open.
    """
    picks = [h.strip() for h in pick_headlines if h and h.strip()]
    archive = [h.strip() for h in archive_headlines if h and h.strip()]
    if not picks or not archive:
        return []
    try:
        vectors = _embed(picks + archive)
    except Exception as e:
        print(f"  [dedupe] semantic gate unavailable, failing open: {e}", file=sys.stderr)
        return None
    if vectors is None:
        print("  [dedupe] OPENAI_API_KEY not set; semantic gate skipped", file=sys.stderr)
        return None

    pick_vecs = vectors[: len(picks)]
    archive_vecs = vectors[len(picks):]
    hits: list[dict] = []
    for i, pick in enumerate(picks):
        best_score = 0.0
        best_headline = ""
        for j, archived in enumerate(archive):
            score = _cosine(pick_vecs[i], archive_vecs[j])
            if score > best_score:
                best_score = score
                best_headline = archived
        if best_score >= threshold:
            hits.append({
                "pick": pick,
                "matched": best_headline,
                "similarity": round(best_score, 3),
            })
    return hits
