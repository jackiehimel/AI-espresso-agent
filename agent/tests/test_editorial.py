"""Editorial validation: hooks, banned copy, drama-as-hook."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import editorial as ed


class HookValidationTests(unittest.TestCase):

    def test_rejects_dangling_your(self):
        hook = "OpenAI shipped Codex. ChatGPT can now look at your. And Google updated Search."
        issues = ed.validate_hook(hook)
        self.assertTrue(any("your" in r for r in issues))

    def test_accepts_complete_clauses(self):
        hook = (
            "OpenAI shipped Codex on mobile. "
            "ChatGPT added bank connections. "
            "And Google wired Gemini into shopping answers."
        )
        self.assertEqual(ed.validate_hook(hook), [])

    def test_voice_stable_per_date(self):
        a = ed.voice_for_date("2026-05-16")
        b = ed.voice_for_date("2026-05-16")
        c = ed.voice_for_date("2026-05-17")
        self.assertEqual(a, b)
        self.assertIn(a, ed.VOICE_CHARACTERS)
        self.assertNotEqual(a, c)  # usually different next day


class StoryCopyValidationTests(unittest.TestCase):

    def test_banned_words_flagged(self):
        story = {
            "headline": "A startup will redefine the market",
            "blurb": "Details here.",
            "why_it_matters": "It matters.",
        }
        issues = ed.validate_story_copy(story)
        self.assertTrue(any("redefine" in r for r in issues))

    def test_drama_headline_flagged(self):
        story = {
            "headline": "OpenAI's nonprofit trial with Elon Musk just wrapped",
            "blurb": "Court ended arguments.",
            "why_it_matters": "Legal outcome pending.",
        }
        issues = ed.validate_story_copy(story)
        self.assertTrue(any("drama" in r for r in issues))

    def test_product_story_clean(self):
        story = {
            "headline": "Google is wiring Gemini into Search shopping answers",
            "blurb": "Gemini pulls live product data in AI Overviews.",
            "why_it_matters": "Commerce queries now use the same model stack.",
        }
        self.assertEqual(ed.validate_story_copy(story), [])

    def test_pick_requires_body(self):
        candidate = {
            "headline": "OpenAI shipped a feature",
            "url": "https://example.com",
        }
        issues = ed.validate_pick_has_body(candidate)
        self.assertTrue(any("verified article body" in r for r in issues))

    def test_paywall_rss_summary_counts_as_verified(self):
        candidate = {
            "paywall": True,
            "body": "A" * 100,
            "body_source": "rss_summary",
        }
        self.assertTrue(ed.candidate_has_verified_body(candidate))
        self.assertEqual(ed.validate_pick_has_body(candidate), [])

    def test_paywall_short_rss_fails(self):
        candidate = {
            "paywall": True,
            "body": "Too short.",
            "body_source": "rss_summary",
        }
        self.assertFalse(ed.candidate_has_verified_body(candidate))


class SlotLabelTests(unittest.TestCase):

    def test_persona_labels(self):
        self.assertEqual(ed.slot_label("business")[0], "MARKET")
        self.assertEqual(ed.slot_label("beginner")[0], "EVERYDAY")
        self.assertEqual(ed.slot_label("engineer")[0], "BUILD")
        self.assertEqual(ed.slot_label("cross")[0], "INDUSTRY")


class PreheaderTests(unittest.TestCase):

    def test_derive_preheader_from_headlines(self):
        from render_html import derive_preheader

        stories = [
            {"headline": "Story one"},
            {"headline": "Story two"},
            {"headline": "Story three"},
        ]
        pre = derive_preheader(stories)
        self.assertIn("Story one", pre)
        self.assertIn("·", pre)


if __name__ == "__main__":
    unittest.main()
