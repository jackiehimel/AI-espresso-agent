"""Tests for load_env.strip_wrapping_quotes."""

from __future__ import annotations

import unittest

from load_env import strip_wrapping_quotes


class StripQuotesTests(unittest.TestCase):
    def test_straight_double_quotes(self):
        self.assertEqual(strip_wrapping_quotes('"abc"'), "abc")

    def test_smart_double_quotes(self):
        self.assertEqual(strip_wrapping_quotes("\u201cpplx-test-key\u201d"), "pplx-test-key")


if __name__ == "__main__":
    unittest.main()
