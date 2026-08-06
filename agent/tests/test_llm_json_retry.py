# tests for call_llm_json JSON-repair retry:
#   the model occasionally emits unescaped inner double quotes (killed the
#   2026-07-23/28/31 runs at the rewrite step) — one re-ask with the parse
#   error must recover; two bad responses still raise loudly.
#
# run from agent/ dir:
#   python -m unittest tests.test_llm_json_retry

import sys
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import espresso_agent as ea

BAD_JSON = (
    '{\n'
    '  "headline": "Meta ships apps faster",\n'
    '  "blurb": "Zuckerberg said AI makes it "dramatically easier" to build."\n'
    '}'
)
GOOD_JSON = '{"headline": "Meta ships apps faster", "blurb": "fixed"}'


class FakeClient:
    def __init__(self, texts):
        self.calls = []
        outer = self

        class _Messages:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                text = texts[len(outer.calls) - 1]
                return SimpleNamespace(content=[SimpleNamespace(text=text)])

        self.messages = _Messages()


class CallLlmJsonRetryTests(unittest.TestCase):

    def _call(self, client):
        with mock.patch.object(ea, "USE_ANTHROPIC", True):
            return ea.call_llm_json(client, "system", "prompt", {"type": "object"})

    def test_valid_first_try_single_call(self):
        client = FakeClient([GOOD_JSON])
        out = self._call(client)
        self.assertEqual(out["blurb"], "fixed")
        self.assertEqual(len(client.calls), 1)

    def test_invalid_then_repaired(self):
        client = FakeClient([BAD_JSON, GOOD_JSON])
        out = self._call(client)
        self.assertEqual(out["blurb"], "fixed")
        self.assertEqual(len(client.calls), 2)
        # the re-ask must feed back the parse error and the bad output
        retry_prompt = client.calls[1]["messages"][0]["content"]
        self.assertIn("not valid JSON", retry_prompt)
        self.assertIn("dramatically easier", retry_prompt)

    def test_two_failures_raise(self):
        client = FakeClient([BAD_JSON, BAD_JSON])
        with self.assertRaises(RuntimeError):
            self._call(client)
        self.assertEqual(len(client.calls), 2)

    def test_fenced_json_still_stripped(self):
        client = FakeClient(["```json\n" + GOOD_JSON + "\n```"])
        out = self._call(client)
        self.assertEqual(out["headline"], "Meta ships apps faster")


if __name__ == "__main__":
    unittest.main()
