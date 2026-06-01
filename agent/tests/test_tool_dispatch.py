"""Unit tests for native-tool dispatch and ship gates (no LLM)."""

import datetime as dt
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import espresso_loop as el


def _verified_body(chars: int = 150) -> str:
    return "Verified article excerpt. " * (chars // 25 + 1)


def _state(**kwargs) -> el.AgentState:
    defaults = dict(
        today=dt.date(2026, 5, 17),
        needed_slots=["business", "beginner", "engineer", "cross"],
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
            rules={"allow_paywalled_stories": True},
        )["ok"])

    def test_pick_rejects_paywalled_story_by_default(self):
        state = _state(
            shortlist=[
                {
                    "id": 1,
                    "headline": "FT paywalled story",
                    "url": "https://www.ft.com/content/abc123",
                    "source": "FT",
                    "tier": 1,
                    "paywall": True,
                    "body": _verified_body(),
                }
            ],
        )
        result = el.tool_pick(
            state,
            {"slot": "business", "id": 1, "reason": "strong"},
            vendor_patterns=[],
            rules={"allow_paywalled_stories": False},
        )
        self.assertIn("paywalled story blocked by policy", result.get("error", ""))

    def test_business_slot_rejects_hn_linked_story(self):
        state = _state(
            shortlist=[
                {
                    "id": 1,
                    "headline": "The mysterious Hy3 LLM is topping OpenRouter rankings",
                    "url": "https://minimaxir.com/2026/05/openrouter-hy3/",
                    "source": "Hacker News (front page)",
                    "tier": 1,
                    "body": _verified_body(),
                }
            ],
        )
        result = el.tool_pick(
            state,
            {"slot": "business", "id": 1, "reason": "seems market-relevant"},
            vendor_patterns=[],
        )
        self.assertIn("primary source", result.get("error", ""))

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

    def test_pick_rejects_duplicate_story_across_slots(self):
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
        first = el.tool_pick(
            state,
            {"slot": "business", "id": 1, "reason": "strong"},
            vendor_patterns=[],
        )
        self.assertTrue(first["ok"])
        second = el.tool_pick(
            state,
            {"slot": "beginner", "id": 1, "reason": "reuse"},
            vendor_patterns=[],
        )
        self.assertIn("duplicate story across slots", second.get("error", ""))
        self.assertEqual(second.get("conflict_slot"), "business")

    def test_pick_rejects_stale_story(self):
        state = _state(
            today=dt.date(2026, 5, 27),
            shortlist=[
                {
                    "id": 1,
                    "headline": "Cursor in Jira",
                    "url": "https://cursor.com/changelog/05-19-26",
                    "source": "Cursor Changelog",
                    "tier": 1,
                    "body": _verified_body(),
                    "published_date": "2026-05-19",
                }
            ],
        )
        result = el.tool_pick(
            state,
            {"slot": "engineer", "id": 1, "reason": "seems relevant"},
            vendor_patterns=[],
            rules={"max_story_age_days": 7},
        )
        self.assertIn("stale story", result.get("error", ""))

    def test_search_news_limit(self):
        state = _state()
        state.search_calls_used = 3
        r = el.tool_search_news(state, {"query": "ai apps"})
        self.assertIn("limit", r.get("error", ""))

    def test_search_news_weak_pool_limit(self):
        state = _state()
        state.working_memory["pool_quality"] = "weak pool today"
        state.working_memory["editor_notes"] = "thin pool; keep quality bar"
        state.search_calls_used = 4
        r = el.tool_search_news(state, {"query": "ai apps"})
        self.assertIn("max 4", r.get("error", ""))

    def test_search_news_limit_gets_bonus_from_aggregator_signals(self):
        state = _state()
        state.working_memory["aggregator_signals"] = ["cursor_composer"]
        self.assertEqual(el._search_call_limit(state), 4)
        state.search_calls_used = 4
        r = el.tool_search_news(state, {"query": "ai apps"})
        self.assertIn("max 4", r.get("error", ""))

    def test_search_news_limit_weak_pool_plus_aggregator_bonus(self):
        state = _state()
        state.working_memory["pool_quality"] = "weak pool today"
        state.working_memory["editor_notes"] = "thin pool; keep quality bar"
        state.working_memory["aggregator_signals"] = ["odyssey_world_models"]
        self.assertEqual(el._search_call_limit(state), 5)

    def test_uncovered_aggregator_signals_detected_when_primary_missing(self):
        candidates = [
            SimpleNamespace(
                headline="Cursor Composer 2.5 nears coding frontier",
                blurb="new coding model",
                url="https://tldr.tech/ai/2026-05-19",
                aggregator=True,
            ),
            SimpleNamespace(
                headline="Generic AI story from primary source",
                blurb="",
                url="https://example.com/story",
                aggregator=False,
            ),
        ]
        signals = el._uncovered_aggregator_signals(candidates)
        self.assertIn("cursor_composer", signals)

    def test_uncovered_aggregator_signals_cleared_when_primary_present(self):
        candidates = [
            SimpleNamespace(
                headline="Cursor Composer 2.5 nears coding frontier",
                blurb="new coding model",
                url="https://tldr.tech/ai/2026-05-19",
                aggregator=True,
            ),
            SimpleNamespace(
                headline="Cursor releases Composer 2.5",
                blurb="official changelog post",
                url="https://cursor.com/changelog/composer-2-5",
                aggregator=False,
            ),
        ]
        signals = el._uncovered_aggregator_signals(candidates)
        self.assertNotIn("cursor_composer", signals)

    def test_high_impact_legal_candidate_detection(self):
        legal = SimpleNamespace(
            headline="Jury rejects Elon Musk lawsuit against OpenAI, reshaping governance control",
            blurb="The ruling changes ownership control and market access assumptions.",
            url="https://example.com/legal-story",
            source_name="Semafor Technology",
            tier=4,
            aggregator=False,
        )
        self.assertTrue(el._is_high_impact_legal_candidate(legal))

    def test_high_impact_legal_candidate_rejects_courtroom_drama_without_consequence(self):
        legal = SimpleNamespace(
            headline="Court hears Musk and OpenAI arguments in headline lawsuit",
            blurb="High-profile legal drama continues without ruling details.",
            url="https://example.com/legal-drama",
            source_name="Semafor Technology",
            tier=4,
            aggregator=False,
        )
        self.assertFalse(el._is_high_impact_legal_candidate(legal))

    def test_high_impact_legal_candidate_requires_ai_entity(self):
        legal = SimpleNamespace(
            headline="Jury verdict reshapes governance control at major media conglomerate",
            blurb="Ruling changes board ownership and platform distribution rights.",
            url="https://example.com/non-ai-legal-story",
            source_name="Business Desk",
            tier=4,
            aggregator=False,
        )
        self.assertFalse(el._is_high_impact_legal_candidate(legal))

    def test_high_impact_legal_injected_into_capped_pool(self):
        legal = SimpleNamespace(
            headline="Jury rejects Elon Musk lawsuit against OpenAI, reshaping governance control",
            blurb="Ruling has product access and governance impact.",
            url="https://example.com/legal-story",
            source_name="Semafor Technology",
            tier=4,
            aggregator=False,
        )
        fresh = [
            SimpleNamespace(
                headline=f"Story {i}",
                blurb="normal item",
                url=f"https://example.com/{i}",
                source_name=f"source-{i % 10}",
                tier=1,
                aggregator=False,
            )
            for i in range(60)
        ]
        fresh.append(legal)
        capped = fresh[:60].copy()
        per_source = {}
        for c in capped:
            per_source[c.source_name] = per_source.get(c.source_name, 0) + 1

        el._inject_high_impact_legal_candidate(capped, fresh, per_source, max_total=60, max_per_source=4)
        self.assertTrue(any(getattr(c, "url", "") == legal.url for c in capped))

    def test_high_impact_legal_injection_skips_low_impact_legal_candidate(self):
        low_impact_legal = SimpleNamespace(
            headline="Court hearing continues in Musk v OpenAI legal battle",
            blurb="Arguments continue as courtroom drama unfolds.",
            url="https://example.com/low-impact-legal",
            source_name="Semafor Technology",
            tier=4,
            aggregator=False,
        )
        fresh = [
            SimpleNamespace(
                headline=f"Story {i}",
                blurb="normal item",
                url=f"https://example.com/{i}",
                source_name=f"source-{i % 10}",
                tier=1,
                aggregator=False,
            )
            for i in range(60)
        ]
        fresh.append(low_impact_legal)
        capped = fresh[:60].copy()
        per_source = {}
        for c in capped:
            per_source[c.source_name] = per_source.get(c.source_name, 0) + 1

        el._inject_high_impact_legal_candidate(capped, fresh, per_source, max_total=60, max_per_source=4)
        self.assertFalse(any(getattr(c, "url", "") == low_impact_legal.url for c in capped))

    def test_high_impact_legal_injection_does_not_add_second_legal_candidate(self):
        existing_legal = SimpleNamespace(
            headline="Jury rejects Elon Musk lawsuit against OpenAI, reshaping governance control",
            blurb="Ruling changes governance control.",
            url="https://example.com/existing-legal",
            source_name="Semafor Technology",
            tier=4,
            aggregator=False,
        )
        another_legal = SimpleNamespace(
            headline="Trial ruling alters OpenAI board charter and ownership structure",
            blurb="Enforceable ruling changes board control and product access terms.",
            url="https://example.com/another-legal",
            source_name="Legal Desk",
            tier=4,
            aggregator=False,
        )
        capped = [existing_legal] + [
            SimpleNamespace(
                headline=f"Story {i}",
                blurb="normal item",
                url=f"https://example.com/{i}",
                source_name=f"source-{i % 10}",
                tier=1,
                aggregator=False,
            )
            for i in range(59)
        ]
        fresh = capped + [another_legal]
        per_source = {}
        for c in capped:
            per_source[c.source_name] = per_source.get(c.source_name, 0) + 1

        el._inject_high_impact_legal_candidate(capped, fresh, per_source, max_total=60, max_per_source=4)
        legal_count = sum(1 for c in capped if el._is_high_impact_legal_candidate(c))
        self.assertEqual(legal_count, 1)
        self.assertTrue(any(getattr(c, "url", "") == existing_legal.url for c in capped))
        self.assertFalse(any(getattr(c, "url", "") == another_legal.url for c in capped))

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

    def test_editorial_rubric_includes_third_card_fallback_lane(self):
        self.assertIn("THIRD-CARD FALLBACK LANE", el._EDITORIAL_RUBRIC)
        self.assertIn("quality-only, never filler", el._EDITORIAL_RUBRIC)
        self.assertIn("capability/tool/workflow unlock", el._EDITORIAL_RUBRIC)

    def test_critic_instructions_require_high_signal_third_slot(self):
        self.assertIn("third slot", el._CRITIC_TAIL.lower())
        self.assertIn("cool/new capability", el._CRITIC_TAIL)
        self.assertIn("direct user utility", el._CRITIC_TAIL)


