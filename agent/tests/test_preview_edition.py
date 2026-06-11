"""Preview runner safety behavior."""

import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import preview_edition


def _write_minimal_edition_json(path: Path) -> None:
    payload = {
        "stories": [
            {"slot": "business", "headline": "Headline", "source_name": "Source"},
        ],
        "notes": ["mode: agent"],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class PreviewEditionTests(unittest.TestCase):
    def test_run_agent_defaults_to_skip_archive(self):
        def _assert_skip_archive(*args, **kwargs):
            self.assertEqual("1", os.environ.get("ESPRESSO_SKIP_ARCHIVE"))

        with patch.dict(os.environ, {}, clear=True):
            with patch("espresso_agent.run", side_effect=_assert_skip_archive) as mock_run:
                preview_edition._run_agent(
                    dt.date(2099, 1, 1),
                    use_cache=True,
                    write_archive=False,
                )

        self.assertNotIn("ESPRESSO_SKIP_ARCHIVE", os.environ)
        mock_run.assert_called_once_with(
            dt.date(2099, 1, 1), dry_run=False, use_cache=True, mode="agent"
        )

    def test_run_agent_write_archive_opt_in(self):
        def _assert_no_forced_skip(*args, **kwargs):
            self.assertNotIn("ESPRESSO_SKIP_ARCHIVE", os.environ)

        with patch.dict(os.environ, {}, clear=True):
            with patch("espresso_agent.run", side_effect=_assert_no_forced_skip) as mock_run:
                preview_edition._run_agent(
                    dt.date(2099, 1, 2),
                    use_cache=False,
                    write_archive=True,
                )

        mock_run.assert_called_once_with(
            dt.date(2099, 1, 2), dry_run=False, use_cache=False, mode="agent"
        )

    def test_main_render_only_skips_agent_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_editions = Path(tmp) / "editions"
            _write_minimal_edition_json(data_editions / "2099-01-01.json")
            html_path = Path(tmp) / "edition.html"
            html_path.write_text("<html></html>", encoding="utf-8")

            with patch.object(sys, "argv", ["preview_edition.py", "2099-01-01", "--render-only", "--no-open"]):
                with patch("preview_edition.DATA_EDITIONS_DIR", data_editions):
                    with patch("preview_edition.load_env_file"):
                        with patch("preview_edition._run_agent") as mock_run_agent:
                            with patch(
                                "preview_edition._run_render",
                                return_value={"html_path": str(html_path), "md_path": "x.md"},
                            ):
                                with patch("preview_edition._print_picks"):
                                    with patch("preview_edition._serve_and_open"):
                                        rc = preview_edition.main()

            self.assertEqual(0, rc)
            mock_run_agent.assert_not_called()

    def test_main_passes_write_archive_flag_to_agent(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_editions = Path(tmp) / "editions"
            target_json = data_editions / "2099-01-03.json"
            html_path = Path(tmp) / "edition.html"
            html_path.write_text("<html></html>", encoding="utf-8")

            def _fake_run_agent(date, use_cache, write_archive):
                _write_minimal_edition_json(target_json)

            with patch.object(
                sys,
                "argv",
                ["preview_edition.py", "2099-01-03", "--write-archive", "--no-open"],
            ):
                with patch("preview_edition.DATA_EDITIONS_DIR", data_editions):
                    with patch("preview_edition.load_env_file"):
                        with patch("preview_edition._run_agent", side_effect=_fake_run_agent) as mock_run_agent:
                            with patch(
                                "preview_edition._run_render",
                                return_value={"html_path": str(html_path), "md_path": "x.md"},
                            ):
                                with patch("preview_edition._print_picks"):
                                    with patch("preview_edition._serve_and_open"):
                                        rc = preview_edition.main()

            self.assertEqual(0, rc)
            _, kwargs = mock_run_agent.call_args
            self.assertTrue(kwargs["write_archive"])


if __name__ == "__main__":
    unittest.main()
