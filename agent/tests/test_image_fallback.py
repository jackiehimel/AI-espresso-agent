import contextlib
import importlib.machinery
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from render_images import _run_image_cli


_CLI_PATH = Path(__file__).resolve().parent.parent / "bin" / "asi-generate-image"
_LOADER = importlib.machinery.SourceFileLoader("asi_generate_image", str(_CLI_PATH))
_SPEC = importlib.util.spec_from_loader("asi_generate_image", _LOADER)
cli = importlib.util.module_from_spec(_SPEC)
_LOADER.exec_module(cli)


class GeminiModelFallbackTests(unittest.TestCase):

    def test_defaults_use_current_stable_models(self):
        with mock.patch.dict(cli.os.environ, {}, clear=True):
            self.assertEqual(
                cli._gemini_models(),
                ("gemini-3.1-flash-image", "gemini-2.5-flash-image"),
            )

    def test_removed_primary_model_falls_back_to_stable_model(self):
        seen_models = []

        def generate(prompt, out_path, aspect_ratio, model=None):
            seen_models.append(model)
            if model == "removed-preview-model":
                raise RuntimeError("404 NOT_FOUND: model is not supported")
            out_path.write_bytes(b"\x89PNG" + b"0" * 20_000)

        with tempfile.TemporaryDirectory() as tmp:
            stem = Path(tmp) / "illustration"
            request = {
                "prompt": "A newspaper engraving of a coffee cup",
                "filename": str(stem),
                "aspect_ratio": "1:1",
            }
            env = {
                "GEMINI_API_KEY": "test-key",
                "GEMINI_IMAGE_MODEL": "removed-preview-model",
                "GEMINI_IMAGE_FALLBACK_MODEL": "stable-model",
            }
            stderr = io.StringIO()
            with (
                mock.patch.dict(cli.os.environ, env, clear=True),
                mock.patch.object(cli, "_generate_gemini", side_effect=generate),
                mock.patch.object(cli.sys, "argv", ["asi-generate-image", cli.json.dumps(request)]),
                contextlib.redirect_stderr(stderr),
            ):
                result = cli.main()

        self.assertEqual(result, 0)
        self.assertEqual(seen_models, ["removed-preview-model", "stable-model"])
        self.assertIn("trying stable-model", stderr.getvalue())
        self.assertIn("backend: gemini [stable-model]", stderr.getvalue())

    def test_gemini_billing_failure_falls_back_to_openai(self):
        seen_models = []
        openai_calls = []

        def generate_gemini(prompt, out_path, aspect_ratio, model=None):
            seen_models.append(model)
            raise RuntimeError("402 RESOURCE_EXHAUSTED: prepayment credits are depleted")

        def generate_openai(prompt, out_path, aspect_ratio):
            openai_calls.append(prompt)
            out_path.write_bytes(b"\x89PNG" + b"0" * 20_000)

        with tempfile.TemporaryDirectory() as tmp:
            stem = Path(tmp) / "illustration"
            request = {
                "prompt": "A newspaper engraving of a coffee cup",
                "filename": str(stem),
                "aspect_ratio": "1:1",
            }
            env = {
                "GEMINI_API_KEY": "test-gemini-key",
                "OPENAI_API_KEY": "test-openai-key",
            }
            stderr = io.StringIO()
            with (
                mock.patch.dict(cli.os.environ, env, clear=True),
                mock.patch.object(cli, "_generate_gemini", side_effect=generate_gemini),
                mock.patch.object(cli, "_generate_openai", side_effect=generate_openai),
                mock.patch.object(cli.sys, "argv", ["asi-generate-image", cli.json.dumps(request)]),
                contextlib.redirect_stderr(stderr),
            ):
                result = cli.main()

        self.assertEqual(result, 0)
        self.assertEqual(
            seen_models,
            ["gemini-3.1-flash-image", "gemini-2.5-flash-image"],
        )
        self.assertEqual(len(openai_calls), 1)
        self.assertIn("trying openai", stderr.getvalue())
        self.assertIn("backend: openai", stderr.getvalue())


class ImageCliDiagnosticsTests(unittest.TestCase):

    def test_failure_logs_include_complete_backend_error(self):
        hidden_error = "x" * 350 + " OPENAI_FALLBACK_FAILURE"
        completed = mock.Mock(returncode=1, stderr=hidden_error)

        with tempfile.TemporaryDirectory() as tmp:
            stem = Path(tmp) / "illustration"
            stderr = io.StringIO()
            with (
                mock.patch("render_images._find_asi_cli", return_value="/tmp/fake-cli"),
                mock.patch("render_images.subprocess.run", return_value=completed),
                contextlib.redirect_stderr(stderr),
            ):
                self.assertFalse(_run_image_cli("prompt", stem, "1:1"))

        self.assertIn("OPENAI_FALLBACK_FAILURE", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
