"""write_edition archive append and ESPRESSO_SKIP_ARCHIVE."""

import os
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from espresso_agent import (
    Edition,
    Story,
    append_archive,
    load_archive,
    recent_archive_headlines,
    write_edition,
)


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


def _archive_rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


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

    def test_append_archive_upserts_same_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_file = Path(tmp) / "archive.jsonl"
            with patch("espresso_agent.ARCHIVE_FILE", archive_file):
                append_archive(_minimal_edition(date="2099-01-01"))
                append_archive(_minimal_edition(date="2099-01-01"))
            rows = _archive_rows(archive_file)
            self.assertEqual(1, len(rows))
            self.assertEqual("2099-01-01", rows[0]["date"])
            self.assertEqual(["fp-test-001"], rows[0]["fingerprints"])

    def test_append_archive_compacts_existing_duplicate_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_file = Path(tmp) / "archive.jsonl"
            archive_file.write_text(
                "\n".join([
                    json.dumps({"date": "2026-05-18", "fingerprints": ["old"], "headlines": ["old"]}),
                    json.dumps({"date": "2026-05-18", "fingerprints": ["new"], "headlines": ["new"]}),
                ])
                + "\n",
                encoding="utf-8",
            )
            with patch("espresso_agent.ARCHIVE_FILE", archive_file):
                append_archive(_minimal_edition(date="2026-05-19"))
            rows = _archive_rows(archive_file)
            self.assertEqual(2, len(rows))
            by_date = {row["date"]: row for row in rows}
            self.assertEqual(["new"], by_date["2026-05-18"]["fingerprints"])
            self.assertEqual(["fp-test-001"], by_date["2026-05-19"]["fingerprints"])

    def test_load_archive_uses_last_row_per_duplicate_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_file = Path(tmp) / "archive.jsonl"
            archive_file.write_text(
                "\n".join([
                    json.dumps({
                        "date": "2099-01-01",
                        "fingerprints": ["stale-fp"],
                        "headlines": ["stale"],
                    }),
                    json.dumps({
                        "date": "2099-01-01",
                        "fingerprints": ["winning-fp"],
                        "headlines": ["winning"],
                    }),
                ])
                + "\n",
                encoding="utf-8",
            )
            with patch("espresso_agent.ARCHIVE_FILE", archive_file):
                fps = load_archive(days=3650)
            self.assertEqual({"winning-fp"}, fps)
            self.assertNotIn("stale-fp", fps)

    def test_recent_archive_headlines_uses_last_row_per_duplicate_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_file = Path(tmp) / "archive.jsonl"
            archive_file.write_text(
                "\n".join([
                    json.dumps({
                        "date": "2098-12-31",
                        "fingerprints": ["a"],
                        "headlines": ["older headline"],
                    }),
                    json.dumps({
                        "date": "2099-01-01",
                        "fingerprints": ["b"],
                        "headlines": ["stale headline"],
                    }),
                    json.dumps({
                        "date": "2099-01-01",
                        "fingerprints": ["c"],
                        "headlines": ["newest headline"],
                    }),
                ])
                + "\n",
                encoding="utf-8",
            )
            with patch("espresso_agent.ARCHIVE_FILE", archive_file):
                headlines = recent_archive_headlines(5)
            self.assertEqual(["newest headline", "older headline"], headlines)


if __name__ == "__main__":
    unittest.main()
