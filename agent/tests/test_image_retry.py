# tests for the asi-generate-image retry/fallback chain:
#   - _is_retryable: transient Gemini failures (503, timeout, empty response)
#     retry; auth/config errors fail fast
#   - _generate_with_retries: backoff between attempts, stops on success or
#     non-retryable, raises after exhausting attempts
#   - _run_chain: primary model exhausted -> stable fallback model
#   - _extract_image_bytes: response.parts=None must not crash
#
# run from agent/ dir:
#   python -m unittest tests.test_image_retry

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_CLI_PATH = Path(__file__).resolve().parent.parent / "bin" / "asi-generate-image"
_loader = importlib.machinery.SourceFileLoader("asi_generate_image", str(_CLI_PATH))
_spec = importlib.util.spec_from_loader("asi_generate_image", _loader)
cli = importlib.util.module_from_spec(_spec)
_loader.exec_module(cli)


def _fake_image_writer(fail_times: int, exc: Exception):
    """Generate fn that raises `exc` for the first `fail_times` calls, then writes a valid PNG-sized file."""
    calls = []

    def gen(prompt, out_path, aspect_ratio, model=None):
        calls.append(model)
        if len(calls) <= fail_times:
            raise exc
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"\x89PNG" + b"0" * 20_000)

    gen.calls = calls
    return gen


GEMINI_503 = RuntimeError(
    "503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is "
    "currently experiencing high demand.', 'status': 'UNAVAILABLE'}}"
)


class IsRetryableTests(unittest.TestCase):

    def test_transient_errors_retry(self):
        for msg in (
            str(GEMINI_503),
            "504 deadline exceeded",
            "Request timed out",
            "429 RESOURCE_EXHAUSTED rate limit",
            "500 INTERNAL server error",
            "Gemini returned no image part",
            "image too small",
        ):
            self.assertTrue(cli._is_retryable(RuntimeError(msg)), msg)

    def test_config_errors_fail_fast(self):
        for msg in (
            "GEMINI_API_KEY not set",
            "400 INVALID_ARGUMENT API key not valid",
            "403 PERMISSION_DENIED",
        ):
            self.assertFalse(cli._is_retryable(RuntimeError(msg)), msg)

    # 429 RESOURCE_EXHAUSTED means "rate limit" OR "out of credits" — the
    # billing flavor must fail fast, not burn 30s of retries per image
    def test_billing_429_fails_fast(self):
        self.assertFalse(cli._is_retryable(RuntimeError(
            "429 RESOURCE_EXHAUSTED. Your prepayment credits are depleted. "
            "Please go to AI Studio to manage your project and billing."
        )))


class RetryLoopTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "img.png"
        self.sleeps = []

    def tearDown(self):
        self.tmp.cleanup()

    def _retry(self, gen, attempts=3):
        cli._generate_with_retries(
            "prompt", self.out, "1:1", "model-x",
            attempts=attempts, base_delay=1.0, sleep_fn=self.sleeps.append,
            generate_fn=gen,
        )

    def test_two_503s_then_success(self):
        gen = _fake_image_writer(2, GEMINI_503)
        self._retry(gen)
        self.assertEqual(len(gen.calls), 3)
        self.assertEqual(len(self.sleeps), 2)
        # exponential backoff: second delay strictly larger than first
        self.assertGreater(self.sleeps[1], self.sleeps[0])
        self.assertTrue(self.out.stat().st_size > cli.MIN_IMAGE_BYTES)

    def test_non_retryable_stops_immediately(self):
        gen = _fake_image_writer(5, RuntimeError("API key not valid"))
        with self.assertRaises(RuntimeError):
            self._retry(gen)
        self.assertEqual(len(gen.calls), 1)
        self.assertEqual(self.sleeps, [])

    def test_exhausted_attempts_raises(self):
        gen = _fake_image_writer(99, GEMINI_503)
        with self.assertRaises(RuntimeError):
            self._retry(gen)
        self.assertEqual(len(gen.calls), 3)

    # a "successful" call that writes a tiny/corrupt file must retry, not pass
    def test_small_file_retries(self):
        calls = []

        def gen(prompt, out_path, aspect_ratio, model=None):
            calls.append(model)
            out_path.write_bytes(b"tiny")

        with self.assertRaises(RuntimeError):
            self._retry(gen)
        self.assertEqual(len(calls), 3)


class ChainTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name) / "img.png"

    def tearDown(self):
        self.tmp.cleanup()

    def test_falls_back_to_stable_model(self):
        seen = []

        def gen(prompt, out_path, aspect_ratio, model=None):
            seen.append(model)
            if model == "primary-model":
                raise GEMINI_503
            out_path.write_bytes(b"\x89PNG" + b"0" * 20_000)

        ok, used, errors = cli._run_chain(
            "prompt", self.out, "1:1",
            models=("primary-model", "stable-model"),
            attempts=2, base_delay=0.0, sleep_fn=lambda _s: None,
            generate_fn=gen,
        )
        self.assertTrue(ok)
        self.assertEqual(used, "stable-model")
        self.assertEqual(seen, ["primary-model", "primary-model", "stable-model"])
        self.assertEqual(len(errors), 1)
        self.assertIn("primary-model", errors[0])

    def test_both_models_fail(self):
        def gen(prompt, out_path, aspect_ratio, model=None):
            raise GEMINI_503

        ok, used, errors = cli._run_chain(
            "prompt", self.out, "1:1",
            models=("primary-model", "stable-model"),
            attempts=2, base_delay=0.0, sleep_fn=lambda _s: None,
            generate_fn=gen,
        )
        self.assertFalse(ok)
        self.assertEqual(used, "")
        self.assertEqual(len(errors), 2)


class ExtractImageBytesTests(unittest.TestCase):

    # regression: Gemini returned response.parts=None on 2026-07-25 and the
    # old `for part in response.parts` crashed with TypeError
    def test_parts_none_raises_retryable(self):
        resp = SimpleNamespace(parts=None)
        with self.assertRaises(RuntimeError) as ctx:
            cli._extract_image_bytes(resp)
        self.assertTrue(cli._is_retryable(ctx.exception))

    def test_inline_bytes_returned(self):
        part = SimpleNamespace(inline_data=SimpleNamespace(data=b"rawbytes"))
        resp = SimpleNamespace(parts=[SimpleNamespace(inline_data=None), part])
        self.assertEqual(cli._extract_image_bytes(resp), b"rawbytes")


if __name__ == "__main__":
    unittest.main()
