"""Tests for daily LLM-generated prompt tile."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import prompt_tile as pt
from editorial import PROMPT_TILE_TEMPLATE
from render_html import render_edition


class ValidatePromptTileTests(unittest.TestCase):

    def test_gold_meeting_brief_passes(self):
        # 22 words in prompt body — under 60 max, above 15 min (snackable prompts)
        tile = {
            "title": "The decision memo writer",
            "kicker": "Paste meeting notes into Claude with this. One page, no fluff.",
            "prompt": (
                "Read [paste notes]. Return: TL;DR (2 lines), decisions made, "
                "owners, and open questions — under 200 words."
            ),
            "tool_hint": "Works in Claude, ChatGPT, or Gemini.",
        }
        self.assertEqual(pt.validate_prompt_tile(tile, recent=[]), [])
        self.assertGreaterEqual(len(tile["prompt"].split()), 15)
        self.assertLessEqual(len(tile["prompt"].split()), 60)

    def test_missing_placeholder_fails(self):
        tile = {
            "title": "Do something vague",
            "kicker": "Quick task",
            "prompt": "Help me with work stuff today in a general way.",
            "tool_hint": "Works anywhere",
        }
        reasons = pt.validate_prompt_tile(tile, recent=[])
        self.assertTrue(any("placeholder" in r for r in reasons))

    def test_template_mentions_story_summaries(self):
        self.assertIn("{story_summaries}", PROMPT_TILE_TEMPLATE)


class RenderLayoutTests(unittest.TestCase):

    def test_html_has_minimal_masthead(self):
        edition = Path(__file__).resolve().parent.parent / "data" / "editions" / "2026-05-16.json"
        if not edition.exists():
            self.skipTest("fixture edition missing")
        result = render_edition(edition, issue_num=2)
        html = Path(result["html_path"]).read_text()
        self.assertNotIn('class="voice-line"', html)
        self.assertNotIn('class="hook"', html)
        self.assertIn('class="wordmark"', html)
        self.assertIn('class="dateline"', html)
        self.assertIn("border: 1px dashed #C9A671", html)
        self.assertIn("background-color: #FFF4D6", html)


if __name__ == "__main__":
    unittest.main()
