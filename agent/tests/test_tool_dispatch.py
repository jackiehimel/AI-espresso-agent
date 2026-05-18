"""Unit tests for native-tool dispatch and ship gates (no LLM)."""

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
        needed_slots=["business", "beginner", "engineer"],
        shortlist=[],
        candidates_by_id={},
        archive_headlines=[],
    )
    defaults.update(kwargs)
    return el.AgentState(**defaults)


class ToolDispatchTests(unittest.TestCase):

    def test_update_memory_pool_quality(self):
        state = _state()
        r = el.tool_update_memory(state, {"key": "pool_quality", "value": "weak — thin pool"})
        self.assertTrue(r["ok"])
        self.assertIn("weak", state.working_memory["pool_quality"])

    def test_update_memory_decisions_append(self):
        state = _state()
        el.tool_update_memory(state, {"key": "decisions", "value": "tried search"})
        el.tool_update_memory(state, {"key": "decisions", "value": "picked business"})
        self.assertEqual(len(state.working_memory["decisions"]), 2)

    def test_note_weak_pool_sets_editor_notes(self):
        state = _state()
        r = el.tool_note_weak_pool(
            state,
            {"reason": "Pool thin", "adjustments": "skip engineer slot"},
        )
        self.assertTrue(r["ok"])
        self.assertIn("Pool thin", state.working_memory["editor_notes"])
        self.assertIn("weak", state.working_memory["pool_quality"].lower())

    def test_note_weak_pool_extends_budget_when_slate_empty(self):
        state = _state()
        state.hard_budget = 40
        r = el.tool_note_weak_pool(state, {"reason": "Thin pool", "adjustments": "2-story"})
        self.assertTrue(r["ok"])
        self.assertEqual(state.hard_budget, 52)
        self.assertIn("required_next", r)
        self.assertIn("reminder", r)

    def test_read_candidate_paywall_uses_rss_when_article_403(self):
        from unittest.mock import patch

        state = _state(
            shortlist=[
                {
                    "id": 1,
                    "headline": "OpenAI bought a voice startup",
                    "url": "https://www.nytimes.com/2026/05/15/technology/example.html",
                    "source": "NYT — Technology",
                    "tier": 1,
                    "paywall": True,
                    "blurb": "A" * 100,
                }
            ],
        )
        with patch("espresso_agent.fetch_url", return_value=None):
            r = el.tool_read_candidate(state, {"id": 1})
        self.assertEqual(r["body_source"], "rss_summary")
        self.assertEqual(len(r["candidate"]["body"]), 100)
        self.assertTrue(el.tool_pick(
            state, {"slot": "beginner", "id": 1, "reason": "verified rss"},
            vendor_patterns=[],
        )["ok"])

    def test_pick_and_unpick(self):
        state = _state(
            shortlist=[
                {
                    "id": 1,
                    "headline": "OpenAI ships X",
                    "url": "https://openai.com/x",
                    "source": "OpenAI",
                    "tier": 1,
                    "body": _verified_body(),
                }
            ],
        )
        r = el.tool_pick(
            state,
            {"slot": "business", "id": 1, "reason": "strong"},
            vendor_patterns=[],
        )
        self.assertTrue(r["ok"])
        self.assertIn("business", state.picks)
        el.tool_unpick(state, {"slot": "business"}, vendor_patterns=[])
        self.assertNotIn("business", state.picks)

    def test_search_news_limit(self):
        state = _state()
        state.search_calls_used = 2
        r = el.tool_search_news(state, {"query": "ai apps"})
        self.assertIn("limit", r.get("error", ""))

    def test_search_news_auth_failure_does_not_count_budget(self):
        from unittest.mock import patch

        state = _state()
        auth_err = "Perplexity API key contains non-ASCII characters"
        with patch.object(el, "_pplx_cli_hits", return_value=(None, None)):
            with patch.object(el, "_perplexity_api_hits", return_value=(None, auth_err)):
                for _ in range(2):
                    r = el.tool_search_news(state, {"query": "coding agents"})
                    self.assertTrue(r.get("auth_failure"))
        self.assertEqual(el._search_calls_used(state), 0)

    def test_search_news_rate_limit_counts_budget(self):
        from unittest.mock import patch

        state = _state()
        with patch.object(el, "_pplx_cli_hits", return_value=(None, "rate limit exceeded")):
            with patch.object(
                el, "_perplexity_api_hits", return_value=(None, "429 rate limit exceeded")
            ):
                with patch.dict("os.environ", {"PERPLEXITY_API_KEY": "pplx-test"}, clear=False):
                    r = el.tool_search_news(state, {"query": "ai apps"})
        self.assertNotIn("auth_failure", r)
        self.assertEqual(el._search_calls_used(state), 1)

    def test_immediate_unpick_after_pick_rejected(self):
        state = _state(
            shortlist=[
                {
                    "id": 1,
                    "headline": "OpenAI ships X",
                    "url": "https://openai.com/x",
                    "source": "OpenAI",
                    "tier": 1,
                    "body": _verified_body(),
                }
            ],
        )
        state.tool_calls = 1
        el.tool_pick(
            state,
            {"slot": "beginner", "id": 1, "reason": "strong"},
            vendor_patterns=[],
        )
        state.tool_calls = 2
        r = el.tool_unpick(state, {"slot": "beginner"}, vendor_patterns=[])
        self.assertIn("just picked", r.get("error", ""))
        self.assertIn("beginner", state.picks)

    def test_unpick_after_check_archive_allowed(self):
        state = _state(
            shortlist=[
                {
                    "id": 1,
                    "headline": "OpenAI ships X",
                    "url": "https://openai.com/x",
                    "source": "OpenAI",
                    "tier": 1,
                    "body": _verified_body(),
                }
            ],
        )
        state.tool_calls = 1
        el.tool_pick(
            state,
            {"slot": "beginner", "id": 1, "reason": "strong"},
            vendor_patterns=[],
        )
        state.tool_calls = 2
        el.tool_check_archive(state, {"headline": "OpenAI ships X"})
        state.tool_calls = 3
        r = el.tool_unpick(state, {"slot": "beginner"}, vendor_patterns=[])
        self.assertTrue(r.get("ok"))
        self.assertNotIn("beginner", state.picks)


