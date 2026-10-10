"""Speech to text with ElevenLabs Scribe (key in ELEVENLABS_API_KEY).

Scribe detects the language and gives word timings with log-probabilities.
It doesn't translate, so non-English notes get an English translation from
Gemma, kept beside the original; the journal quotes the original.
"""

from __future__ import annotations

import difflib
import math
import mimetypes
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from . import config, llm
from .schema import Word

MIN_SPEECH_S = 0.8
LOW_CONFIDENCE = 0.6
TIMEOUT_S = 120
# Scribe answers ISO 639-3; the rest of the app uses the short codes.
SHORT_LANG = {"eng": "en", "hin": "hi", "mar": "mr", "guj": "gu", "kan": "kn", "tam": "ta", "tel": "te",
              "ben": "bn", "urd": "ur", "pan": "pa", "mal": "ml"}

# Words Scribe should expect on a Sahyadri trail, sent as keyterms alongside
# the fort pack's place names. Spelling help only, never facts.
TRAIL_TERMS = ["langur", "langurs", "taak", "kanda bhaji", "chaas", "bhakri", "pithla", "zunka", "misal",
               "Sahyadri", "buruj", "darwaja", "kada", "machi", "gad"]

TRANSLATE_SYSTEM = """Translate a walker's short trail voice note into plain English.
Keep place names, food names and local words as they are (for example taak, kanda bhaji, darwaja).
Return only the translation."""


class SttError(RuntimeError):
    pass


@dataclass
class Transcript:
    text: str
    lang: str | None
    words: list[Word] = field(default_factory=list)
    confidence: float | None = None
    translation: str | None = None
    skipped: str | None = None


def _glossary_fix(text: str, glossary: list[str]) -> str:
    """Correct place names speech to text mishears, e.g. 'Sinhgarh' -> 'Sinhagad'."""
    terms = [g for g in glossary if len(g) >= 5]
    if not terms:
        return text
    tokens = re.findall(r"\w+|\W+", text)
    words_idx = [i for i, t in enumerate(tokens) if re.match(r"\w", t)]
    for n in (3, 2, 1):
        for k in range(len(words_idx) - n + 1):
            span = words_idx[k : k + n]
            if any(tokens[i] is None for i in span):
                continue
            phrase = " ".join(tokens[i] for i in span)
            for term in terms:
                if len(term.split()) != n or phrase.lower() == term.lower():
                    continue
                if difflib.SequenceMatcher(None, phrase.lower(), term.lower()).ratio() >= 0.82:
                    tokens[span[0]] = term
                    for i in range(span[0] + 1, span[-1] + 1):
                        tokens[i] = None
                    break
    return "".join(t for t in tokens if t is not None)


def _scribe(path: Path, keyterms: list[str]) -> dict:
    cfg = config.load()
    if not cfg.elevenlabs_api_key:
        raise SttError("ELEVENLABS_API_KEY isn't set. Add it to .env (local) or the Render dashboard, then restart.")
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    terms = list(dict.fromkeys(t for t in keyterms if 0 < len(t) <= 50 and len(t.split()) <= 5))[:100]
    for attempt in range(5):
        try:
            with path.open("rb") as f:
                r = httpx.post(
                    f"{cfg.elevenlabs_base_url}/speech-to-text",
                    headers={"xi-api-key": cfg.elevenlabs_api_key},
                    data={"model_id": cfg.stt_model, "timestamps_granularity": "word", "tag_audio_events": "false",
                          **({"keyterms": terms} if terms else {})},
                    files={"file": (path.name, f, mime)},
                    timeout=TIMEOUT_S,
                )
        except httpx.HTTPError as e:
            raise SttError(f"Couldn't reach ElevenLabs ({e.__class__.__name__}). Check the connection and try again.") from e
        if r.status_code in (400, 422) and terms and "keyterm" in r.text.lower():
            terms = []  # this model doesn't take keyterms; transcribe without them
            continue
        if r.status_code in (429, 500, 502, 503) and attempt < 3:
            time.sleep(min(30, 4 * 2 ** attempt))
            continue
        break
    if r.status_code in (401, 403):
        raise SttError("ElevenLabs refused the key, or the account is out of credits. Check ELEVENLABS_API_KEY.")
    if r.status_code != 200:
        raise SttError(f"ElevenLabs answered {r.status_code}: {r.text[:300]}")
    return r.json()


def transcribe(path: Path, glossary: list[str]) -> Transcript:
    body = _scribe(path, glossary + TRAIL_TERMS)
    raw_lang = body.get("language_code")
    lang = SHORT_LANG.get(raw_lang or "", raw_lang)
    words: list[Word] = []
    for w in body.get("words") or []:
        if w.get("type", "word") != "word" or not str(w.get("text", "")).strip():
            continue
        lp = w.get("logprob")
        p = round(math.exp(lp), 3) if isinstance(lp, (int, float)) else None
        words.append(Word(w=w["text"].strip(), start=round(w.get("start") or 0, 2), end=round(w.get("end") or 0, 2), p=p))

    text = re.sub(r"\s+", " ", body.get("text") or "").strip()
    speech_s = sum(max(0.0, w.end - w.start) for w in words)
    if not text or (words and speech_s < MIN_SPEECH_S):
        return Transcript(text="", lang=lang, skipped="no speech")

    text = _glossary_fix(text, glossary)
    text = text[:1].upper() + text[1:]  # Scribe sometimes starts a note in lower case
    probs = [w.p for w in words if w.p is not None]
    confidence = round(math.fsum(probs) / len(probs), 3) if probs else None
    translation = None
    if lang and lang != "en":
        translation = llm.chat(config.load().llm_model, TRANSLATE_SYSTEM, text, task="translate", temperature=0.1) or None
    return Transcript(text=text, lang=lang, words=words, confidence=confidence, translation=translation)