class ShipGateTests(unittest.TestCase):

    def _four_picks(self, tier1: int = 1):
        picks = {}
        tiers = [1, 2, 2, 2] if tier1 else [2, 2, 2, 2]
        headlines = {
            "business": "Anthropic and BlackRock partner on AI for asset management",
            "beginner": "ChatGPT can now look at your bank account",
            "engineer": "OpenAI brings its Codex coding app to mobile",
            "cross": "CFTC runs ML models to flag suspicious bets on Polymarket",
        }
        for slot, tier in zip(["business", "beginner", "engineer", "cross"], tiers):
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
            picks=self._four_picks(),
        )
        for p in state.picks.values():
            p.pop("body", None)
        verdict = el.tool_self_critique(state, {})
        self.assertEqual(verdict["verdict"], "revise")
        self.assertTrue(verdict["issues"])

    def test_ship_blocked_without_critique(self):
        state = _state(picks=self._four_picks())
        gate = el.validate_ship_gates(state, {"tier1_minimum": 1})
        self.assertFalse(gate["ok"])
        self.assertTrue(any("self_critique" in e for e in gate["errors"]))

    def test_ship_ok_after_approve(self):
        state = _state(
            picks=self._four_picks(),
            last_critic_verdict={"verdict": "approve", "reason": "good mix"},
        )
        gate = el.validate_ship_gates(state, {"tier1_minimum": 1})
        self.assertTrue(gate["ok"])

    def test_ship_blocked_no_tier1(self):
        state = _state(
            picks=self._four_picks(tier1=0),
            last_critic_verdict={"verdict": "approve", "reason": "ok"},
        )
        gate = el.validate_ship_gates(state, {"tier1_minimum": 1})
        self.assertFalse(gate["ok"])

    def test_weak_pool_two_picks_not_allowed(self):
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
        self.assertFalse(gate["ok"])

    def test_weak_pool_three_picks_allowed(self):
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
                    "url": "https://example.com/2",
                    "source": "y",
                    "tier": 2,
                    "body": _verified_body(),
                },
                "engineer": {
                    "id": 3,
                    "headline": "Claude now runs memory-aware coding agents in background tasks",
                    "url": "https://example.com/3",
                    "source": "z",
                    "tier": 2,
                    "body": _verified_body(),
                },
            },
            last_critic_verdict={"verdict": "approve", "reason": "ok"},
        )
        state.working_memory["pool_quality"] = "weak pool today"
        state.working_memory["editor_notes"] = "documented weak pool with constrained source quality"
        gate = el.validate_ship_gates(state, {"tier1_minimum": 1})
        self.assertTrue(gate["ok"], gate["errors"])

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
            picks=self._four_picks(),
            last_critic_verdict={"verdict": "approve", "reason": "ok"},
        )
        r = el.tool_ship_edition(state, {}, {"tier1_minimum": 1})
        self.assertTrue(r["shipped"])
        self.assertTrue(state.shipped)

    def test_ship_rejects_waymo_even_if_critic_approved(self):
        picks = self._four_picks()
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

    def test_ship_allows_one_non_load_bearing_card(self):
        picks = self._four_picks()
        picks["cross"]["headline"] = "Cursor ships AI Shadow Workspace for background code iteration"
        picks["beginner"]["headline"] = "Shadow Workspace now runs in the background while you code"
        state = _state(
            needed_slots=["business", "beginner", "cross", "engineer"],
            picks=picks,
            last_critic_verdict={"verdict": "approve", "reason": "cool mix"},
        )
        gate = el.validate_ship_gates(state, {"tier1_minimum": 1})
        self.assertTrue(gate["ok"], gate["errors"])

    def test_ship_blocks_when_two_cards_are_non_load_bearing(self):
        picks = self._four_picks()
        picks["beginner"]["headline"] = "Shadow Workspace now runs in the background while you code"
        picks["cross"]["headline"] = "New automation workflow ships this week"
        state = _state(
            needed_slots=["business", "beginner", "cross", "engineer"],
            picks=picks,
            last_critic_verdict={"verdict": "approve", "reason": "cool mix"},
        )
        gate = el.validate_ship_gates(state, {"tier1_minimum": 1})
        self.assertFalse(gate["ok"])
        self.assertTrue(
            any("too many non-load-bearing stories" in e for e in gate["errors"]),
            gate["errors"],
        )

    def test_approve_lock_blocks_unpick(self):
        state = _state(
            picks=self._four_picks(),
            last_critic_verdict={"verdict": "approve", "reason": "ok"},
        )
        r = el.dispatch_tool("unpick", {"slot": "business"}, state, [], {"tier1_minimum": 1})
        self.assertIn("locked", r.get("error", ""))

    def test_ship_gate_failure_clears_approve_lock(self):
        picks = self._four_picks()
        picks["beginner"]["headline"] = "Waymo driverless cars become trapped in Atlanta suburb after glitch"
        state = _state(
            picks=picks,
            last_critic_verdict={"verdict": "approve", "reason": "ok"},
        )
        el.tool_ship_edition(state, {}, {"tier1_minimum": 1})
        self.assertIsNone(state.last_critic_verdict)
        r = el.dispatch_tool("unpick", {"slot": "business"}, state, [], {"tier1_minimum": 1})
        self.assertNotIn("locked", r.get("error", ""))

    def test_weak_pool_critic_override_does_not_bypass_ship_gates(self):
        state = _state(
            picks={
                "business": {
                    "id": 1,
                    "headline": "ChatGPT can now look at your bank account",
                    "url": "https://example.com",
                    "source": "OpenAI News",
                    "tier": 1,
                    "body": _verified_body(),
                },
                "beginner": {
                    "id": 2,
                    "headline": "OpenAI brings its Codex coding app to mobile",
                    "url": "https://example.com/2",
                    "source": "OpenAI News",
                    "tier": 2,
                    "body": _verified_body(),
                },
            },
            last_critic_verdict={"verdict": "revise", "reason": "thin pool"},
        )
        state.working_memory["pool_quality"] = "weak pool today"
        state.working_memory["editor_notes"] = "documented weak pool"
        r = el.tool_ship_edition(state, {}, {"tier1_minimum": 1})
        self.assertFalse(r["shipped"])


