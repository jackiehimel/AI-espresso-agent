"""Tests for honest QOTD rendering (preview vs hosted API)."""

import os
import sys
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
        edition = Path(__file__).resolve().parent.parent / "data" / "editions" / "2026-05-19.json"
        if not edition.exists():
            self.skipTest("3-story fixture edition missing")
        env = {k: v for k, v in os.environ.items() if k != "AI_ESPRESSO_QOTD_API_URL"}
        with patch.dict(os.environ, env, clear=True):
            result = render_edition(edition, issue_num=99)
        html = Path(result["html_path"]).read_text()
        self.assertIn("Preview edition", html)
        self.assertNotIn('id="qotd-form"', html)
        Path(result["html_path"]).unlink(missing_ok=True)
        Path(result["md_path"]).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
