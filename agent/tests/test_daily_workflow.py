"""Guards on production daily-edition workflow (no deterministic fallback in CI)."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

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
        self.assertIn("::error::Slack failure notification failed", text)

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

    def test_dedupe_runs_before_generate(self):
        """Regression: dedupe must come before generate so a committed edition
        cannot be silently overwritten by a re-run and duplicate sends are
        always prevented before any expensive generation step."""
        text = WORKFLOW.read_text(encoding="utf-8")
        dedupe_idx = text.find("- name: Check duplicate-send guard")
        generate_idx = text.find("- name: Generate edition JSON (agent mode)")
        render_idx = text.find("- name: Render HTML + Markdown + illustrations")
        send_idx = text.find("- name: Send edition email")
        self.assertGreater(dedupe_idx, 0)
        self.assertGreater(generate_idx, 0)
        self.assertGreater(render_idx, 0)
        self.assertGreater(send_idx, 0)
        self.assertLess(dedupe_idx, generate_idx, "dedupe must precede generate")
        self.assertLess(generate_idx, render_idx, "generate must precede render")
        self.assertLess(render_idx, send_idx, "render must precede send")

    def test_generate_step_is_gated_on_dedupe(self):
        """Generate step must be skipped when the edition is already committed
        so a recovery re-run cannot overwrite a shipped slate."""
        text = WORKFLOW.read_text(encoding="utf-8")
        match = re.search(
            r"- name: Generate edition JSON \(agent mode\)\n(.*?)\n      - name:",
            text,
            re.DOTALL,
        )
        self.assertIsNotNone(match, "could not locate generate step")
        block = match.group(1)
        self.assertIn("if:", block, f"generate step missing `if:` gate: {block!r}")
        self.assertIn("steps.dedupe.outputs.already_sent", block)
        self.assertIn("!= 'true'", block)

    def test_render_step_is_not_gated(self):
        """Render must always run so it produces html_path/md_path/issue_num
        outputs that the send and manifest steps depend on, even during a
        recovery re-run where generate is skipped."""
        text = WORKFLOW.read_text(encoding="utf-8")
        match = re.search(
            r"- name: Render HTML \+ Markdown \+ illustrations\n(.*?)\n        run:",
            text,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        header = match.group(1)
        self.assertNotIn("if:", header, f"render must not have `if:`: {header!r}")


class FailureNotificationBehaviorTests(unittest.TestCase):
    """Run the workflow's real shell with a local fake transport, never Slack."""

    def _notify(self, webhook="", curl_status=0, response="ok"):
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        step = next(
            step for step in workflow["jobs"]["notify-failure"]["steps"]
            if "SLACK_WEBHOOK_URL" in step.get("env", {})
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            curl = root / "curl"
            curl.write_text(
                "#!/bin/sh\n"
                "cp payload.json sent-payload.json\n"
                "printf '%s' \"$FAKE_RESPONSE\"\n"
                "exit \"$FAKE_STATUS\"\n",
                encoding="utf-8",
            )
            curl.chmod(0o755)
            summary = root / "summary.md"
            summary.touch()
            result = subprocess.run(
                ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
                cwd=root,
                env={
                    "PATH": str(root) + os.pathsep + os.environ["PATH"],
                    "SLACK_WEBHOOK_URL": webhook,
                    "RUN_URL": "https://github.com/example/repo/actions/runs/123",
                    "GITHUB_STEP_SUMMARY": str(summary),
                    "FAKE_STATUS": str(curl_status),
                    "FAKE_RESPONSE": response,
                },
                text=True,
                capture_output=True,
            )
            payload = root / "sent-payload.json"
            return result, summary.read_text(), json.loads(payload.read_text()) if payload.exists() else None

    def test_unset_webhook_reports_no_alert_and_fails(self):
        result, summary, payload = self._notify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("::error::", result.stdout)
        self.assertIn("not delivered", summary)
        self.assertIsNone(payload)

    def test_transport_or_http_failure_reports_no_alert_and_fails(self):
        for status in (6, 22, 28):
            with self.subTest(curl_status=status):
                result, summary, _ = self._notify("https://example.invalid/hook", status)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("not delivered", summary)
                self.assertNotIn("Slack alert delivered", summary)

    def test_http_success_with_rejected_body_fails(self):
        result, summary, _ = self._notify("https://example.invalid/hook", response="invalid_payload")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not delivered", summary)

    def test_confirmed_delivery_succeeds_and_links_run(self):
        result, summary, payload = self._notify("https://example.invalid/hook")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Slack alert delivered", summary)
        self.assertIn("https://github.com/example/repo/actions/runs/123", json.dumps(payload))


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
