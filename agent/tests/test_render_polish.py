"""Phase 4 polish: public HTML footer, hidden tiers, PNG compression."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from render_html import (
    FOOTER_CONTACT_EMAIL,
    FOOTER_REPO_URL,
    edition_footer_html,
    render_edition,
)
from render_images import EDITION_PNG_MAX_WIDTH, compress_edition_pngs
from render_images import _can_reuse_existing_illustration, _prompt_digest_path
from render_images import _curated_scene
from render_images import _run_image_cli


class PublicHtmlPolishTests(unittest.TestCase):

    def _write_four_story_fixture(self, out_dir: Path) -> Path:
        edition = out_dir / "four-story.json"
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

    def test_footer_uses_solvd_contact_and_repo(self):
        html = edition_footer_html()
        self.assertIn(FOOTER_CONTACT_EMAIL, html)
        self.assertIn(FOOTER_REPO_URL, html)
        self.assertNotIn("vanderbilt.edu", html)
        self.assertNotIn("AI-ESPRESSO-MAIN", html)

    def test_rendered_html_hides_source_tiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            edition = self._write_four_story_fixture(out)
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
            with self.assertRaisesRegex(ValueError, "Edition has 2 stories; need 4\\."):
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


class IllustrationCacheTests(unittest.TestCase):

    def test_existing_image_reused_only_when_prompt_digest_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "variant_c_01.png"
            path.write_bytes(b"x" * 12_000)  # pass _illustration_ok size gate
            digest_path = _prompt_digest_path(path)
            digest_path.write_text("abc123", encoding="utf-8")
            self.assertFalse(_can_reuse_existing_illustration(path, "new prompt"))

            digest_path.write_text(
                hashlib.sha1("new prompt".encode("utf-8")).hexdigest(),
                encoding="utf-8",
            )
            self.assertTrue(_can_reuse_existing_illustration(path, "new prompt"))

    def test_run_image_cli_keeps_prior_image_on_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "variant_c_01"
            png = Path(str(base) + ".png")
            original = b"z" * 12_000
            png.write_bytes(original)

            with patch("render_images._find_asi_cli", return_value="/tmp/fake-cli"), patch(
                "subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="fake", timeout=300)
            ):
                ok = _run_image_cli("prompt changed", base, "1:1")

            self.assertTrue(ok)
            self.assertEqual(png.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
