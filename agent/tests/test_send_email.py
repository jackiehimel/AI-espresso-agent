"""Tests for email delivery HTML rewrites."""

import os
import tempfile
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from send_email import (
    EMAIL_CARD_MIN_HEIGHT,
    EMAIL_IMAGE_ROW_HEIGHT,
    _build_email_safe_html,
    _stabilize_inline_img_tags,
    send_edition_email,
)


class EmailHtmlTests(unittest.TestCase):
    def test_build_email_safe_html_uses_table_layout(self):
        html = """
<html><body><div class="container">
  <header class="masthead">
    <h1 class="wordmark">ai espresso<span class="cup">&nbsp;☕</span></h1>
    <p class="tagline">your morning cup of AI</p>
    <p class="dateline">Thu &middot; May 21 &middot; 2026</p>
  </header>
  <section class="edition-grid">
    <div class="story-cards">
      <article class="card">
        <a class="card-link" href="https://example.com/story">
          <img class="card-image" src="cid:img1" alt="story">
        </a>
        <div class="card-body">
          <p class="category market">MARKET</p>
          <h2 class="headline"><a class="headline-link" href="https://example.com/story">Headline</a></h2>
          <p class="kicker"><strong>$5B</strong> kicker</p>
          <p class="source"><a class="source-link" href="https://example.com/story">Source</a> · May 21</p>
        </div>
      </article>
    </div>
    <article class="card prompt-card">
      <div class="card-body">
        <p class="prompt-tag">Try this prompt</p>
        <h2 class="prompt-title">Title</h2>
        <p class="prompt-tool-hint">Hint</p>
        <div class="prompt-code">Prompt body</div>
      </div>
    </article>
  </section>
  <footer class="footer">footer</footer>
</div></body></html>
"""
        patched = _build_email_safe_html(html)
        self.assertIn('<table role="presentation"', patched)
        self.assertIn('max-width:680px', patched)
        self.assertIn('max-width:310px', patched)
        self.assertIn("Try this prompt", patched)
        self.assertIn("color:#00955A", patched)

    def test_story_cards_equalize_height_within_row(self):
        """Cards must render at equal heights even when kicker text wraps to
        different line counts. We enforce this with a fixed pixel minimum
        height (HTML attribute + CSS) on the inner card table because
        percentage heights don't resolve reliably without a definite
        height somewhere up the ancestor chain.
        """
        html = """
<html><body><div class="container">
  <header class="masthead"><h1 class="wordmark">ai espresso</h1>
    <p class="tagline">tagline</p><p class="dateline">DATE</p></header>
  <section class="edition-grid"><div class="story-cards">
    <article class="card"><a class="card-link" href="https://a.example">
      <img class="card-image" src="cid:img1" alt="a"></a>
      <div class="card-body"><p class="category news">NEWS</p>
        <h2 class="headline"><a class="headline-link" href="https://a.example">Short A</a></h2>
        <p class="kicker">Tiny.</p>
        <p class="source"><a class="source-link" href="https://a.example">Src A</a> · today</p>
      </div></article>
    <article class="card"><a class="card-link" href="https://b.example">
      <img class="card-image" src="cid:img2" alt="b"></a>
      <div class="card-body"><p class="category news">NEWS</p>
        <h2 class="headline"><a class="headline-link" href="https://b.example">Headline B</a></h2>
        <p class="kicker">A much longer kicker that will wrap to multiple lines and
        force this card to be taller than its sibling unless the inner card
        table is pinned to a fixed minimum height.</p>
        <p class="source"><a class="source-link" href="https://b.example">Src B</a> · today</p>
      </div></article>
  </div></section>
  <footer class="footer">footer</footer>
</div></body></html>
"""
        patched = _build_email_safe_html(html)
        h = EMAIL_CARD_MIN_HEIGHT

        # Every card table must carry the HTML height attribute (Outlook/Word engine).
        self.assertEqual(
            patched.count(f'height="{h}"'),
            2,
            f"expected height='{h}' on both inner card tables",
        )
        # Plus matching CSS height + min-height so modern clients honor the floor too.
        self.assertEqual(
            patched.count(f"height:{h}px"),
            4,
            "expected each card to carry both 'height:Npx' and 'min-height:Npx'",
        )
        self.assertEqual(patched.count(f"min-height:{h}px"), 2)
        self.assertEqual(patched.count(f'height="{EMAIL_IMAGE_ROW_HEIGHT}"'), 2)
        self.assertEqual(patched.count(f"height:{EMAIL_IMAGE_ROW_HEIGHT}px"), 2)

    def test_inline_cid_images_get_explicit_dimensions(self):
        html = '<img class="card-image" src="cid:abc123">'
        patched = _stabilize_inline_img_tags(html)

        self.assertIn('width="160"', patched)
        self.assertIn('height="160"', patched)
        self.assertIn("max-width:160px", patched)
        self.assertIn('src="cid:abc123"', patched)

    def test_stale_guard_blocks_issue_mismatch_for_expected_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            editions = root / "editions"
            editions.mkdir(parents=True, exist_ok=True)
            html_path = editions / "edition_7_variant_c.html"
            html_path.write_text("<html><body><div class='container'></div></body></html>", encoding="utf-8")
            md_path = editions / "edition_7_variant_c.md"
            md_path.write_text("fallback text", encoding="utf-8")

            data_dir = root / "agent" / "data" / "editions"
            data_dir.mkdir(parents=True, exist_ok=True)
            (data_dir / "2026-05-28.json").write_text(
                json.dumps({"date": "2026-05-28", "issue_num": 6}),
                encoding="utf-8",
            )

            old_env = dict(os.environ)
            try:
                os.environ["AI_ESPRESSO_FROM"] = "sender@example.com"
                os.environ["AI_ESPRESSO_TO"] = "to@example.com"
                os.environ["AI_ESPRESSO_DRY_RUN"] = "1"
                os.environ["AI_ESPRESSO_EXPECTED_DATE"] = "2026-05-28"
                result = send_edition_email(html_path, md_path)
            finally:
                os.environ.clear()
                os.environ.update(old_env)

            self.assertFalse(result["sent"])
            self.assertIn("stale-send guard", result["reason"])

    def test_no_resend_guard_blocks_previously_sent_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            editions = root / "editions"
            editions.mkdir(parents=True, exist_ok=True)
            html_path = editions / "edition_7_variant_c.html"
            html_path.write_text("<html><body><div class='container'></div></body></html>", encoding="utf-8")
            md_path = editions / "edition_7_variant_c.md"
            md_path.write_text("fallback text", encoding="utf-8")

            data_dir = root / "agent" / "data" / "editions"
            data_dir.mkdir(parents=True, exist_ok=True)
            (data_dir / "2026-05-28.json").write_text(
                json.dumps({"date": "2026-05-28", "issue_num": 7}),
                encoding="utf-8",
            )
            sent_log = root / "agent" / "data" / "sent_editions.json"
            sent_log.write_text(
                json.dumps({"schema_version": 1, "sent_dates": ["2026-05-28"]}),
                encoding="utf-8",
            )

            old_env = dict(os.environ)
            try:
                os.environ["AI_ESPRESSO_FROM"] = "sender@example.com"
                os.environ["AI_ESPRESSO_TO"] = "to@example.com"
                os.environ["AI_ESPRESSO_DRY_RUN"] = "1"
                os.environ["AI_ESPRESSO_EXPECTED_DATE"] = "2026-05-28"
                result = send_edition_email(html_path, md_path)
            finally:
                os.environ.clear()
                os.environ.update(old_env)

            self.assertFalse(result["sent"])
            self.assertIn("no-resend policy", result["reason"])

    def test_successful_send_records_date_and_blocks_repeat(self):
        class _FakeSMTP:
            def __init__(self, *args, **kwargs):
                self.sent = False

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def starttls(self):
                return None

            def login(self, *_args, **_kwargs):
                return None

            def send_message(self, *_args, **_kwargs):
                self.sent = True

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            editions = root / "editions"
            editions.mkdir(parents=True, exist_ok=True)
            html_path = editions / "edition_8_variant_c.html"
            html_path.write_text("<html><body><div class='container'></div></body></html>", encoding="utf-8")
            md_path = editions / "edition_8_variant_c.md"
            md_path.write_text("fallback text", encoding="utf-8")

            data_dir = root / "agent" / "data" / "editions"
            data_dir.mkdir(parents=True, exist_ok=True)
            (data_dir / "2026-05-31.json").write_text(
                json.dumps({"date": "2026-05-31", "issue_num": 8}),
                encoding="utf-8",
            )

            old_env = dict(os.environ)
            try:
                os.environ["AI_ESPRESSO_FROM"] = "sender@example.com"
                os.environ["AI_ESPRESSO_TO"] = "to@example.com"
                os.environ["GMAIL_APP_PASSWORD"] = "pw"
                os.environ["AI_ESPRESSO_EXPECTED_DATE"] = "2026-05-31"
                os.environ.pop("AI_ESPRESSO_DRY_RUN", None)
                with patch("send_email.smtplib.SMTP", _FakeSMTP):
                    first = send_edition_email(html_path, md_path)
                    second = send_edition_email(html_path, md_path)
            finally:
                os.environ.clear()
                os.environ.update(old_env)

            self.assertTrue(first["sent"])
            self.assertFalse(second["sent"])
            self.assertIn("no-resend policy", second["reason"])


if __name__ == "__main__":
    unittest.main()