class MockToolLoopTests(unittest.TestCase):

    def test_run_tool_agent_ships_on_tool_use(self):
        from unittest.mock import MagicMock, patch

        headlines = [
            "Anthropic and BlackRock partner on AI for asset management",
            "ChatGPT can now look at your bank account",
            "OpenAI brings its Codex coding app to mobile",
            "CFTC runs ML models to flag suspicious bets on Polymarket",
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
                for i in range(4)
            ],
        )
        state.candidates_by_id = {i: state.shortlist[i] for i in range(4)}
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
                    FakeBlock("pick", {"slot": "cross", "id": 3, "reason": "w"}, "tu4"),
                ])
            if call_count["n"] == 2:
                return FakeResp([FakeBlock("self_critique", {}, "tu5")])
            if call_count["n"] == 3:
                state.last_critic_verdict = {"verdict": "approve", "reason": "ok", "issues": []}
                return FakeResp([FakeBlock("ship_edition", {}, "tu6")])
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
        self.assertEqual(len(state.picks), 4)

    def test_run_tool_agent_grants_finalization_lap_on_stall_without_budget_exhaustion(self):
        state = _state(
            picks={
                "business": {
                    "id": 1,
                    "headline": "Anthropic and BlackRock partner on AI for asset management",
                    "url": "https://example.com/1",
                    "source": "x",
                    "tier": 1,
                    "body": _verified_body(),
                },
                "beginner": {
                    "id": 2,
                    "headline": "ChatGPT can now look at your bank account",
                    "url": "https://example.com/2",
                    "source": "y",
                    "tier": 2,
                    "body": _verified_body(),
                },
                "engineer": {
                    "id": 3,
                    "headline": "OpenAI brings its Codex coding app to mobile",
                    "url": "https://example.com/3",
                    "source": "z",
                    "tier": 2,
                    "body": _verified_body(),
                },
                "cross": {
                    "id": 4,
                    "headline": "CFTC runs ML models to flag suspicious bets on Polymarket",
                    "url": "https://example.com/4",
                    "source": "w",
                    "tier": 2,
                    "body": _verified_body(),
                },
            },
            last_critic_verdict={"verdict": "revise", "reason": "same issue repeats"},
        )
        state.tool_calls = 12
        state.hard_budget = 40
        state.working_memory["forced_convergence"] = {
            "active": True,
            "revise_streak": 3,
            "no_improvement_revise_streak": 2,
            "last_signature": "business:1|beginner:2|engineer:3|cross:4",
            "last_issue_classes": ["vendor_mix"],
        }

        with patch.object(el, "_anthropic_client", return_value=(object(), "test-model")):
            with patch.object(el, "_run_tool_agent_loop", side_effect=[False, True]) as mock_loop:
                ok = el.run_tool_agent(state, [], {"tier1_minimum": 1}, [])

        self.assertTrue(ok)
        self.assertEqual(mock_loop.call_count, 2)
        self.assertTrue(state.working_memory["finalization_contract"]["active"])