class ShipGateTests(unittest.TestCase):

    def _three_picks(self, tier1: int = 1):
        picks = {}
        tiers = [1, 2, 2] if tier1 else [2, 2, 2]
        headlines = {
            "business": "Anthropic and BlackRock partner on AI for asset management",
            "beginner": "ChatGPT can now look at your bank account",
            "engineer": "OpenAI brings its Codex coding app to mobile",
        }
        for slot, tier in zip(["business", "beginner", "engineer"], tiers):
            picks[slot] = {
                "id": 1,
                "headline": headlines[slot],
                "url": "https://example.com",
                "source": "x",
                "tier": tier,
                "body": _verified_body(),
            }
        return picks

    def test_self_critique_rejects_unverified_picks_without_llm(self):
        state = _state(
            picks=self._three_picks(),
        )
        for p in state.picks.values():
            p.pop("body", None)
        verdict = el.tool_self_critique(state, {})
        self.assertEqual(verdict["verdict"], "revise")
        self.assertTrue(verdict["issues"])

    def test_ship_blocked_without_critique(self):
        state = _state(picks=self._three_picks())
        gate = el.validate_ship_gates(state, {"tier1_minimum": 1})
        self.assertFalse(gate["ok"])
        self.assertTrue(any("self_critique" in e for e in gate["errors"]))

    def test_ship_ok_after_approve(self):
        state = _state(
            picks=self._three_picks(),
            last_critic_verdict={"verdict": "approve", "reason": "good mix"},
        )
        gate = el.validate_ship_gates(state, {"tier1_minimum": 1})
        self.assertTrue(gate["ok"])

    def test_ship_blocked_no_tier1(self):
        state = _state(
            picks=self._three_picks(tier1=0),
            last_critic_verdict={"verdict": "approve", "reason": "ok"},
        )
        gate = el.validate_ship_gates(state, {"tier1_minimum": 1})
        self.assertFalse(gate["ok"])

    def test_weak_pool_two_picks_allowed(self):
        state = _state(
            picks={
                "business": {
                    "id": 1,
                    "headline": "ChatGPT can now look at your bank account",
                    "url": "https://example.com",
                    "source": "x",
                    "tier": 1,
                    "body": _verified_body(),
                },
                "beginner": {
                    "id": 2,
                    "headline": "OpenAI brings its Codex coding app to mobile",
                    "url": "https://example.com",
                    "source": "y",
                    "tier": 2,
                    "body": _verified_body(),
                },
            },
            last_critic_verdict={"verdict": "approve", "reason": "ok"},
        )
        state.working_memory["pool_quality"] = "weak pool today"
        state.working_memory["editor_notes"] = "Skipping engineer — thin pool"
        gate = el.validate_ship_gates(state, {"tier1_minimum": 1})
        self.assertTrue(gate["ok"])

    def test_two_picks_without_weak_note_blocked(self):
        state = _state(
            picks={
                "business": {
                    "id": 1,
                    "headline": "ChatGPT can now look at your bank account",
                    "url": "https://example.com",
                    "source": "x",
                    "tier": 1,
                    "body": _verified_body(),
                },
                "beginner": {
                    "id": 2,
                    "headline": "OpenAI brings its Codex coding app to mobile",
                    "url": "https://example.com",
                    "source": "y",
                    "tier": 2,
                    "body": _verified_body(),
                },
            },
            last_critic_verdict={"verdict": "approve", "reason": "ok"},
        )
        gate = el.validate_ship_gates(state, {"tier1_minimum": 1})
        self.assertFalse(gate["ok"])

    def test_tool_ship_edition_sets_shipped(self):
        state = _state(
            picks=self._three_picks(),
            last_critic_verdict={"verdict": "approve", "reason": "ok"},
        )
        r = el.tool_ship_edition(state, {}, {"tier1_minimum": 1})
        self.assertTrue(r["shipped"])
        self.assertTrue(state.shipped)

    def test_ship_rejects_waymo_even_if_critic_approved(self):
        picks = self._three_picks()
        picks["beginner"] = {
            "id": 9,
            "headline": "Waymo driverless cars become trapped in Atlanta suburb after glitch",
            "url": "https://example.com",
            "source": "BBC Technology",
            "tier": 1,
            "body": _verified_body(),
        }
        state = _state(
            picks=picks,
            last_critic_verdict={"verdict": "approve", "reason": "ok"},
        )
        gate = el.validate_ship_gates(state, {"tier1_minimum": 1})
        self.assertFalse(gate["ok"])
        self.assertTrue(any("constitution" in e for e in gate["errors"]))

    def test_approve_lock_blocks_unpick(self):
        state = _state(
            picks=self._three_picks(),
            last_critic_verdict={"verdict": "approve", "reason": "ok"},
        )
        r = el.dispatch_tool("unpick", {"slot": "business"}, state, [], {"tier1_minimum": 1})
        self.assertIn("locked", r.get("error", ""))

    def test_ship_gate_failure_clears_approve_lock(self):
        picks = self._three_picks()
        picks["beginner"]["headline"] = "Waymo driverless cars become trapped in Atlanta suburb after glitch"
        state = _state(
            picks=picks,
            last_critic_verdict={"verdict": "approve", "reason": "ok"},
        )
        el.tool_ship_edition(state, {}, {"tier1_minimum": 1})
        self.assertIsNone(state.last_critic_verdict)
        r = el.dispatch_tool("unpick", {"slot": "business"}, state, [], {"tier1_minimum": 1})
        self.assertNotIn("locked", r.get("error", ""))


