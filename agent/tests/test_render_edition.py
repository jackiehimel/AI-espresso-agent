"""Tests for render_edition CLI exit codes."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from render_edition import exit_code_for_images


class RenderEditionExitTests(unittest.TestCase):

    def test_missing_images_exit_1_by_default(self):
        self.assertEqual(
            exit_code_for_images({"generated": [], "missing": ["/a.png"]}, allow_missing=False),
            1,
        )

    def test_missing_images_allowed(self):
        self.assertEqual(
            exit_code_for_images({"generated": [], "missing": ["/a.png"]}, allow_missing=True),
            0,
        )

    def test_all_images_ok(self):
        self.assertEqual(
            exit_code_for_images({"generated": ["/a.png"], "missing": []}, allow_missing=False),
            0,
        )


if __name__ == "__main__":
    unittest.main()
