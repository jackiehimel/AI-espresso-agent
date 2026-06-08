"""
AI Espresso demo server.

Endpoints:
  GET  /                — landing page
  POST /api/try         — rewrite latest stories via Claude (live demo)
  GET  /api/editions    — JSON list of available editions
  GET  /api/trace/{n}   — agent trace data for a given edition
  GET  /edition/{n}     — rendered edition HTML
  GET  /editions/...    — static files (images, assets)
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

AGENT_DIR = Path(__file__).resolve().parent.parent / "agent"
sys.path.insert(0, str(AGENT_DIR))

from load_env import load_env_file
load_env_file(AGENT_DIR / ".env")

app = FastAPI(title="AI Espresso Demo", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

EDITIONS_DIR = Path(__file__).resolve().parent.parent / "editions"
DATA_EDITIONS_DIR = (
    Path(__file__).resolve().parent.parent / "agent" / "data" / "editions"
)

app.mount("/editions", StaticFiles(directory=str(EDITIONS_DIR)), name="editions")


def _edition_json_by_issue() -> dict[int, dict]:
    """Build a map of issue_num → parsed edition JSON."""
    result = {}
    for jf in DATA_EDITIONS_DIR.glob("*.json"):
        try:
            data = json.loads(jf.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        num = data.get("issue_num")
        if num is not None:
            result[int(num)] = data
    return result


def _list_editions() -> list[dict]:
    """Scan available editions, return metadata sorted newest-first."""
    by_issue = _edition_json_by_issue()
    editions = []
    for html_path in sorted(EDITIONS_DIR.glob("edition_*_variant_c.html"), reverse=True):
        match = re.search(r"edition_(\d+)", html_path.name)
        if not match:
            continue
        num = int(match.group(1))
        data = by_issue.get(num)

        if data:
            stories = data.get("stories", [])
            editions.append({
                "number": num,
                "date": data.get("date", ""),
                "headline": stories[0]["headline"] if stories else "",
                "story_count": len(stories),
            })
        else:
            headline = ""
            date_str = ""
            try:
                content = html_path.read_text(errors="ignore")
                m = re.search(r'<span style="display:none">([^<]+)</span>', content)
                if m:
                    headline = m.group(1).strip()
                m2 = re.search(r'content="[A-Z]+\s*·\s*([^"]+)"', content)
                if m2:
                    date_str = m2.group(1).strip()
            except OSError:
                pass
            editions.append({
                "number": num,
                "date": date_str or f"Edition {num}",
                "headline": headline,
                "story_count": 0,
            })
    return editions


@app.get("/api/editions")
async def api_editions():
    return JSONResponse({"editions": _list_editions()})


@app.get("/api/trace/{edition_number}")
async def api_trace(edition_number: int):
    """Return the agent trace + story data for the guided tour."""
    by_issue = _edition_json_by_issue()
    data = by_issue.get(edition_number)
    if not data:
        raise HTTPException(404, "Edition data not found.")

    stories = data.get("stories", [])
    trace = data.get("agent_trace", [])
    prompt_tile = data.get("try_this_prompt", {})
    daily_q = data.get("daily_question", "")

    picks = [
        {
            "original_headline": e.get("args", {}).get("id", ""),
            "persona": e.get("args", {}).get("persona", ""),
            "reason": e.get("args", {}).get("reason", ""),
        }
        for e in trace
        if e.get("kind") == "tool_call" and e.get("name") == "pick"
    ]

    handoff = next((e for e in trace if e.get("kind") == "handoff"), {})
    summary = handoff.get("result_summary", "")

    image_paths = []
    assets_dir = EDITIONS_DIR / f"edition_{edition_number}" / "assets"
    if assets_dir.is_dir():
        image_paths = sorted(
            f"/editions/edition_{edition_number}/assets/{p.name}"
            for p in assets_dir.glob("variant_c_*.png")
        )

    return JSONResponse({
        "edition_number": edition_number,
        "date": data.get("date", ""),
        "pipeline_summary": summary,
        "picks": picks,
        "stories": [
            {
                "headline": s["headline"],
                "blurb": s["blurb"],
                "why_it_matters": s["why_it_matters"],
                "source": s["source_name"],
                "url": s["source_url"],
                "original_headline": s["original_headline"],
            }
            for s in stories
        ],
        "images": image_paths,
        "prompt_tile": prompt_tile,
        "daily_question": daily_q,
    })


DEMO_REWRITE_SYSTEM = (
    "You write headlines and blurbs for AI Espresso, a concise daily AI news briefing. "
    "Rewrite each story with a sharp, conversational tone. No jargon. No hype. "
    "Return a JSON array. Each element: {\"headline\": ..., \"blurb\": (50 words max), "
    "\"why_it_matters\": (1 sentence)}. Return ONLY the JSON array, no markdown fences."
)


def _load_latest_candidates() -> list[dict]:
    """Pull candidate stories from the most recent edition JSON."""
    for jf in sorted(DATA_EDITIONS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(jf.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        stories = data.get("stories", [])
        if stories:
            return [
                {
                    "headline": s["original_headline"],
                    "source": s["source_name"],
                    "url": s["source_url"],
                    "blurb": s.get("blurb", ""),
                }
                for s in stories
            ]
    return []


@app.post("/api/try")
async def try_agent():
    """Rewrite the latest edition's stories via Claude."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(503, "ANTHROPIC_API_KEY not configured.")

    candidates = _load_latest_candidates()
    if not candidates:
        raise HTTPException(404, "No candidate stories available.")

    from anthropic import Anthropic
    client = Anthropic(api_key=api_key)

    batch_prompt = "Rewrite these stories for AI Espresso:\n\n"
    for i, c in enumerate(candidates, 1):
        batch_prompt += f"{i}. {c['headline']} ({c['source']})\n   {c['blurb'][:150]}\n\n"

    resp = client.messages.create(
        model=os.environ.get("ESPRESSO_MODEL", "claude-sonnet-4-5"),
        max_tokens=2000,
        system=DEMO_REWRITE_SYSTEM,
        messages=[{"role": "user", "content": batch_prompt}],
    )
    text = resp.content[0].text.strip()
    start = text.find("[")
    end = text.rfind("]") + 1
    if start >= 0 and end > start:
        text = text[start:end]

    try:
        rewrites = json.loads(text)
    except json.JSONDecodeError:
        rewrites = [{"headline": c["headline"], "blurb": c["blurb"], "why_it_matters": ""} for c in candidates]

    stories = []
    for c, rw in zip(candidates, rewrites):
        stories.append({
            "headline": rw.get("headline", c["headline"]),
            "blurb": rw.get("blurb", c["blurb"]),
            "why_it_matters": rw.get("why_it_matters", ""),
            "source": c["source"],
            "url": c["url"],
            "original_headline": c["headline"],
        })

    return JSONResponse({"stories": stories})


@app.get("/edition/{number}", response_class=HTMLResponse)
async def serve_edition(number: int):
    html_path = EDITIONS_DIR / f"edition_{number}_variant_c.html"
    if not html_path.is_file():
        raise HTTPException(404, f"Edition {number} not found.")
    content = html_path.read_text()
    content = content.replace(
        f'src="edition_{number}/',
        f'src="/editions/edition_{number}/',
    )
    return HTMLResponse(content)


LANDING_PAGE = Path(__file__).parent / "index.html"


@app.get("/", response_class=HTMLResponse)
async def landing():
    if LANDING_PAGE.is_file():
        return HTMLResponse(LANDING_PAGE.read_text())
    return HTMLResponse("<h1>AI Espresso</h1><p>Frontend not found.</p>")
