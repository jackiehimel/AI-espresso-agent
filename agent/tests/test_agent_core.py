# tests for the regression-prone bits of espresso_agent / espresso_loop:
#   - fingerprint_of: dedupe key stability across cosmetic title/url drift
#   - detect_vendor: vendor cap depends on this; needs to be wrong-tolerant
#   - search allow-list: prevents kleap.co-class SEO listicles from sneaking in
#   - tier-1 ship gate: blocks shipping without tier-1 pick
#
# run from agent/ dir:
#   python -m unittest tests.test_agent_core
# no pytest dep; stdlib only so cron runner doesn't need extras.

import sys
import unittest
from pathlib import Path

# make agent/ importable when tests run from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import espresso_agent as ea
import espresso_loop as el


class FingerprintTests(unittest.TestCase):

    # same story from two different sources should *not* dedupe \u2014 host is
    # part of the key on purpose so we don't suppress legitimate coverage
    def test_different_hosts_distinct(self):
        a = ea.fingerprint_of("OpenAI ships agents API", "https://openai.com/x")
        b = ea.fingerprint_of("OpenAI ships agents API", "https://theverge.com/y")
        self.assertNotEqual(a, b)

    # cosmetic title drift (case, punctuation, trailing whitespace) on same
    # host should collapse to one fingerprint
    def test_title_normalization(self):
        a = ea.fingerprint_of("OpenAI Ships Agents API!", "https://openai.com/x")
        b = ea.fingerprint_of("openai ships agents api ", "https://openai.com/x")
        self.assertEqual(a, b)

    # same headline + same host but different path should still match.
    # this is intentional \u2014 source republishes the same press release at
    # multiple URLs all the time and we treat them as one event.
    def test_same_host_different_path(self):
        a = ea.fingerprint_of("Claude 3.5 launches", "https://anthropic.com/news/a")
        b = ea.fingerprint_of("Claude 3.5 launches", "https://anthropic.com/blog/b")
        self.assertEqual(a, b)


class VendorDetectionTests(unittest.TestCase):

    def _cand(self, headline: str, url: str) -> ea.Candidate:
        return ea.Candidate(
            headline=headline, url=url, source_name="x", tier=1
        )

    def test_openai_by_host(self):
        c = self._cand("New model dropped", "https://openai.com/news/foo")
        self.assertEqual(ea.detect_vendor(c), "openai")

    def test_anthropic_by_headline(self):
        c = self._cand("Anthropic launches Claude 3.5", "https://example.com/x")
        self.assertEqual(ea.detect_vendor(c), "anthropic")

    # google/deepmind/gemini all roll up to one vendor for the cap \u2014 if
    # this regresses, vendor cap stops working for google stories
    def test_google_family_collapses(self):
        for headline in ("DeepMind unveils X", "Gemini 3 lands", "Google's new AI"):
            with self.subTest(headline=headline):
                c = self._cand(headline, "https://example.com/x")
                self.assertEqual(ea.detect_vendor(c), "google")

    def test_unknown_vendor_returns_none(self):
        c = self._cand("Some random startup launches thing", "https://example.com/x")
        self.assertIsNone(ea.detect_vendor(c))


class SearchAllowlistTests(unittest.TestCase):

    # sample edition 2026-05-21 picked a kleap.co listicle; that exact
    # failure mode must stay blocked
    def test_kleap_blocked(self):
        allowed = ea.search_allowed_domains()
        self.assertFalse(
            ea.is_search_domain_allowed("https://kleap.co/blog/best-ai-apps", allowed)
        )

    def test_quality_press_allowed(self):
        allowed = ea.search_allowed_domains()
        for url in (
            "https://www.theverge.com/anthropic",
            "https://techcrunch.com/2026/openai",
            "https://www.bloomberg.com/news/x",
            "https://arxiv.org/abs/2401.12345",
        ):
            with self.subTest(url=url):
                self.assertTrue(ea.is_search_domain_allowed(url, allowed))

    # source allow-list pulled from sources.yaml \u2014 if config drifts the
    # daily-fetch hosts should still be searchable
    def test_configured_sources_allowed(self):
        allowed = ea.search_allowed_domains()
        self.assertTrue(ea.is_search_domain_allowed("https://anthropic.com/news", allowed))
        self.assertTrue(ea.is_search_domain_allowed("https://openai.com/news/x", allowed))

    def test_random_blog_blocked(self):
        allowed = ea.search_allowed_domains()
        self.assertFalse(
            ea.is_search_domain_allowed("https://medium.com/@x/ai-tools", allowed)
        )
        self.assertFalse(
            ea.is_search_domain_allowed("https://buzzfeed.com/ai-tools", allowed)
        )


class Tier1ShipGateTests(unittest.TestCase):

    def test_blocks_ship_when_no_tier1(self):
        _, rules = ea.load_sources()
        state = el.AgentState(
            today=__import__("datetime").date(2026, 5, 17),
            needed_slots=["business", "beginner", "engineer"],
            shortlist=[],
            candidates_by_id={},
            archive_headlines=[],
            picks={
                "business": {"id": 1, "headline": "x", "url": "u", "tier": 2},
                "beginner": {"id": 2, "headline": "y", "url": "u", "tier": 2},
                "engineer": {"id": 3, "headline": "z", "url": "u", "tier": 2},
            },
            last_critic_verdict={"verdict": "approve", "reason": "ok"},
        )
        gate = el.validate_ship_gates(state, rules)
        self.assertFalse(gate["ok"])

    def test_allows_ship_with_one_tier1(self):
        _, rules = ea.load_sources()
        state = el.AgentState(
            today=__import__("datetime").date(2026, 5, 17),
            needed_slots=["business", "beginner", "engineer"],
            shortlist=[],
            candidates_by_id={},
            archive_headlines=[],
            picks={
                "business": {
                    "id": 1,
                    "headline": "Anthropic and BlackRock partner on AI for asset management",
                    "url": "u",
                    "tier": 1,
                    "body": "Verified excerpt " * 20,
                },
                "beginner": {
                    "id": 2,
                    "headline": "ChatGPT can now look at your bank account",
                    "url": "u",
                    "tier": 2,
                    "body": "Verified excerpt " * 20,
                },
                "engineer": {
                    "id": 3,
                    "headline": "OpenAI brings its Codex coding app to mobile",
                    "url": "u",
                    "tier": 2,
                    "body": "Verified excerpt " * 20,
                },
            },
            last_critic_verdict={"verdict": "approve", "reason": "ok"},
        )
        gate = el.validate_ship_gates(state, rules)
        self.assertTrue(gate["ok"])


class RssSummaryTests(unittest.TestCase):

    def test_rss_item_summary_uses_bs4_not_findtext(self):
        xml = """<?xml version="1.0"?>
        <rss><channel><item>
          <title>NYT AI story headline</title>
          <link>https://nytimes.com/ai-story</link>
          <description><![CDATA[<p>Paywalled lede here.</p>]]></description>
        </item></channel></rss>"""
        source = ea.Source(
            name="NYT",
            url="https://nytimes.com/rss",
            tier=1,
            kind="rss",
            paywall=True,
        )
        cands = ea.extract_rss_candidates(xml, source, max_n=1)
        self.assertEqual(len(cands), 1)
        self.assertIn("Paywalled lede", cands[0].blurb)


if __name__ == "__main__":
    unittest.main()
