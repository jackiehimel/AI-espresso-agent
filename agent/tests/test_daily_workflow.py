"""Guards on production daily-edition workflow (no deterministic fallback in CI)."""

import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
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

    def test_failure_notification_has_optional_slack_webhook(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("name: Optional Slack failure notification", text)
        self.assertIn("SLACK_WEBHOOK_URL", text)
        self.assertIn("if [ -z \"${SLACK_WEBHOOK_URL:-}\" ]; then", text)
        self.assertIn("skipping Slack notification", text)
        self.assertIn("::warning::Slack failure notification failed", text)

    def test_render_output_is_parsed_without_stdin_heredoc_conflict(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("OUTPUT_JSON=\"$OUTPUT\" python - <<'PY'", text)
        self.assertIn("json.loads(os.environ[\"OUTPUT_JSON\"])", text)
        self.assertNotIn("echo \"$OUTPUT\" | python - <<'PY'", text)

    def test_render_sets_qotd_api_base_for_hosted_form(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("AI_ESPRESSO_QOTD_API_URL", text)
        self.assertIn("https://ai-garage-navy.vercel.app", text)

    def test_email_has_duplicate_send_guard(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("force_resend", text)
        self.assertIn("name: Send edition email", text)
        self.assertIn("id: dedupe", text)
        self.assertIn("already_sent", text)
        self.assertIn("Skipping email: edition for", text)

    def test_workflow_writes_publish_manifest(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("name: Write publish manifest for downstream consumers", text)
        self.assertIn("python write_publish_manifest.py", text)
        self.assertIn("--issue-num", text)
        self.assertIn("--source-repo", text)


class DedupeGuardBehaviorTests(unittest.TestCase):
    """Execute the exact bash from the duplicate-send guard against a real git
    repo to lock in correct behavior. Regression coverage for the bug where
    `git log --grep` returns exit 0 with no matches, which made the elif branch
    always set already_sent=true."""

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git not available")
        if shutil.which("bash") is None:
            self.skipTest("bash not available")
        self.tmp = Path(tempfile.mkdtemp(prefix="dedupe-guard-"))
        self._git("init", "-q", "-b", "main")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test")
        self._git("commit", "--allow-empty", "-q", "-m", "root")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _git(self, *args):
        subprocess.run(["git", *args], cwd=self.tmp, check=True, capture_output=True)

    def _extract_dedupe_script(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        match = re.search(
            r"- name: Check duplicate-send guard.*?^      - name:",
            text,
            re.DOTALL | re.MULTILINE,
        )
        self.assertIsNotNone(match, "could not locate dedupe guard step")
        step_block = match.group(0)
        lines = step_block.splitlines()
        try:
            run_idx = next(i for i, ln in enumerate(lines) if ln.strip() == "run: |")
        except StopIteration:  # pragma: no cover - structural
            self.fail("dedupe step has no `run: |` block")
        body_lines = []
        for ln in lines[run_idx + 1:]:
            if ln.startswith("      - name:"):
                break
            if ln.startswith("          "):
                body_lines.append(ln[10:])
            elif ln.strip() == "":
                body_lines.append("")
            else:
                break
        return "\n".join(body_lines)

    def _run_dedupe(self, date: str) -> str:
        script = self._extract_dedupe_script()
        github_output = self.tmp / "github_output"
        github_output.write_text("", encoding="utf-8")
        rendered = script.replace("${{ steps.date.outputs.value }}", date)
        wrapped = f"set -e\nexport GITHUB_OUTPUT={github_output}\n{rendered}\n"
        subprocess.run(
            ["bash", "-c", wrapped],
            cwd=self.tmp,
            check=True,
            capture_output=True,
        )
        return github_output.read_text(encoding="utf-8")

    def test_fresh_date_is_not_marked_already_sent(self):
        """No prior commit, file not tracked. already_sent must be false."""
        out = self._run_dedupe("2099-01-01")
        self.assertIn("already_sent=false", out, f"got: {out!r}")
        self.assertNotIn("already_sent=true", out)

    def test_already_committed_file_marks_already_sent(self):
        editions_dir = self.tmp / "agent" / "data" / "editions"
        editions_dir.mkdir(parents=True)
        (editions_dir / "2099-02-02.json").write_text("{}", encoding="utf-8")
        self._git("add", "agent/data/editions/2099-02-02.json")
        self._git("commit", "-q", "-m", "Daily edition for 2099-02-02")
        out = self._run_dedupe("2099-02-02")
        self.assertIn("already_sent=true", out, f"got: {out!r}")

    def test_commit_message_match_marks_already_sent_even_if_file_deleted(self):
        editions_dir = self.tmp / "agent" / "data" / "editions"
        editions_dir.mkdir(parents=True)
        target = editions_dir / "2099-03-03.json"
        target.write_text("{}", encoding="utf-8")
        self._git("add", "agent/data/editions/2099-03-03.json")
        self._git("commit", "-q", "-m", "Daily edition for 2099-03-03")
        self._git("rm", "-q", "agent/data/editions/2099-03-03.json")
        self._git("commit", "-q", "-m", "remove stale artifact")
        out = self._run_dedupe("2099-03-03")
        self.assertIn("already_sent=true", out, f"got: {out!r}")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    unittest.main()
