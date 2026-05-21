"""Tests for email delivery HTML rewrites."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from send_email import (
    _build_email_safe_html,
    _stabilize_inline_img_tags,
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
        self.assertIn('max-width:420px', patched)
        self.assertIn("Try this prompt", patched)
        self.assertIn("color:#00955A", patched)

    def test_inline_cid_images_get_explicit_dimensions(self):
        html = '<img class="card-image" src="cid:abc123">'
        patched = _stabilize_inline_img_tags(html)

        self.assertIn('width="160"', patched)
        self.assertIn('height="160"', patched)
        self.assertIn("max-width:160px", patched)
        self.assertIn('src="cid:abc123"', patched)


if __name__ == "__main__":
    unittest.main()
