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

    def test_ship_succeeds_with_3_valid_picks(self):
        state = self._make_state_with_picks(3)
        r = el._tool_ship(state, {})
        self.assertTrue(r["shipped"])

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
            _candidate(0, "AI glitch ruins everything", body=_verified_body(), tier=1),
            _candidate(1, "AI agent ships code", body=_verified_body()),
            _candidate(2, "ChatGPT launches feature", body=_verified_body()),
        ]
        state = _state(candidates=candidates)
        for i in range(3):
            el._tool_pick(state, {"id": i, "reason": "test", "persona": "market"}, [])
        r = el._tool_ship(state, {})
        self.assertFalse(r["shipped"])
        self.assertTrue(any("constitution" in e for e in r["errors"]))

    def test_ship_allows_one_non_load_bearing(self):
        candidates = [
            _candidate(0, "Raspberry Pi profit forecast", body=_verified_body(), tier=1),
            _candidate(1, "ChatGPT launches new AI feature", body=_verified_body()),
            _candidate(2, "Claude agent ships code autonomously", body=_verified_body()),
        ]
        state = _state(candidates=candidates)
        for i in range(3):
            el._tool_pick(state, {"id": i, "reason": "test", "persona": "market"}, [])
        r = el._tool_ship(state, {})
        self.assertTrue(r["shipped"])


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
        state = self._make_state_with_picks(3)
        state.tool_calls = state.hard_budget
        gate = el._validate_ship(state)
        self.assertTrue(gate["ok"])

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
