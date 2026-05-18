"""write_edition archive append and ESPRESSO_SKIP_ARCHIVE."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from espresso_agent import Edition, Story, write_edition


def _minimal_edition(date: str = "2099-01-01") -> Edition:
    return Edition(
        date=date,
        stories=[
            Story(
                slot="business",
                headline="Test headline",
                blurb="blurb",
                why_it_matters="why",
                source_name="Test Source",
                source_url="https://example.com/a",
                tier=1,
                original_headline="Test headline",
                fingerprint="fp-test-001",
            )
        ],
        try_this_prompt={"title": "Prompt", "body": "Do the thing."},
        daily_question="Question?",
        generated_at="2099-01-01T00:00:00Z",
    )


class WriteEditionArchiveTests(unittest.TestCase):

    @patch("espresso_agent.append_archive")
    def test_appends_archive_when_skip_unset(self, mock_append):
        with tempfile.TemporaryDirectory() as tmp:
            editions = Path(tmp) / "editions"
            editions.mkdir()
            env = {k: v for k, v in os.environ.items() if k != "ESPRESSO_SKIP_ARCHIVE"}
            with patch.dict(os.environ, env, clear=True):
                with patch("espresso_agent.EDITIONS_DIR", editions):
                    out = write_edition(_minimal_edition(), dry_run=False)
            self.assertTrue(out.exists())
            mock_append.assert_called_once()

    @patch("espresso_agent.append_archive")
    def test_skips_archive_when_skip_env_1(self, mock_append):
        with tempfile.TemporaryDirectory() as tmp:
            editions = Path(tmp) / "editions"
            editions.mkdir()
            with patch.dict(os.environ, {"ESPRESSO_SKIP_ARCHIVE": "1"}, clear=False):
                with patch("espresso_agent.EDITIONS_DIR", editions):
                    out = write_edition(_minimal_edition(), dry_run=False)
            self.assertTrue(out.exists())
            mock_append.assert_not_called()

    @patch("espresso_agent.append_archive")
    def test_appends_archive_when_skip_env_0(self, mock_append):
        with tempfile.TemporaryDirectory() as tmp:
            editions = Path(tmp) / "editions"
            editions.mkdir()
            with patch.dict(os.environ, {"ESPRESSO_SKIP_ARCHIVE": "0"}, clear=False):
                with patch("espresso_agent.EDITIONS_DIR", editions):
                    write_edition(_minimal_edition(), dry_run=False)
            mock_append.assert_called_once()

    @patch("espresso_agent.append_archive")
    def test_dry_run_never_appends(self, mock_append):
        with tempfile.TemporaryDirectory() as tmp:
            editions = Path(tmp) / "editions"
            editions.mkdir()
            with patch.dict(os.environ, {"ESPRESSO_SKIP_ARCHIVE": "1"}, clear=False):
                with patch("espresso_agent.EDITIONS_DIR", editions):
                    out = write_edition(_minimal_edition(), dry_run=True)
            self.assertFalse(out.exists())
            mock_append.assert_not_called()


if __name__ == "__main__":
    unittest.main()
