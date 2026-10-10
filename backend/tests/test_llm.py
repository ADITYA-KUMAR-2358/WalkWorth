"""The Gemma client against a scripted fake endpoint (no network)."""

from __future__ import annotations

import copy
import os
import time
import unittest

from tests import TMP  # noqa: F401

import httpx

from walkworth import config, llm


def ok(text: str):
    return 200, {"choices": [{"message": {"content": text}}], "usage": {"completion_tokens": 5}}, {}


class FakeEndpoint:
    def __init__(self):
        self.sent: list[dict] = []
        self.script: list[tuple] = []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.sent.append({"url": url, "body": copy.deepcopy(json), "auth": (headers or {}).get("Authorization")})
        status, payload, hdrs = self.script.pop(0)
        req = httpx.Request("POST", url)
        if isinstance(payload, dict):
            return httpx.Response(status, json=payload, headers=hdrs, request=req)
        return httpx.Response(status, text=payload, headers=hdrs, request=req)


class LlmTest(unittest.TestCase):
    def setUp(self):
        self.env = {k: os.environ.get(k) for k in ("LLM_API_KEY", "LLM_MAX_RPM", "LLM_MAX_WAIT", "LLM_MAX_PER_DAY", "LLM_THINKING_LEVEL")}
        os.environ.update({"LLM_API_KEY": "test-key", "LLM_MAX_RPM": "0", "LLM_MAX_WAIT": "3", "LLM_MAX_PER_DAY": "0",
                           "LLM_THINKING_LEVEL": "minimal"})
        config.load.cache_clear()
        llm._refused.clear()
        (config.load().data_dir / "llm_calls.json").unlink(missing_ok=True)
        self.fake = FakeEndpoint()
        self.real_post, httpx.post = httpx.post, self.fake

    def tearDown(self):
        httpx.post = self.real_post
        for k, v in self.env.items():
            os.environ[k] = v if v is not None else ""
        config.load.cache_clear()

    def test_openai_shape_url_auth_and_json(self):
        self.fake.script = [ok('```json\n{"a": 1}\n```')]
        out = llm.chat("m", "sys", "hi", schema={"type": "object"})
        self.assertEqual(out, '{"a": 1}')
        sent = self.fake.sent[0]
        self.assertTrue(sent["url"].endswith("/chat/completions"))
        self.assertEqual(sent["auth"], "Bearer test-key")
        self.assertEqual(sent["body"]["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual(sent["body"]["response_format"], {"type": "json_object"})
        self.assertEqual(sent["body"]["reasoning_effort"], "minimal")

    def test_refused_option_is_dropped_and_remembered(self):
        self.fake.script = [(400, 'Unknown name "reasoning_effort"', {}), ok("one"), ok("two")]
        self.assertEqual(llm.chat("m", "s", "u"), "one")
        self.assertNotIn("reasoning_effort", self.fake.sent[1]["body"])
        self.assertEqual(llm.chat("m", "s", "u"), "two")
        self.assertNotIn("reasoning_effort", self.fake.sent[2]["body"])

    def test_429_is_waited_out_then_gives_up(self):
        self.fake.script = [(429, "slow", {"retry-after": "1"}), ok("<think>x</think>done")]
        t = time.monotonic()
        self.assertEqual(llm.chat("m", "s", "u"), "done")
        self.assertGreaterEqual(time.monotonic() - t, 1)
        self.fake.script = [(429, "x", {"retry-after": "2"}), (429, "x", {"retry-after": "2"})]
        with self.assertRaisesRegex(llm.ModelError, "LLM_MAX_WAIT"):
            llm.chat("m", "s", "u")

    def test_image_is_a_data_uri(self):
        self.fake.script = [ok("a gate")]
        llm.chat("m", "s", "caption", images=[b"\xff\xd8"])
        parts = self.fake.sent[0]["body"]["messages"][1]["content"]
        self.assertTrue(parts[0]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(parts[1]["type"], "text")

    def test_daily_cap_counts_answers_only(self):
        os.environ["LLM_MAX_PER_DAY"] = "2"
        config.load.cache_clear()
        self.fake.script = [(400, 'bad "reasoning_effort"', {}), ok("a"), ok("b")]
        llm.chat("m", "s", "u")
        llm.chat("m", "s", "u")
        self.assertEqual(llm.calls_today()["used"], 2)
        with self.assertRaises(llm.DailyLimit):
            llm.chat("m", "s", "u")

    def test_throttle_spaces_calls(self):
        os.environ["LLM_MAX_RPM"] = "120"
        config.load.cache_clear()
        llm._next_slot = 0.0
        self.fake.script = [ok("a"), ok("b"), ok("c")]
        t = time.monotonic()
        for _ in range(3):
            llm.chat("m", "s", "u")
        self.assertGreaterEqual(time.monotonic() - t, 0.95)

    def test_missing_key_and_bad_status_messages(self):
        os.environ["LLM_API_KEY"] = ""
        config.load.cache_clear()
        with self.assertRaisesRegex(llm.ModelError, "LLM_API_KEY isn't set"):
            llm.chat("m", "s", "u")
        os.environ["LLM_API_KEY"] = "k"
        config.load.cache_clear()
        self.fake.script = [(404, "no", {})]
        with self.assertRaisesRegex(llm.ModelError, "LLM_MODEL"):
            llm.chat("m", "s", "u")
        self.fake.script = [(401, "no", {})]
        with self.assertRaisesRegex(llm.ModelError, "refused the key"):
            llm.chat("m", "s", "u")


if __name__ == "__main__":
    unittest.main()
