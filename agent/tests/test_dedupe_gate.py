"""Cross-edition dedupe gate — exact layers, semantic layer, and ship wiring.

Regression context: the 2026-06-09 edition repeated three stories from the
2026-06-07/08 editions (Apple WWDC, Amazon Alexa merch, SpaceX/Google deal).
The exact-layer tests below use those real strings.
"""

import datetime as dt
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dedupe_gate as dg
import espresso_loop as el

TODAY = dt.date(2026, 6, 9)


class CanonicalUrlTests(unittest.TestCase):

    def test_strips_query_fragment_www_trailing_slash(self):
        self.assertEqual(
            dg.canonical_url("https://WWW.aboutamazon.com/news/retail/design-merch/?utm_source=rss#x"),
            "aboutamazon.com/news/retail/design-merch",
        )

    def test_amazon_repeat_pair_collapses(self):
        # Real Jun 8 vs Jun 9 pair: same article, query-string differs.
        a = dg.canonical_url("https://www.aboutamazon.com/news/retail/design-merch-with-ai-alexa-for-shopping")
        b = dg.canonical_url("https://www.aboutamazon.com/news/retail/design-merch-with-ai-alexa-for-shopping?utm_source=rss")
        self.assertEqual(a, b)

    def test_empty_and_garbage_safe(self):
        self.assertEqual(dg.canonical_url(""), "")
        self.assertEqual(dg.canonical_url("not a url"), "")


class ArchiveIndexTests(unittest.TestCase):

    RECORDS = [
        {
            "date": "2026-06-08",
            "headlines": ["Amazon now lets you AI-generate a design and print it on a shirt"],
            "original_headlines": ["How to design custom merch with Alexa for Shopping"],
            "urls": ["https://www.aboutamazon.com/news/retail/design-merch-with-ai-alexa-for-shopping"],
            "fingerprints": ["06d1e87c595cd4ac"],
        },
        {
            "date": "2026-01-01",  # outside 30d window
            "headlines": ["Ancient story"],
            "original_headlines": [],
            "urls": ["https://example.com/old"],
            "fingerprints": [],
        },
    ]

    def test_url_layer_blocks_same_article_with_tracking_params(self):
        index = dg.build_archive_index(self.RECORDS, TODAY)
        reason = dg.exact_repeat_reason(
            "Customers can now design merch with Alexa for Shopping on Amazon",
            "https://www.aboutamazon.com/news/retail/design-merch-with-ai-alexa-for-shopping?utm_source=rss",
            index,
        )
        self.assertIsNotNone(reason)
        self.assertIn("same article URL", reason)

    def test_title_layer_blocks_syndicated_headline_on_new_host(self):
        index = dg.build_archive_index(self.RECORDS, TODAY)
        reason = dg.exact_repeat_reason(
            "How to design custom merch with Alexa for Shopping",
            "https://techcrunch.com/2026/06/09/alexa-merch/",
            index,
        )
        self.assertIsNotNone(reason)
        self.assertIn("same headline", reason)

    def test_fresh_story_passes(self):
        index = dg.build_archive_index(self.RECORDS, TODAY)
        self.assertIsNone(dg.exact_repeat_reason(
            "OpenAI ships a new agents API",
            "https://openai.com/blog/agents-api",
            index,
        ))

    def test_window_excludes_old_rows(self):
        index = dg.build_archive_index(self.RECORDS, TODAY)
        self.assertIsNone(dg.exact_repeat_reason(
            "Ancient story", "https://example.com/old", index,
        ))

    def test_none_index_is_noop(self):
        self.assertIsNone(dg.exact_repeat_reason("x", "https://e.com/x", None))

    def test_rows_missing_new_fields_are_tolerated(self):
        index = dg.build_archive_index(
            [{"date": "2026-06-08", "headlines": ["Some headline"], "fingerprints": []}],
            TODAY,
        )
        self.assertIn(dg.normalize_title("Some headline"), index.title_norms)
        self.assertEqual(index.urls, set())


