"""Tests for daily LLM-generated prompt tile."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import prompt_tile as pt
from editorial import PROMPT_TILE_STYLE_EXAMPLES, PROMPT_TILE_TEMPLATE
from render_html import render_edition

MAY_18_EXPLAINER = (
    "Explain [topic or concept] in plain English for someone smart but unfamiliar with the domain. "
    "No jargon. Use analogies where helpful. Structure it as: what it is, why it matters, and "
    "one concrete example. Keep it under 200 words."
)


class ValidatePromptTileTests(unittest.TestCase):

    def test_skeptical_reviewer_passes(self):
        tile = {
            "title": "The skeptical reviewer",
            "kicker": "",
            "prompt": PROMPT_TILE_STYLE_EXAMPLES[0]["prompt"],
            "tool_hint": PROMPT_TILE_STYLE_EXAMPLES[0]["tool_hint"],
        }
        self.assertEqual(pt.validate_prompt_tile(tile, recent=[]), [])

    def test_style_examples_pass_validation(self):
        for ex in PROMPT_TILE_STYLE_EXAMPLES:
            tile = {
                "title": ex["title"],
                "kicker": "",
                "prompt": ex["prompt"],
                "tool_hint": ex["tool_hint"],
            }
            reasons = pt.validate_prompt_tile(tile, recent=[])
            self.assertEqual(reasons, [], msg=f"{ex['title']}: {reasons}")

    def test_bracket_placeholder_fails(self):
        tile = {
            "title": "The vague helper",
            "kicker": "",
            "prompt": "Read [paste notes here] and summarize them for me in plain English today.",
            "tool_hint": "When you need a quick summary at work.",
        }
        reasons = pt.validate_prompt_tile(tile, recent=[])
        self.assertTrue(any("bracket" in r for r in reasons))

    def test_plain_english_explainer_fails(self):
        tile = {
            "title": "The plain-English explainer",
            "kicker": "Paste into Claude",
            "prompt": MAY_18_EXPLAINER,
            "tool_hint": "Great for onboarding or stakeholder memos.",
        }
        reasons = pt.validate_prompt_tile(tile, recent=[])
        self.assertTrue(
            any("bracket" in r or "generic" in r for r in reasons),
            msg=str(reasons),
        )

    def test_missing_input_cue_fails(self):
        tile = {
            "title": "The something vague",
            "kicker": "",
            "prompt": (
                "You are a helpful assistant and I want you to do something useful for my job "
                "today without me giving you any specific context or materials to work from at "
                "all right now please respond with generic advice."
            ),
            "tool_hint": "When you need help with work today.",
        }
        reasons = pt.validate_prompt_tile(tile, recent=[])
        self.assertTrue(any("input cue" in r for r in reasons))

    def test_profanity_fails(self):
        tile = {
            "title": "The angry reviewer",
            "kicker": "",
            "prompt": (
                "I'm about to send you some bullshit scope creep from a client email below. "
                "Tell me what they're really asking for and what I should push back on first."
            ),
            "tool_hint": "Before you reply to an unreasonable ask.",
        }
        reasons = pt.validate_prompt_tile(tile, recent=[])
        self.assertTrue(any("profanity" in r for r in reasons))

    def test_template_not_story_grounded(self):
        self.assertNotIn("{story_summaries}", PROMPT_TILE_TEMPLATE)


class RenderLayoutTests(unittest.TestCase):

    def test_html_has_minimal_masthead(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            edition = out / "three-story.json"
            edition.write_text(
                json.dumps(
                    {
                        "date": "2026-05-19",
                        "stories": [
                            {
                                "slot": "business",
                                "headline": "Anthropic ships enterprise memory controls",
                                "blurb": "Admins can now define workspace retention settings.",
                                "why_it_matters": "Teams get concrete governance over long-running AI work.",
                                "source_name": "Anthropic News",
                                "source_url": "https://example.com/a",
                                "tier": 1,
                            },
                            {
                                "slot": "beginner",
                                "headline": "Google previews AI glasses for daily navigation",
                                "blurb": "Wearable assistant now reads signs and live context.",
                                "why_it_matters": "Hands-free AI becomes usable in everyday tasks.",
                                "source_name": "CNBC — Technology",
                                "source_url": "https://example.com/b",
                                "tier": 1,
                            },
                            {
                                "slot": "cross",
                                "headline": "Meta adds real-time scene tracking for creators",
                                "blurb": "New model tracks multiple objects in live video.",
                                "why_it_matters": "Faster iteration for media and simulation workflows.",
                                "source_name": "Meta AI Blog",
                                "source_url": "https://example.com/c",
                                "tier": 1,
                            },
                        ],
                        "try_this_prompt": {
                            "title": "The skeptics pass",
                            "prompt": "I am pasting a launch note. Tell me what is real versus brand language.",
                            "tool_hint": "Use before forwarding internal summaries.",
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = render_edition(edition, issue_num=2, editions_dir=out)
            html = Path(result["html_path"]).read_text()
            self.assertNotIn('class="voice-line"', html)
            self.assertNotIn('class="hook"', html)
            self.assertIn('class="wordmark"', html)
            self.assertIn('class="dateline"', html)
            self.assertIn("border: 1px dashed #C9A671", html)
            self.assertIn("background: linear-gradient(165deg, #FFF8E8 0%, #FFF4D6 55%, #F9E9C8 100%)", html)


if __name__ == "__main__":
    unittest.main()
