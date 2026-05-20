"""Tests for honest QOTD rendering (preview vs hosted API)."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from render_html import (
    build_qotd_script,
    build_qotd_section,
    qotd_api_base,
    render_edition,
)


class QotdSectionTests(unittest.TestCase):

    def _write_three_story_fixture(self, out_dir: Path) -> Path:
        edition = out_dir / "three-story.json"
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
                        {
                            "slot": "engineer",
                            "headline": "OpenAI ships constrained coding agent checkpoints",
                            "blurb": "Developers can resume long coding runs without restarting.",
                            "why_it_matters": "Long-running code tasks become more reliable.",
                            "source_name": "OpenAI News",
                            "source_url": "https://example.com/d",
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
        return edition

    def test_preview_mode_no_form_or_fake_success(self):
        html = build_qotd_section("What is AI?", api_base=None)
        self.assertIn("Preview edition", html)
        self.assertNotIn("qotd-form", html)
        self.assertNotIn("Thanks — recorded", html)

    def test_hosted_mode_has_form_and_success_copy(self):
        html = build_qotd_section("What is AI?", api_base="https://example.com")
        self.assertIn('id="qotd-form"', html)
        self.assertIn("Thanks — recorded", html)

    def test_script_empty_without_api_base(self):
        self.assertEqual(build_qotd_script('"2026-05-18"', None), "")

    def test_script_posts_to_configured_base(self):
        script = build_qotd_script('"2026-05-18"', "https://garage.example")
        self.assertIn("https://garage.example/api/daily-question", script)
        self.assertIn("if (!res.ok)", script)
        self.assertNotIn(".catch(function () {{}}).finally", script)

    def test_api_base_from_env(self):
        with patch.dict(os.environ, {"AI_ESPRESSO_QOTD_API_URL": "https://x.test/"}, clear=False):
            self.assertEqual(qotd_api_base(), "https://x.test")

    def test_rendered_html_preview_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            edition = self._write_three_story_fixture(out)
            env = {k: v for k, v in os.environ.items() if k != "AI_ESPRESSO_QOTD_API_URL"}
            with patch.dict(os.environ, env, clear=True):
                result = render_edition(edition, issue_num=99, editions_dir=out)
            html = Path(result["html_path"]).read_text()
            self.assertIn("Preview edition", html)
            self.assertNotIn('id="qotd-form"', html)


if __name__ == "__main__":
    unittest.main()
