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

    def test_footer_uses_solvd_contact_and_repo(self):
        html = edition_footer_html()
        self.assertIn(FOOTER_CONTACT_EMAIL, html)
        self.assertIn(FOOTER_REPO_URL, html)
        self.assertNotIn("vanderbilt.edu", html)
        self.assertNotIn("AI-ESPRESSO-MAIN", html)

    def test_rendered_html_hides_source_tiers(self):
        editions_dir = Path(__file__).resolve().parent.parent / "data" / "editions"
        edition = editions_dir / "2026-05-19.json"
        if not edition.exists():
            self.skipTest("3-story fixture edition missing")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            result = render_edition(edition, issue_num=99, editions_dir=out)
            html = Path(result["html_path"]).read_text()
            self.assertNotIn("source-tier", html)
            self.assertNotIn(">T1<", html)
            self.assertNotIn(">T2<", html)
            self.assertIn(FOOTER_REPO_URL, html)
            self.assertIn(FOOTER_CONTACT_EMAIL, html)

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
