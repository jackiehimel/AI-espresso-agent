"""Verify enabled sources in sources.yaml return fetchable content."""

from __future__ import annotations

import os
import unittest

import yaml

from espresso_agent import fetch_url, load_sources


@unittest.skipIf(
    os.environ.get("ESPRESSO_SKIP_NETWORK_TESTS") == "1",
    "set ESPRESSO_SKIP_NETWORK_TESTS=1 to skip",
)
class EnabledSourcesFetchTests(unittest.TestCase):
    def test_each_enabled_source_returns_html(self):
        sources, _rules = load_sources()
        enabled = [s for s in sources if s.enabled]
        self.assertGreater(len(enabled), 10)
        failures: list[str] = []
        for s in enabled:
            html = fetch_url(s.url, use_cache=False, prestige=s.prestige or s.paywall)
            if not html or len(html) < 500:
                failures.append(f"{s.name} ({s.url})")
        self.assertFalse(failures, "fetch failed:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
