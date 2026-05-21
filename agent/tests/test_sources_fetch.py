"""Verify enabled sources in sources.yaml return fetchable content."""

from __future__ import annotations

import os
import unittest

import yaml

from espresso_agent import extract_candidates, fetch_url, load_sources

CI_OPTIONAL_FLAKY_FEEDS = {
    "The Information — AI",
    "Don't Worry About the Vase (Zvi Mowshowitz)",
    "FreightWaves — AI",
    "Microsoft AI Blog",
    "Rest of World",
    "Import AI (Jack Clark)",
}


@unittest.skipIf(
    os.environ.get("ESPRESSO_SKIP_NETWORK_TESTS") == "1",
    "set ESPRESSO_SKIP_NETWORK_TESTS=1 to skip",
)
class EnabledSourcesFetchTests(unittest.TestCase):
    def test_ci_optional_flaky_feeds_cover_known_unstable_endpoints(self):
        self.assertIn("Rest of World", CI_OPTIONAL_FLAKY_FEEDS)
        self.assertIn("Import AI (Jack Clark)", CI_OPTIONAL_FLAKY_FEEDS)

    def test_each_enabled_source_returns_html(self):
        sources, _rules = load_sources()
        enabled = [s for s in sources if s.enabled]
        self.assertGreater(len(enabled), 10)
        failures: list[str] = []
        optional_failures: list[str] = []
        running_on_ci = os.environ.get("GITHUB_ACTIONS") == "true"
        for s in enabled:
            body = fetch_url(s.url, use_cache=False, prestige=s.prestige or s.paywall)
            if not body or len(body) < 500:
                msg = f"{s.name} ({s.url})"
                if running_on_ci and s.name in CI_OPTIONAL_FLAKY_FEEDS:
                    optional_failures.append(msg)
                else:
                    failures.append(msg)
                continue
            if s.kind == "rss" and not extract_candidates(body, s, max_n=1):
                msg = f"{s.name} ({s.url}) — RSS returned no items"
                if running_on_ci and s.name in CI_OPTIONAL_FLAKY_FEEDS:
                    optional_failures.append(msg)
                else:
                    failures.append(msg)
        if optional_failures:
            print("optional CI feed failures:\n" + "\n".join(optional_failures))
        self.assertFalse(failures, "fetch failed:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
