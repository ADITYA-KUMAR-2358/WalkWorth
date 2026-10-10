"""ElevenLabs Scribe client against a scripted fake endpoint (no network)."""

from __future__ import annotations

import os
import unittest

from tests import TMP  # noqa: F401

import httpx

from walkworth import config, stt

AUDIO = config.REPO_ROOT / "fixtures" / "sinhagad-sample" / "audio" / "note-01.ogg"
WORDS = [{"text": "Started", "start": 0.1, "end": 0.6, "type": "word", "logprob": -0.05},
         {"text": " ", "start": 0.6, "end": 0.7, "type": "spacing"},
         {"text": "Atkarwadi", "start": 0.7, "end": 1.5, "type": "word", "logprob": -0.4}]


class SttTest(unittest.TestCase):
    def setUp(self):
        self.old = os.environ.get("ELEVENLABS_API_KEY")
        os.environ["ELEVENLABS_API_KEY"] = "test"
        config.load.cache_clear()
        self.sent: list[dict] = []
        self.script: list[httpx.Response] = []
        self.real, httpx.post = httpx.post, self.fake

    def tearDown(self):
        httpx.post = self.real
        os.environ["ELEVENLABS_API_KEY"] = self.old or ""
        config.load.cache_clear()

    def fake(self, url, headers=None, data=None, files=None, timeout=None):
        self.sent.append({"url": url, "data": dict(data or {}), "key": (headers or {}).get("xi-api-key")})
        return self.script.pop(0)

    def reply(self, status=200, body=None, text=None):
        req = httpx.Request("POST", "https://x")
        return httpx.Response(status, json=body, request=req) if body is not None else httpx.Response(status, text=text, request=req)

    def test_keyterms_language_and_confidence(self):
        self.script = [self.reply(body={"text": "Started Atkarwadi", "language_code": "eng", "words": WORDS})]
        r = stt.transcribe(AUDIO, ["Atkarwadi", "Sinhagad"])
        sent = self.sent[0]
        self.assertTrue(sent["url"].endswith("/speech-to-text"))
        self.assertEqual(sent["key"], "test")
        self.assertIn("Atkarwadi", sent["data"]["keyterms"])
        self.assertIn("kanda bhaji", sent["data"]["keyterms"])
        self.assertEqual(r.lang, "en")
        self.assertEqual([w.w for w in r.words], ["Started", "Atkarwadi"])
        self.assertAlmostEqual(r.confidence, (0.951 + 0.670) / 2, places=2)

    def test_keyterms_dropped_if_refused(self):
        self.script = [self.reply(400, text='{"detail": "keyterms not supported for this model"}'),
                       self.reply(body={"text": "Started Atkarwadi", "language_code": "eng", "words": WORDS})]
        r = stt.transcribe(AUDIO, ["Atkarwadi"])
        self.assertEqual(r.text, "Started Atkarwadi")
        self.assertNotIn("keyterms", self.sent[1]["data"])

    def test_silence_is_skipped(self):
        self.script = [self.reply(body={"text": "", "language_code": "eng", "words": []})]
        self.assertEqual(stt.transcribe(AUDIO, []).skipped, "no speech")

    def test_bad_key_message(self):
        self.script = [self.reply(401, text="unauthorized")]
        with self.assertRaisesRegex(stt.SttError, "ELEVENLABS_API_KEY"):
            stt.transcribe(AUDIO, [])


if __name__ == "__main__":
    unittest.main()