class MockToolLoopTests(unittest.TestCase):

    def test_run_tool_agent_ships_on_tool_use(self):
        from unittest.mock import MagicMock, patch

        headlines = [
            "Anthropic and BlackRock partner on AI for asset management",
            "ChatGPT can now look at your bank account",
            "OpenAI brings its Codex coding app to mobile",
        ]
        state = _state(
            shortlist=[
                {
                    "id": i,
                    "headline": headlines[i],
                    "url": f"https://example.com/test{i}",
                    "source": "s",
                    "tier": 1,
                    "score": 90,
                    "persona": "business",
                    "body": _verified_body(),
                }
                for i in range(3)
            ],
        )
        state.candidates_by_id = {i: state.shortlist[i] for i in range(3)}
        state.working_memory["coverage_gaps"] = []

        class FakeBlock:
            def __init__(self, name, input_dict, bid):
                self.type = "tool_use"
                self.name = name
                self.input = input_dict
                self.id = bid

        class FakeResp:
            def __init__(self, blocks):
                self.content = blocks
                self.stop_reason = "tool_use"

        call_count = {"n": 0}

        def fake_create(**kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return FakeResp([
                    FakeBlock("pick", {"slot": "business", "id": 0, "reason": "x"}, "tu1"),
                    FakeBlock("pick", {"slot": "beginner", "id": 1, "reason": "y"}, "tu2"),
                    FakeBlock("pick", {"slot": "engineer", "id": 2, "reason": "z"}, "tu3"),
                ])
            if call_count["n"] == 2:
                return FakeResp([FakeBlock("self_critique", {}, "tu4")])
            if call_count["n"] == 3:
                state.last_critic_verdict = {"verdict": "approve", "reason": "ok", "issues": []}
                return FakeResp([FakeBlock("ship_edition", {}, "tu5")])
            return FakeResp([])

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = fake_create

        with patch.object(el, "_anthropic_client", return_value=(mock_client, "test-model")):
            with patch.object(el, "tool_self_critique") as mock_critique:
                def approve(state, args):
                    state.last_critic_verdict = {"verdict": "approve", "reason": "ok", "issues": []}
                    return state.last_critic_verdict
                mock_critique.side_effect = approve
                ok = el.run_tool_agent(state, [], {"tier1_minimum": 1}, [])

        self.assertTrue(ok)
        self.assertTrue(state.shipped)
        self.assertEqual(len(state.picks), 3)


if __name__ == "__main__":
    unittest.main()
