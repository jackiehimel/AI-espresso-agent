"""Phase 4 polish: public HTML footer, hidden tiers, PNG compression."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from render_html import (
    FOOTER_CONTACT_EMAIL,
    FOOTER_REPO_URL,
    edition_footer_html,
    render_edition,
)
from render_images import EDITION_PNG_MAX_WIDTH, compress_edition_pngs


class PublicHtmlPolishTests(unittest.TestCase):

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

    def test_footer_uses_solvd_contact_and_repo(self):
        html = edition_footer_html()
        self.assertIn(FOOTER_CONTACT_EMAIL, html)
        self.assertIn(FOOTER_REPO_URL, html)
        self.assertNotIn("vanderbilt.edu", html)
        self.assertNotIn("AI-ESPRESSO-MAIN", html)

    def test_rendered_html_hides_source_tiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            edition = self._write_three_story_fixture(out)
            result = render_edition(edition, issue_num=99, editions_dir=out)
            html = Path(result["html_path"]).read_text()
            self.assertNotIn("source-tier", html)
            self.assertNotIn(">T1<", html)
            self.assertNotIn(">T2<", html)
            self.assertIn(FOOTER_REPO_URL, html)
            self.assertIn(FOOTER_CONTACT_EMAIL, html)
            self.assertIn('class="card-link" href="https://example.com/a"', html)
            self.assertIn('class="headline-link" href="https://example.com/b"', html)
            self.assertIn('class="source-link" href="https://example.com/c"', html)

    def test_render_rejects_two_story_editions(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            edition = out / "two-story.json"
            edition.write_text(
                json.dumps(
                    {
                        "date": "2026-05-18",
                        "stories": [
                            {
                                "slot": "business",
                                "headline": "Anthropic raises Claude usage limits",
                                "blurb": "Higher limits for paying users.",
                                "why_it_matters": "More work can run without context resets.",
                                "source_name": "Anthropic News",
                                "source_url": "https://example.com/a",
                                "tier": 1,
                            },
                            {
                                "slot": "engineer",
                                "headline": "Meta AI ships real-time video tracking update",
                                "blurb": "Faster model for live computer vision tasks.",
                                "why_it_matters": "Developers can prototype faster on edge workloads.",
                                "source_name": "Meta AI Blog",
                                "source_url": "https://example.com/b",
                                "tier": 1,
                            },
                        ],
                        "try_this_prompt": {
                            "title": "Try this prompt",
                            "prompt": "Summarize today's two strongest AI moves.",
                            "tool_hint": "Paste into your assistant.",
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "Edition has 2 stories; need 3\\."):
                render_edition(edition, issue_num=77, editions_dir=out)


class CompressEditionPngTests(unittest.TestCase):

    def test_compress_resizes_large_png(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.png"
            Image.new("RGB", (1024, 1024), "#f5f0e8").save(path)
            before = path.stat().st_size
            result = compress_edition_pngs([path], max_width=EDITION_PNG_MAX_WIDTH)
            self.assertEqual(result["compressed"], [str(path)])
            with Image.open(path) as img:
                self.assertLessEqual(max(img.size), EDITION_PNG_MAX_WIDTH)
            self.assertLess(path.stat().st_size, before)


if __name__ == "__main__":
    unittest.main()
