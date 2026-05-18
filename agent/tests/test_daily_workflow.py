"""Guards on production daily-edition workflow (no deterministic fallback in CI)."""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "daily-edition.yml"


class DailyEditionWorkflowTests(unittest.TestCase):

    def test_workflow_exists(self):
        self.assertTrue(WORKFLOW.is_file(), f"missing {WORKFLOW}")

    def test_production_uses_agent_mode_only(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("mode='agent'", text)
        self.assertNotIn("mode='deterministic'", text)
        self.assertNotIn('mode="deterministic"', text)

    def test_production_never_sets_deterministic_fallback_env(self):
        for line in WORKFLOW.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            self.assertNotIn(
                "ESPRESSO_ALLOW_DETERMINISTIC_FALLBACK",
                line,
                f"production workflow must not set fallback env: {line!r}",
            )


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    unittest.main()
