"""Exercise selection -> native editor tools -> rewrite with offline SDK responses."""

import datetime as dt
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import espresso_agent as ea
import espresso_loop as el


def generation_fixture(*, fail_first_editor=False, search_only=False):
    day = dt.date(2026, 10, 18)
    candidates = [
        ea.Candidate(
            headline=f"AI assistant adds useful feature {i}",
            url=f"https://source{i}.example/ai-feature",
            source_name=f"Source {i}", tier=1 if i == 0 else 2,
            blurb=("RAW_RSS_DUPLICATION " * 6000) if i == 0 else "",
            published_date=day.isoformat(),
        ) for i in range(4)
    ]
    enriched = [
        {"id": i, "headline": c.headline, "url": c.url,
         "source": c.source_name, "tier": c.tier, "blurb": c.blurb,
         "paywall": i == 3, "published_date": day.isoformat(),
         "body_source": "article",
         "body": (f"VERIFIED_EVIDENCE_{i} describes this AI feature accurately. " * 60)[:2500]}
        for i, c in enumerate(candidates)
    ]
    if search_only:
        enriched[-1]["id"] = 10000
    requests, rewrites, selected = [], [], []
    editor_calls = 0
    select = el.agentic_select

    def record_selection(*args, **kwargs):
        value = select(*args, **kwargs)
        selected.extend(value[0])
        return value

    def create(**kwargs):
        nonlocal editor_calls
        requests.append({
            "model": kwargs["model"], "max_tokens": kwargs["max_tokens"],
            "payload_chars": len(json.dumps(kwargs, default=lambda obj: vars(obj))),
            "kind": "editor" if kwargs.get("tools") else "json",
        })
        if kwargs.get("tools"):
            editor_calls += 1
            if fail_first_editor and editor_calls == 1:
                raise RuntimeError("offline transient editor failure")
            blocks = [SimpleNamespace(
                type="tool_use", name="pick", id=f"pick-{i}",
                input={"id": entry["id"], "reason": "verified useful AI feature", "persona": "build"},
            ) for i, entry in enumerate(enriched)]
            blocks.append(SimpleNamespace(type="tool_use", name="ship_edition", id="ship", input={}))
            return SimpleNamespace(content=blocks)
        prompt = kwargs["messages"][0]["content"]
        if prompt.startswith("Rewrite this story"):
            rewrites.append(prompt)
            value = {"headline": "AI assistant adds a useful feature", "blurb": "A verified capability is now available.", "why_it_matters": "Readers can try the new feature."}
        else:
            value = {"ranked": [{"id": i, "score": 90} for i in range(4)], "gaps": []}
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(value))])

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    with (
        mock.patch.dict(ea.os.environ, {"ESPRESSO_AGENT_MAX_ATTEMPTS": "2", "ESPRESSO_ALLOW_DETERMINISTIC_FALLBACK": "0"}),
        mock.patch.object(ea, "USE_ANTHROPIC", True),
        mock.patch.object(ea, "Anthropic", return_value=client, create=True),
        mock.patch("anthropic.Anthropic", return_value=client),
        mock.patch.object(ea, "load_sources", return_value=([], {})),
        mock.patch.object(ea, "fetch_all_candidates", return_value=candidates),
        mock.patch.object(ea, "load_archive", return_value=set()),
        mock.patch.object(ea, "_load_archive_records_compacted", return_value=[]),
        mock.patch.object(ea, "recent_archive_headlines", return_value=[]),
        mock.patch.object(el, "discover_via_search", return_value=[]) as discovery,
        mock.patch.object(el, "prefetch_bodies", return_value=enriched) as prefetch,
        mock.patch.object(el, "agentic_select", side_effect=record_selection),
        mock.patch.object(el, "write_agent_failure_artifact", return_value=Path("offline-failure.json")),
        mock.patch.object(ea, "build_prompt_tile", return_value={"prompt": "offline prompt"}),
        mock.patch.object(ea, "build_daily_question", return_value="Offline question?"),
    ):
        ea.run(day, dry_run=True, mode="agent")
    return {"requests": requests, "rewrites": rewrites, "candidates": candidates, "selected": selected,
            "enriched": enriched, "discovery_calls": discovery.call_count,
            "prefetch_calls": prefetch.call_count}


class SelectedEvidenceTests(unittest.TestCase):
    def test_rewrite_uses_verified_excerpt_not_full_rss_or_empty_summary(self):
        run = generation_fixture()
        self.assertEqual(len(run["rewrites"]), 4)
        for i, prompt in enumerate(run["rewrites"]):
            self.assertTrue(run["enriched"][i]["body"] in prompt, "rewrite lost verified article evidence")
            self.assertNotIn("RAW_RSS_DUPLICATION", prompt)
            self.assertLess(len(prompt), 3500)
        self.assertEqual(run["candidates"][0].blurb, "RAW_RSS_DUPLICATION " * 6000)

    def test_search_only_pick_retains_verified_excerpt_for_rewrite(self):
        run = generation_fixture(search_only=True)
        self.assertTrue(run["enriched"][-1]["body"] in run["rewrites"][-1], "search pick lost verified evidence")
        self.assertEqual(run["selected"][-1].published_date, "2026-10-18")
        self.assertTrue(run["selected"][-1].paywall)


if __name__ == "__main__":
    unittest.main()
