"""Phase 4 polish: public HTML footer, hidden tiers, PNG compression."""

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
        edition = Path(__file__).resolve().parent.parent / "data" / "editions" / "2026-05-16.json"
        if not edition.exists():
            self.skipTest("fixture edition missing")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            result = render_edition(edition, issue_num=99, editions_dir=out)
            html = Path(result["html_path"]).read_text()
            self.assertNotIn("source-tier", html)
            self.assertNotIn(">T1<", html)
            self.assertNotIn(">T2<", html)
            self.assertIn(FOOTER_REPO_URL, html)
            self.assertIn(FOOTER_CONTACT_EMAIL, html)


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