class LoopGuardrailTests(unittest.TestCase):

    def _state_with_picks(self) -> el.AgentState:
        state = _state(
            picks={
                "business": {
                    "id": 1,
                    "headline": "Anthropic and BlackRock partner on AI for asset management",
                    "url": "https://example.com/1",
                    "source": "x",
                    "tier": 1,
                    "body": _verified_body(),
                },
                "beginner": {
                    "id": 2,
                    "headline": "ChatGPT can now look at your bank account",
                    "url": "https://example.com/2",
                    "source": "y",
                    "tier": 2,
                    "body": _verified_body(),
                },
                "engineer": {
                    "id": 3,
                    "headline": "OpenAI brings its Codex coding app to mobile",
                    "url": "https://example.com/3",
                    "source": "z",
                    "tier": 2,
                    "body": _verified_body(),
                },
                "cross": {
                    "id": 4,
                    "headline": "CFTC runs ML models to flag suspicious bets on Polymarket",
                    "url": "https://example.com/4",
                    "source": "w",
                    "tier": 2,
                    "body": _verified_body(),
                },
            },
        )
        return state

    def test_revise_streak_enables_forced_convergence(self):
        state = self._state_with_picks()
        repeated = {
            "verdict": "revise",
            "reason": "same vendor redundancy and duplication",
            "issues": ["vendor concentration", "duplicate event"],
        }
        with patch.object(el, "llm_json", return_value=repeated):
            el.tool_self_critique(state, {})
            el.tool_self_critique(state, {})
            el.tool_self_critique(state, {})
        forced = state.working_memory.get("forced_convergence", {})
        self.assertTrue(forced.get("active"))
        self.assertGreaterEqual(forced.get("no_improvement_revise_streak", 0), 2)

    def test_repick_oscillation_blocked_after_revise_unpick(self):
        state = self._state_with_picks()
        state.last_critic_verdict = {
            "verdict": "revise",
            "reason": "duplicate/vendor issue",
            "issues": ["same vendor"],
        }
        removed = state.picks["beginner"]["id"]
        unpick = el.tool_unpick(state, {"slot": "beginner"}, vendor_patterns=[])
        self.assertTrue(unpick["ok"])

        shortlist_entry = {
            "id": removed,
            "headline": "ChatGPT can now look at your bank account",
            "url": "https://example.com/2",
            "source": "y",
            "tier": 2,
            "body": _verified_body(),
        }
        state.shortlist = [shortlist_entry]
        repick = el.tool_pick(
            state,
            {"slot": "beginner", "id": removed, "reason": "trying same again"},
            vendor_patterns=[],
        )
        self.assertIn("do-not-repick", repick.get("error", ""))

    def test_finalization_contract_blocks_exploration(self):
        state = self._state_with_picks()
        state.working_memory["finalization_contract"] = {
            "active": True,
            "phase": "await_critique",
            "targeted_swaps_used": 0,
        }
        blocked = el.dispatch_tool(
            "search_news",
            {"query": "fresh angle"},
            state,
            [],
            {"tier1_minimum": 1},
        )
        self.assertIn("finalization contract", blocked.get("error", ""))

    def test_finalization_recritique_allows_fill_when_slate_incomplete(self):
        state = self._state_with_picks()
        state.picks.pop("cross")
        state.shortlist = [
            {
                "id": 99,
                "headline": "AI discovers new battery chemistry pathway for grid storage",
                "url": "https://example.com/99",
                "source": "x",
                "tier": 1,
                "body": _verified_body(),
            }
        ]
        state.working_memory["finalization_contract"] = {
            "active": True,
            "phase": "await_recritique",
            "targeted_swaps_used": 1,
        }
        picked = el.dispatch_tool(
            "pick",
            {"slot": "cross", "id": 99, "reason": "fill missing slot"},
            state,
            [],
            {"tier1_minimum": 1},
        )
        self.assertTrue(picked.get("ok"), picked)

    def test_stall_summary_records_revise_loop(self):
        state = self._state_with_picks()
        state.working_memory["forced_convergence"] = {
            "active": True,
            "no_improvement_revise_streak": 3,
            "revise_streak": 3,
            "last_signature": "business:1|beginner:2|engineer:3|cross:4",
        }
        summary = el._stall_reason_summary(state)
        self.assertIn("revise_loop", summary)


if __name__ == "__main__":
    unittest.main()