class SemanticRepeatTests(unittest.TestCase):

    def test_returns_none_without_api_key(self):
        env = {
            k: v for k, v in os.environ.items()
            if k not in ("OPENAI_API_KEY", "GEMINI_API_KEY")
        }
        with mock.patch.dict(os.environ, env, clear=True):
            result = dg.semantic_repeats(["pick"], ["archived"])
        self.assertIsNone(result)

    def test_returns_none_on_api_failure(self):
        with mock.patch.object(dg, "_embed", side_effect=RuntimeError("boom")):
            result = dg.semantic_repeats(["pick"], ["archived"])
        self.assertIsNone(result)

    def test_embed_retries_once_on_transient_http_error(self):
        import httpx
        calls = {"n": 0}

        def flaky(texts, key):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.HTTPError("503")
            return [[1.0, 0.0] for _ in texts]

        with (
            mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}, clear=False),
            mock.patch.object(dg, "_embed_gemini", side_effect=flaky),
            mock.patch.object(dg.time, "sleep"),
            mock.patch.dict(dg._EMBED_CACHE, {}, clear=True),
        ):
            vectors = dg._embed(["headline a"])
        self.assertEqual(calls["n"], 2)
        self.assertEqual(vectors, [[1.0, 0.0]])

    def test_embed_raises_after_second_http_error(self):
        import httpx
        with (
            mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}, clear=False),
            mock.patch.object(dg, "_embed_gemini", side_effect=httpx.HTTPError("503")),
            mock.patch.object(dg.time, "sleep"),
            mock.patch.dict(dg._EMBED_CACHE, {}, clear=True),
        ):
            # Second failure propagates; semantic_repeats fails open on it.
            with self.assertRaises(httpx.HTTPError):
                dg._embed(["headline a"])

    def test_empty_inputs_return_clean(self):
        self.assertEqual(dg.semantic_repeats([], ["a"]), [])
        self.assertEqual(dg.semantic_repeats(["a"], []), [])

    def test_flags_above_threshold_and_passes_below(self):
        vectors = {
            "SpaceX just signed a $30 billion deal to power Google's AI": [1.0, 0.0],
            "Google will pay SpaceX $920M per month for compute": [0.95, 0.31],
            "A totally unrelated robotics story": [0.0, 1.0],
        }
        def fake_embed(texts):
            return [vectors[t] for t in texts]

        with mock.patch.object(dg, "_embed", side_effect=fake_embed):
            hits = dg.semantic_repeats(
                ["SpaceX just signed a $30 billion deal to power Google's AI",
                 "A totally unrelated robotics story"],
                ["Google will pay SpaceX $920M per month for compute"],
                threshold=0.80,
            )
        self.assertEqual(len(hits), 1)
        self.assertIn("SpaceX", hits[0]["pick"])
        self.assertEqual(hits[0]["matched"], "Google will pay SpaceX $920M per month for compute")


def _pick(pid, headline, url="https://example.com/x", tier=1):
    return {
        "id": pid, "headline": headline, "url": url, "tier": tier,
        "body": "Verified excerpt " * 20,
    }


class ShipGateWiringTests(unittest.TestCase):

    def _state(self, picks, archive_headlines=None, archive_index=None):
        return el.AgentState(
            today=TODAY,
            candidates=[],
            archive_headlines=archive_headlines or [],
            archive_index=archive_index,
            picks=picks,
        )

    def test_ship_blocked_on_exact_url_repeat(self):
        index = dg.build_archive_index(ArchiveIndexTests.RECORDS, TODAY)
        picks = {
            "pick_1": _pick(1, "Fresh story one"),
            "pick_2": _pick(2, "Fresh story two", url="https://example.com/y"),
            "pick_3": _pick(
                3, "Alexa designs your merch now",
                url="https://www.aboutamazon.com/news/retail/design-merch-with-ai-alexa-for-shopping?utm_source=rss",
            ),
        }
        gate = el._validate_ship(self._state(picks, archive_index=index))
        self.assertFalse(gate["ok"])
        self.assertTrue(any("same article URL" in e for e in gate["errors"]))

    def test_ship_blocked_on_semantic_repeat(self):
        picks = {
            "pick_1": _pick(1, "SpaceX just signed a $30 billion deal to power Google's AI"),
            "pick_2": _pick(2, "Fresh story two"),
            "pick_3": _pick(3, "Fresh story three"),
        }
        hit = [{
            "pick": "SpaceX just signed a $30 billion deal to power Google's AI",
            "matched": "Google will pay SpaceX $920M per month for compute",
            "similarity": 0.81,
        }]
        with mock.patch.object(el, "semantic_repeats", return_value=hit):
            gate = el._validate_ship(self._state(
                picks, archive_headlines=["Google will pay SpaceX $920M per month for compute"],
            ))
        self.assertFalse(gate["ok"])
        self.assertTrue(any("pick_1" in e and "same story" in e for e in gate["errors"]))

    def test_ship_fails_open_when_semantic_gate_unavailable(self):
        picks = {
            "pick_1": _pick(1, "Anthropic and BlackRock partner on AI for asset management"),
            "pick_2": _pick(2, "ChatGPT can now look at your bank account", tier=2),
            "pick_3": _pick(3, "OpenAI brings its Codex coding app to mobile", tier=2),
        }
        with mock.patch.object(el, "semantic_repeats", return_value=None):
            gate = el._validate_ship(self._state(picks, archive_headlines=["whatever"]))
        self.assertTrue(gate["ok"])

    def test_ship_clean_when_no_repeats(self):
        picks = {
            "pick_1": _pick(1, "Anthropic and BlackRock partner on AI for asset management"),
            "pick_2": _pick(2, "ChatGPT can now look at your bank account", tier=2),
            "pick_3": _pick(3, "OpenAI brings its Codex coding app to mobile", tier=2),
        }
        with mock.patch.object(el, "semantic_repeats", return_value=[]):
            gate = el._validate_ship(self._state(picks, archive_headlines=["old headline"]))
        self.assertTrue(gate["ok"])

    def test_pick_tool_rejects_exact_repeat(self):
        index = dg.build_archive_index(ArchiveIndexTests.RECORDS, TODAY)
        state = self._state({}, archive_index=index)
        state.candidates = [_pick(
            7, "Alexa merch again",
            url="https://www.aboutamazon.com/news/retail/design-merch-with-ai-alexa-for-shopping?utm_source=rss",
        )]
        result = el._tool_pick(
            state, {"id": 7, "reason": "r", "persona": "everyday"}, [],
        )
        self.assertIn("error", result)
        self.assertIn("already shipped", result["error"])


