"""Unit tests for the Hybrid Discovery Agent pipeline (no LLM calls)."""

import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import espresso_loop as el


def _verified_body(chars: int = 150) -> str:
    return "Verified article excerpt. " * (chars // 25 + 1)


def _state(**kwargs) -> el.AgentState:
    defaults = dict(
        today=dt.date(2026, 5, 17),
        candidates=[],
        archive_headlines=[],
    )
    defaults.update(kwargs)
    return el.AgentState(**defaults)


def _candidate(id: int, headline: str, source: str = "TestSource", tier: int = 1,
               url: str = "https://example.com", body: str = "", **extra) -> dict:
    d = {
        "id": id, "headline": headline, "source": source, "tier": tier,
        "url": url, "body": body, "body_source": "article" if body else "",
        "blurb": "", "paywall": False, "vertical": None,
    }
    d.update(extra)
    return d


VENDOR_PATTERNS = [
    ("openai", ["openai", "chatgpt", "gpt-4", "gpt-5", "codex"]),
    ("anthropic", ["anthropic", "claude"]),
    ("google", ["google", "gemini", "deepmind"]),
    ("meta", ["meta ", "llama"]),
]


class PickTests(unittest.TestCase):

    def test_pick_succeeds_with_body(self):
        c = _candidate(1, "ChatGPT can now look at your bank account", body=_verified_body())
        state = _state(candidates=[c])
        r = el._tool_pick(state, {"id": 1, "reason": "exciting", "persona": "everyday"}, VENDOR_PATTERNS)
        self.assertTrue(r["ok"])
        self.assertEqual(r["pick_count"], 1)

    def test_pick_rejects_no_body(self):
        c = _candidate(1, "Story without body")
        state = _state(candidates=[c])
        r = el._tool_pick(state, {"id": 1, "reason": "test", "persona": "market"}, VENDOR_PATTERNS)
        self.assertIn("error", r)
        self.assertIn("body", r["error"])

    def test_pick_rejects_duplicate(self):
        c = _candidate(1, "ChatGPT feature", body=_verified_body())
        state = _state(candidates=[c])
        el._tool_pick(state, {"id": 1, "reason": "first", "persona": "market"}, VENDOR_PATTERNS)
        r = el._tool_pick(state, {"id": 1, "reason": "again", "persona": "build"}, VENDOR_PATTERNS)
        self.assertIn("error", r)
        self.assertIn("already", r["error"])

    def test_vendor_cap_enforced(self):
        c1 = _candidate(1, "OpenAI launches feature A", body=_verified_body())
        c2 = _candidate(2, "OpenAI launches feature B", body=_verified_body())
        c3 = _candidate(3, "OpenAI launches feature C", body=_verified_body())
        state = _state(candidates=[c1, c2, c3])
        el._tool_pick(state, {"id": 1, "reason": "a", "persona": "market"}, VENDOR_PATTERNS)
        el._tool_pick(state, {"id": 2, "reason": "b", "persona": "build"}, VENDOR_PATTERNS)
        r = el._tool_pick(state, {"id": 3, "reason": "c", "persona": "everyday"}, VENDOR_PATTERNS)
        self.assertIn("error", r)
        self.assertIn("vendor cap", r["error"])

    def test_max_picks_enforced(self):
        candidates = [
            _candidate(i, f"Story {i}", body=_verified_body(), source=f"Source{i}")
            for i in range(7)
        ]
        state = _state(candidates=candidates)
        for i in range(6):
            el._tool_pick(state, {"id": i, "reason": "fill", "persona": "market"}, [])
        r = el._tool_pick(state, {"id": 6, "reason": "overflow", "persona": "market"}, [])
        self.assertIn("error", r)
        self.assertEqual(len(state.picks), 6)

    def test_pick_after_unpick_does_not_overwrite_existing_slot(self):
        """Regression: 2026-06-14 edition shipped only 3 stories because pick
        after unpick reused an in-use slot key and silently overwrote a sibling
        pick. Slots must be monotonic across the lifetime of the state.
        """
        candidates = [
            _candidate(i, f"Story {i}", body=_verified_body(), source=f"Outlet{i}")
            for i in range(6)
        ]
        state = _state(candidates=candidates)
        for i in range(4):
            el._tool_pick(state, {"id": i, "reason": "fill", "persona": "market"}, [])
        self.assertEqual(sorted(state.picks), ["pick_1", "pick_2", "pick_3", "pick_4"])

        el._tool_unpick(state, {"id": 0}, [])
        self.assertEqual(sorted(state.picks), ["pick_2", "pick_3", "pick_4"])

        r1 = el._tool_pick(state, {"id": 4, "reason": "replacement A", "persona": "build"}, [])
        self.assertTrue(r1.get("ok"))
        self.assertEqual(r1["pick_count"], 4)
        self.assertIn("pick_5", state.picks)
        self.assertEqual(int(state.picks["pick_5"]["id"]), 4)
        self.assertEqual(int(state.picks["pick_4"]["id"]), 3, "pick_4 must not be overwritten")

        r2 = el._tool_pick(state, {"id": 5, "reason": "replacement B", "persona": "industry"}, [])
        self.assertTrue(r2.get("ok"))
        self.assertEqual(r2["pick_count"], 5)
        self.assertIn("pick_6", state.picks)
        self.assertEqual(int(state.picks["pick_6"]["id"]), 5)

        ids = {int(p["id"]) for p in state.picks.values()}
        self.assertEqual(ids, {1, 2, 3, 4, 5})

    def test_source_cap_enforced(self):
        """No more than SOURCE_CAP stories from the same outlet (2026-06-14
        shipped 3 Wired stories — that must be rejected).
        """
        candidates = [
            _candidate(i, f"AI story {i}", body=_verified_body(), source="Wired — AI")
            for i in range(3)
        ]
        state = _state(candidates=candidates)
        r1 = el._tool_pick(state, {"id": 0, "reason": "a", "persona": "industry"}, [])
        r2 = el._tool_pick(state, {"id": 1, "reason": "b", "persona": "everyday"}, [])
        r3 = el._tool_pick(state, {"id": 2, "reason": "c", "persona": "build"}, [])
        self.assertTrue(r1.get("ok"))
        self.assertTrue(r2.get("ok"))
        self.assertIn("error", r3)
        self.assertIn("source cap", r3["error"])
        self.assertEqual(len(state.picks), 2)

    def test_source_cap_treats_outlet_case_insensitively(self):
        candidates = [
            _candidate(0, "Story A", body=_verified_body(), source="TechCrunch"),
            _candidate(1, "Story B", body=_verified_body(), source="techcrunch"),
            _candidate(2, "Story C", body=_verified_body(), source="TECHCRUNCH"),
        ]
        state = _state(candidates=candidates)
        el._tool_pick(state, {"id": 0, "reason": "a", "persona": "market"}, [])
        el._tool_pick(state, {"id": 1, "reason": "b", "persona": "build"}, [])
        r = el._tool_pick(state, {"id": 2, "reason": "c", "persona": "industry"}, [])
        self.assertIn("error", r)
        self.assertIn("source cap", r["error"])

    def test_source_cap_unpick_frees_slot(self):
        candidates = [
            _candidate(i, f"Story {i}", body=_verified_body(), source="Bloomberg")
            for i in range(3)
        ]
        state = _state(candidates=candidates)
        el._tool_pick(state, {"id": 0, "reason": "a", "persona": "market"}, [])
        el._tool_pick(state, {"id": 1, "reason": "b", "persona": "build"}, [])
        el._tool_unpick(state, {"id": 0}, [])
        r = el._tool_pick(state, {"id": 2, "reason": "c", "persona": "industry"}, [])
        self.assertTrue(r.get("ok"), r)


class SearchNewsFreshnessTests(unittest.TestCase):
    """In-loop search_news must age-gate hits like the discovery prefetch so the
    editor can't pick undated or stale (>max_age) web results (2026-06-15).
    """

    def _patch_search(self, hits):
        orig = el._perplexity_search
        el._perplexity_search = lambda query: (hits, None)
        self.addCleanup(lambda: setattr(el, "_perplexity_search", orig))

    def test_search_news_drops_undated_and_stale_hits(self):
        self._patch_search([
            {"title": "Fresh", "url": "https://www.theverge.com/2026/05/16/fresh",
             "snippet": "x", "domain": "theverge.com"},
            {"title": "Stale", "url": "https://www.theverge.com/2026/05/01/stale",
             "snippet": "x", "domain": "theverge.com"},
            {"title": "Undated", "url": "https://www.theverge.com/undated",
             "snippet": "no date here", "domain": "theverge.com"},
        ])
        state = _state(today=dt.date(2026, 5, 17))  # max_age_days defaults to 3
        r = el._tool_search_news(state, {"query": "ai"})
        self.assertEqual(r["added"], 1)
        self.assertEqual(r.get("dropped_stale"), 2)
        self.assertEqual(len(state.extra_candidates), 1)
        kept = state.extra_candidates[0]
        self.assertEqual(kept["published_date"], "2026-05-16")
        self.assertTrue(kept["via_search"])


class UnpickTests(unittest.TestCase):

    def test_unpick_removes_pick(self):
        c = _candidate(1, "Test story", body=_verified_body())
        state = _state(candidates=[c])
        el._tool_pick(state, {"id": 1, "reason": "test", "persona": "market"}, VENDOR_PATTERNS)
        self.assertEqual(len(state.picks), 1)
        r = el._tool_unpick(state, {"id": 1}, VENDOR_PATTERNS)
        self.assertTrue(r["ok"])
        self.assertEqual(len(state.picks), 0)

    def test_unpick_decrements_vendor_count(self):
        c = _candidate(1, "OpenAI feature", body=_verified_body())
        state = _state(candidates=[c])
        el._tool_pick(state, {"id": 1, "reason": "test", "persona": "market"}, VENDOR_PATTERNS)
        self.assertEqual(state.vendor_counts.get("openai"), 1)
        el._tool_unpick(state, {"id": 1}, VENDOR_PATTERNS)
        self.assertEqual(state.vendor_counts.get("openai", 0), 0)


class ShipGateTests(unittest.TestCase):

    def _make_state_with_picks(self, n=3):
        candidates = [
            _candidate(i, f"AI story {i} with clear AI hook", body=_verified_body(),
                       source=f"Source{i}", tier=1 if i == 0 else 2)
            for i in range(n)
        ]
        state = _state(candidates=candidates)
        for i in range(n):
            el._tool_pick(state, {"id": i, "reason": "test", "persona": "market"}, [])
        return state

    def test_ship_succeeds_with_4_valid_picks(self):
        state = self._make_state_with_picks(4)
        r = el._tool_ship(state, {})
        self.assertTrue(r["shipped"])

    def test_ship_fails_with_odd_5_picks(self):
        state = self._make_state_with_picks(5)
        r = el._tool_ship(state, {})
        self.assertFalse(r["shipped"])
        self.assertTrue(any("4 or 6" in e for e in r["errors"]))

    def test_ship_fails_with_2_picks(self):
        state = self._make_state_with_picks(2)
        r = el._tool_ship(state, {})
        self.assertFalse(r["shipped"])
        self.assertTrue(any("need" in e for e in r["errors"]))

    def test_ship_fails_without_tier1(self):
        candidates = [
            _candidate(i, f"AI story {i} with clear AI hook", body=_verified_body(),
                       source=f"Source{i}", tier=2)
            for i in range(3)
        ]
        state = _state(candidates=candidates)
        for i in range(3):
            el._tool_pick(state, {"id": i, "reason": "test", "persona": "market"}, [])
        r = el._tool_ship(state, {})
        self.assertFalse(r["shipped"])
        self.assertTrue(any("tier-1" in e for e in r["errors"]))

    def test_ship_rejects_failure_framing(self):
        candidates = [
            _candidate(0, "AI glitch ruins everything", body=_verified_body(),
                       tier=1, source="OutletA"),
            _candidate(1, "AI agent ships code", body=_verified_body(), source="OutletB"),
            _candidate(2, "ChatGPT launches feature", body=_verified_body(), source="OutletC"),
        ]
        state = _state(candidates=candidates)
        for i in range(3):
            el._tool_pick(state, {"id": i, "reason": "test", "persona": "market"}, [])
        r = el._tool_ship(state, {})
        self.assertFalse(r["shipped"])
        self.assertTrue(any("constitution" in e for e in r["errors"]))

    def test_ship_allows_one_non_load_bearing(self):
        candidates = [
            _candidate(0, "Raspberry Pi profit forecast", body=_verified_body(),
                       tier=1, source="OutletA"),
            _candidate(1, "ChatGPT launches new AI feature", body=_verified_body(),
                       source="OutletB"),
            _candidate(2, "Claude agent ships code autonomously", body=_verified_body(),
                       source="OutletC"),
            _candidate(3, "Gemini adds a new reasoning mode", body=_verified_body(),
                       source="OutletD"),
        ]
        state = _state(candidates=candidates)
        for i in range(4):
            el._tool_pick(state, {"id": i, "reason": "test", "persona": "market"}, [])
        r = el._tool_ship(state, {})
        self.assertTrue(r["shipped"])

    def test_ship_rejects_stale_pick(self):
        today = dt.date(2026, 5, 17)
        stale = (today - dt.timedelta(days=5)).isoformat()
        candidates = [
            _candidate(0, "ChatGPT launches stale AI feature", body=_verified_body(),
                       tier=1, source="OutletA", published_date=stale),
            _candidate(1, "Claude agent ships fresh AI feature", body=_verified_body(),
                       source="OutletB"),
            _candidate(2, "Gemini releases new AI capability", body=_verified_body(),
                       source="OutletC"),
        ]
        state = _state(candidates=candidates, today=today, max_age_days=3)
        for i in range(3):
            el._tool_pick(state, {"id": i, "reason": "test", "persona": "market"}, [])
        r = el._tool_ship(state, {})
        self.assertFalse(r["shipped"])
        self.assertTrue(
            any("too old" in e and "5 days" in e for e in r["errors"]),
            r["errors"],
        )

    def test_ship_accepts_fresh_and_undated(self):
        today = dt.date(2026, 5, 17)
        fresh = (today - dt.timedelta(days=2)).isoformat()
        candidates = [
            _candidate(0, "ChatGPT launches AI feature today", body=_verified_body(),
                       tier=1, source="OutletA", published_date=fresh),
            _candidate(1, "Claude agent ships AI tool", body=_verified_body(),
                       source="OutletB"),
            _candidate(2, "Gemini releases new AI model", body=_verified_body(),
                       source="OutletC"),
            _candidate(3, "Llama gets a new function-calling API", body=_verified_body(),
                       source="OutletD"),
        ]
        state = _state(candidates=candidates, today=today, max_age_days=3)
        for i in range(4):
            el._tool_pick(state, {"id": i, "reason": "test", "persona": "market"}, [])
        r = el._tool_ship(state, {})
        self.assertTrue(r["shipped"], r.get("errors"))


class SearchNewsTests(unittest.TestCase):

    def test_search_respects_limit(self):
        state = _state()
        state.search_calls_used = el.MAX_SEARCH_CALLS
        r = el._tool_search_news(state, {"query": "AI news"})
        self.assertIn("error", r)
        self.assertIn("limit", r["error"])


class ReadCandidateTests(unittest.TestCase):

    def test_returns_cached_body(self):
        c = _candidate(1, "Test story", body=_verified_body())
        state = _state(candidates=[c])
        r = el._tool_read_candidate(state, {"id": 1})
        self.assertTrue(r.get("cached"))

    def test_returns_error_for_missing_id(self):
        state = _state()
        r = el._tool_read_candidate(state, {"id": 999})
        self.assertIn("error", r)


class RankHeadlinesTests(unittest.TestCase):

    def test_schema_structure(self):
        schema = {
            "type": "object",
            "properties": {
                "ranked": {"type": "array"},
                "gaps": {"type": "array"},
            },
            "required": ["ranked"],
        }
        self.assertIn("ranked", schema["properties"])


class FallbackShipTests(unittest.TestCase):

    def test_fallback_ships_with_3_picks_on_budget_exhaustion(self):
        # The budget-exhausted backstop tolerates a thin 3 (enforce_even=False)
        # rather than failing daily delivery.
        state = self._make_state_with_picks(3)
        state.tool_calls = state.hard_budget
        gate = el._validate_ship(state, enforce_even=False)
        self.assertTrue(gate["ok"])

    def test_fallback_trims_odd_5_to_even_4(self):
        state = self._make_state_with_picks(5)
        state.tool_calls = state.hard_budget
        removed = el._trim_picks_to_even(state, [])
        self.assertEqual(len(removed), 1)
        self.assertEqual(len(state.picks), 4)
        self.assertTrue(el._validate_ship(state, enforce_even=False)["ok"])

    def _make_state_with_picks(self, n=3):
        candidates = [
            _candidate(i, f"AI story {i} with clear AI hook", body=_verified_body(),
                       source=f"Source{i}", tier=1 if i == 0 else 2)
            for i in range(n)
        ]
        state = _state(candidates=candidates)
        for i in range(n):
            el._tool_pick(state, {"id": i, "reason": "test", "persona": "market"}, [])
        return state


if __name__ == "__main__":
    unittest.main()