class ArchiveRowFieldsTests(unittest.TestCase):
    """append_archive now persists urls + original_headlines for the gate."""

    def test_append_archive_writes_urls_and_original_headlines(self):
        import tempfile
        import espresso_agent as ea

        edition = ea.Edition(
            date="2099-02-01",
            stories=[ea.Story(
                slot="build", headline="Rewritten headline", blurb="b",
                why_it_matters="w", source_name="Src",
                source_url="https://example.com/article?utm_source=rss",
                tier=1, original_headline="Original RSS headline", fingerprint="fp1",
            )],
            try_this_prompt={}, daily_question="q", generated_at="t",
        )
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "archive.jsonl"
            with mock.patch.object(ea, "ARCHIVE_FILE", archive):
                ea.append_archive(edition)
            row = json.loads(archive.read_text().strip())
        self.assertEqual(row["urls"], ["https://example.com/article?utm_source=rss"])
        self.assertEqual(row["original_headlines"], ["Original RSS headline"])


@unittest.skipUnless(
    os.environ.get("GEMINI_API_KEY") or os.environ.get("OPENAI_API_KEY"),
    "calibration requires an embedding API key (network); run manually",
)
class SemanticCalibrationTests(unittest.TestCase):
    """Threshold guard replaying the gate over the real Jun 2026 repeats.

    Mirrors production exactly: each known repeat pick (original RSS headline)
    is compared against the archived rewritten + original headlines, and must
    be flagged; known distinct picks — including same-vendor follow-ups —
    must NOT be flagged. Measured scores (gemini-embedding-001 @ 768):
    repeats 0.837-0.929, distinct <= 0.739, ambiguous reframe 0.771.
    """

    # Rewritten + original headlines as archived for 2026-06-07/08.
    ARCHIVE = [
        "Google will pay SpaceX $920M per month for compute",
        "Apple ships a new Siri that can actually hold a conversation",
        "Apple announces Siri AI and its next generation of Apple Intelligence",
        "Amazon now lets you AI-generate a design and print it on a shirt",
        "How to design custom merch with Alexa for Shopping",
        "NotebookLM can now run Python code and cite its sources",
        "Do your best research with NotebookLM",
        "Xiaomi's open model now runs at 1,000 tokens per second",
        "Cursor SDK now lets you ship agents that review their own code",
    ]

    # The three repeats that actually shipped on 2026-06-09 (original headlines).
    REPEAT_PICKS = [
        "Apple unveils next generation of Apple Intelligence, Siri AI, and more",
        "Customers can now design merch with Alexa for Shopping on Amazon",
        "SpaceX Has $30 Billion Deal to Provide Google With A.I. Computing Power",
    ]

    # Genuinely new stories, including same-vendor follow-ups, that must pass.
    DISTINCT_PICKS = [
        "Introducing Claude Opus 4.8",                      # vs no Claude in archive
        "Gemini 3.5: frontier intelligence with action",    # vs NotebookLM stories
        "OpenAI Files Confidentially for IPO",              # different company event
        "FrontierCode",                                     # Cognition coding model
        "DeepSeek just made its cheapest prices permanent",
    ]

    def test_gate_flags_real_repeats_and_passes_distinct_stories(self):
        hits = dg.semantic_repeats(self.REPEAT_PICKS + self.DISTINCT_PICKS, self.ARCHIVE)
        self.assertIsNotNone(hits, "embedding API unavailable")
        flagged = {h["pick"] for h in hits}

        print(f"\n  threshold: {dg.SEMANTIC_THRESHOLD}")
        for h in hits:
            print(f"  flagged {h['similarity']}: {h['pick'][:60]}")

        for pick in self.REPEAT_PICKS:
            self.assertIn(pick, flagged, f"known repeat NOT flagged: {pick[:60]}")
        for pick in self.DISTINCT_PICKS:
            self.assertNotIn(pick, flagged, f"distinct story wrongly flagged: {pick[:60]}")


if __name__ == "__main__":
    unittest.main()
