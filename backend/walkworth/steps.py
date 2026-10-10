"""The pipeline's steps as plain functions keyed by id.

Temporal activities (activities.py) call these. Every step reads what it needs
from SQLite and writes its result back keyed by id, so running a step twice
gives the same rows, and a step that already finished is skipped.
Rows the user edited are never overwritten.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import timedelta
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ValidationError

from . import config, db, fortpack, gpx, llm, stt
from .schema import Entry, GeneratedLine, Observation, ObservationKind, Photo, TrackPoint, Trek, VoiceNote

CAPTION_WINDOW = timedelta(minutes=10)
PHOTO_ATTACH_WINDOW = timedelta(minutes=15)
LONG_NOTE_WORDS = 40


def _trek(conn, trek_id: str) -> Trek:
    trek = db.get(conn, Trek, trek_id)
    if trek is None:
        raise ValueError(f"No trek with id {trek_id}")
    return trek


def _glossary(trek: Trek) -> list[str]:
    pack = fortpack.load(trek.fort_pack_id) if trek.fort_pack_id else None
    if not pack:
        return []
    return sorted({*pack.glossary, *(lm.name for lm in pack.landmarks)})


def _abs(path: str) -> Path:
    return config.media_path(path)


# ---- Import ---------------------------------------------------------------


def prepare(trek_id: str) -> dict:
    """Check what was imported and list the work for the later steps."""
    with db.connect() as conn:
        _trek(conn, trek_id)
        notes = db.fetch(conn, VoiceNote, "trek_id = ?", (trek_id,), "t")
        photos = db.fetch(conn, Photo, "trek_id = ?", (trek_id,), "t")
        track = conn.execute("SELECT COUNT(*) FROM track_point WHERE trek_id = ?", (trek_id,)).fetchone()[0]
    missing = [n.id for n in notes if n.audio_path and not _abs(n.audio_path).is_file()]
    if missing:
        raise FileNotFoundError(f"Audio is missing for {len(missing)} notes, e.g. {missing[0]}. Import the .walk bundle again.")
    return {
        "notes": [n.id for n in notes],
        "to_transcribe": [n.id for n in notes if n.audio_path and not n.transcript and not n.skipped and not n.user_edited],
        "photos": [p.id for p in photos],
        "track_points": track,
    }


# ---- Transcribe -----------------------------------------------------------


def transcribe_note(note_id: str) -> str:
    with db.connect() as conn:
        note = db.get(conn, VoiceNote, note_id)
        if note is None:
            return "gone"
        if note.user_edited or note.transcript or note.skipped or not note.audio_path:
            return "already done"
        trek = _trek(conn, note.trek_id)
    result = stt.transcribe(_abs(note.audio_path), _glossary(trek))
    with db.connect() as conn:
        # Re-read: the user may have edited it while Scribe ran.
        fresh = db.get(conn, VoiceNote, note_id)
        if fresh is None or fresh.user_edited:
            return "kept user edit"
        db.upsert(conn, fresh.model_copy(update={
            "transcript": result.text or None, "words": result.words, "lang": result.lang,
            "translation": result.translation, "confidence": result.confidence, "skipped": result.skipped,
        }))
    return result.skipped or "transcribed"


# ---- Align ----------------------------------------------------------------


def align(trek_id: str) -> dict:
    with db.connect() as conn:
        trek = _trek(conn, trek_id)
        pack = fortpack.load(trek.fort_pack_id) if trek.fort_pack_id else None
        track = db.fetch(conn, TrackPoint, "trek_id = ?", (trek_id,), "t")
        offset = timedelta(seconds=trek.time_offset_s)
        snapped = 0
        for model in (VoiceNote, Photo):
            for item in db.fetch(conn, model, "trek_id = ?", (trek_id,)):
                p = gpx.nearest_in_time(track, item.t + offset)
                update: dict = {}
                if p:
                    update = {"lat": p.lat, "lon": p.lon, "ele_m": p.ele_m, "location_source": "track"}
                elif item.lat is None:
                    update = {"location_source": "none"}
                lat, lon = update.get("lat", item.lat), update.get("lon", item.lon)
                lm = fortpack.nearest_landmark(pack, lat, lon)
                update["landmark_id"] = lm.id if lm else None
                snapped += lm is not None
                db.upsert(conn, item.model_copy(update=update))
    return {"track_points": len(track), "snapped": snapped}


# ---- Extract --------------------------------------------------------------


class ExtractedObservation(BaseModel):
    kind: ObservationKind
    text: str


class Extracted(BaseModel):
    observations: list[ExtractedObservation]
    landmark_mentions: list[str]
    mood: str | None
    quote: str


EXTRACT_SYSTEM = """You read one voice note a walker recorded on a trail and pull out what they noticed.
Return JSON only. Use only what the walker actually said; never add anything.
- observations: each thing they noticed. kind is one of wildlife, plant, structure, view, weather, people, feeling. text is a few words in their own terms.
- landmark_mentions: place names they said, exactly as written in the transcript.
- mood: one lowercase word for how they sound, or null.
- quote: the most vivid phrase, copied character for character from the transcript. Do not change a single word."""


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    s = s.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", s).strip(" .,!?\"'")


def _verbatim(quote: str, transcript: str) -> str | None:
    """Return the quote as it appears in the transcript, or None if it doesn't."""
    q = _norm(quote)
    if not q:
        return None
    t = transcript
    idx = _norm(t).find(q)
    if idx < 0:
        return None
    # Map back to the original text by searching case-insensitively.
    m = re.search(re.escape(quote.strip(" .,!?\"'")), t, re.I)
    return m.group(0) if m else quote.strip()


def extract_note(note_id: str) -> str:
    with db.connect() as conn:
        note = db.get(conn, VoiceNote, note_id)
        if note is None or not note.transcript:
            return "nothing to read"
        if note.quote and conn.execute("SELECT 1 FROM observation WHERE note_id = ?", (note_id,)).fetchone():
            return "already done"
        trek = _trek(conn, note.trek_id)
    model = config.load().llm_model
    user = f"Transcript ({note.lang or 'unknown language'}):\n{note.transcript}"
    if note.translation:
        user += f"\n\nEnglish translation (for understanding only; quote from the transcript):\n{note.translation}"

    result: Extracted | None = None
    error = None
    for _attempt in range(2):
        prompt = user if error is None else f"{user}\n\nYour last answer was rejected: {error}. Fix it."
        raw = llm.chat(model, EXTRACT_SYSTEM, prompt, schema=Extracted.model_json_schema(), task="extract", temperature=0.1)
        try:
            candidate = Extracted.model_validate_json(raw)
        except ValidationError as e:
            error = f"invalid JSON for the schema ({e.errors()[0]['msg']})"
            continue
        verbatim = _verbatim(candidate.quote, note.transcript)
        if verbatim is None:
            error = "the quote is not copied exactly from the transcript"
            continue
        result = candidate.model_copy(update={"quote": verbatim})
        break

    pack = fortpack.load(trek.fort_pack_id) if trek.fort_pack_id else None
    with db.connect() as conn:
        fresh = db.get(conn, VoiceNote, note_id)
        if fresh is None:
            return "gone"
        conn.execute("DELETE FROM observation WHERE note_id = ?", (note_id,))
        if result is None:
            # Fall back to the raw transcript: the journal still has the walker's words.
            db.upsert(conn, fresh.model_copy(update={"quote": fresh.transcript, "mood": None}))
            return f"fell back to transcript ({error})"
        landmark_id = fresh.landmark_id
        if not landmark_id and pack:
            for mention in result.landmark_mentions:
                hit = next((lm for lm in pack.landmarks if _norm(lm.name) == _norm(mention)), None)
                if hit:
                    landmark_id = hit.id
                    break
        for i, ob in enumerate(result.observations):
            db.upsert(conn, Observation(id=f"{note_id}-o{i}", note_id=note_id, kind=ob.kind, text=ob.text.strip(),
                                        landmark_id=landmark_id))
        db.upsert(conn, fresh.model_copy(update={
            "quote": result.quote, "mood": (result.mood or "").strip().lower() or None, "landmark_id": landmark_id,
        }))
    return f"{len(result.observations)} observations"


# ---- Caption --------------------------------------------------------------

CAPTION_SYSTEM = """You write the caption under a photo in a walker's trek journal.
Describe only what is visible in the photo. One plain sentence, at most 12 words.
No hashtags, no quotation marks, no guesses about names or history."""


def caption_photo(photo_id: str) -> str:
    with db.connect() as conn:
        photo = db.get(conn, Photo, photo_id)
        if photo is None:
            return "gone"
        if photo.user_edited or photo.caption:
            return "already done"
        trek = _trek(conn, photo.trek_id)
        notes = [n for n in db.fetch(conn, VoiceNote, "trek_id = ? AND transcript IS NOT NULL", (photo.trek_id,))]
    near = min(notes, key=lambda n: abs(n.t - photo.t), default=None)
    if near and abs(near.t - photo.t) > CAPTION_WINDOW:
        near = None
    user = "Write the caption for this photo."
    if near:
        user += (f'\nAround the time of the photo the walker said: "{near.transcript}"\n'
                 "If the photo shows what they mention, use their words for it. Otherwise describe only what you see.")
    raw = llm.chat(config.load().vision_model, CAPTION_SYSTEM, user,
                   images=[llm.image_for_model(_abs(photo.path))], task="caption", temperature=0.2)
    caption = re.sub(r"\s+", " ", raw).strip().strip('"“”').strip()
    caption = caption.split("\n")[0]
    words = caption.split()
    if len(words) > 16:
        caption = " ".join(words[:16]).rstrip(",;:") + "."
    with db.connect() as conn:
        fresh = db.get(conn, Photo, photo_id)
        if fresh is None or fresh.user_edited:
            return "kept user edit"
        db.upsert(conn, fresh.model_copy(update={
            "caption": caption, "caption_source_ids": [photo_id] + ([near.id] if near else []),
        }))
    return caption


# ---- Compose --------------------------------------------------------------

ChapterKey = Literal["start", "climb", "top", "descent", "first", "middle", "last"]
DEFAULT_TITLES = {
    "start": "Setting out",
    "climb": "The climb",
    "top": "At the top",
    "descent": "The way down",
    "first": "Setting out",
    "middle": "Along the way",
    "last": "The way back",
}
CHAPTER_ORDER = ["start", "climb", "top", "descent", "first", "middle", "last"]


def _chapter_fn(track: list[TrackPoint], items_t: list) -> Callable:
    eles = [p.ele_m for p in track if p.ele_m is not None]
    if len(track) >= 2 and eles and max(eles) - min(eles) >= 80:
        lo, hi = min(eles), max(eles)
        threshold = hi - 0.15 * (hi - lo)
        top_times = [p.t for p in track if p.ele_m is not None and p.ele_m >= threshold]
        top_start, top_end = top_times[0], top_times[-1]
        start_pt = track[0]

        def chapter(t, lat, lon):
            if t < top_start:
                if lat is not None and gpx.haversine_m(lat, lon, start_pt.lat, start_pt.lon) < 400:
                    return "start"
                return "climb"
            return "top" if t <= top_end else "descent"

        return chapter

    # No useful elevation: thirds by time.
    if items_t:
        t0, t1 = min(items_t), max(items_t)
        span = (t1 - t0) or timedelta(seconds=1)
    else:
        t0, span = None, timedelta(seconds=1)

    def by_time(t, lat, lon):
        f = (t - t0) / span if t0 else 0
        return "first" if f < 1 / 3 else "middle" if f < 2 / 3 else "last"

    return by_time


def build_entries(trek_id: str) -> list[Entry]:
    """Deterministic structure: chapters by trail segment, one entry per voice
    note, photos attached to the nearest note (or pinned by the user)."""
    with db.connect() as conn:
        trek = _trek(conn, trek_id)
        track = db.fetch(conn, TrackPoint, "trek_id = ?", (trek_id,), "t")
        notes = [n for n in db.fetch(conn, VoiceNote, "trek_id = ?", (trek_id,), "t") if n.transcript]
        photos = db.fetch(conn, Photo, "trek_id = ?", (trek_id,), "t")
    offset = timedelta(seconds=trek.time_offset_s)
    chapter = _chapter_fn(track, [n.t + offset for n in notes] + [p.t + offset for p in photos])

    entries: dict[str, Entry] = {}
    for n in notes:
        # The walker's words, verbatim. Long notes are led by the extracted
        # phrase (also verbatim); the journal shows the full note under it.
        long_note = len((n.transcript or "").split()) > LONG_NOTE_WORDS
        quote = (n.quote or n.transcript) if long_note and not n.user_edited else n.transcript
        entries[n.id] = Entry(
            id=f"{trek_id}-e-{n.id}", trek_id=trek_id, chapter=chapter(n.t + offset, n.lat, n.lon), t=n.t + offset,
            ele_m=n.ele_m, note_id=n.id, landmark_id=n.landmark_id, quote=quote,
        )
    for p in photos:
        host = entries.get(p.pinned_note_id or "")
        if host is None:
            near = min(notes, key=lambda n: abs(n.t - p.t), default=None)
            if near and abs(near.t - p.t) <= PHOTO_ATTACH_WINDOW and chapter(near.t + offset, near.lat, near.lon) == chapter(p.t + offset, p.lat, p.lon):
                host = entries[near.id]
        if host is not None:
            host.photo_ids.append(p.id)
            if not host.landmark_id and p.landmark_id:
                host.landmark_id = p.landmark_id
        else:
            entries[p.id] = Entry(
                id=f"{trek_id}-e-{p.id}", trek_id=trek_id, chapter=chapter(p.t + offset, p.lat, p.lon), t=p.t + offset,
                ele_m=p.ele_m, photo_ids=[p.id], landmark_id=p.landmark_id,
            )
    ordered = sorted(entries.values(), key=lambda e: e.t)
    # Chapter order follows time, not the dict above.
    return ordered


class LineOut(BaseModel):
    text: str
    source_ids: list[str]


class ChapterOut(BaseModel):
    title: LineOut
    line: LineOut


class OpenerOut(BaseModel):
    opener: list[LineOut]


COMPOSE_RULES = """Rules, all mandatory:
- Use only what the FACTS say, mostly in their own words. Never add animals, people, feelings, weather, history, numbers or places that are not in the facts. Don't describe the walker (alone, tired, happy) unless a fact says so.
- Every line lists the ids of the facts it uses in source_ids. A line that uses no fact must not exist.
- Write numbers as digits, exactly as the facts give them.
- Write plainly and quietly. Don't say "the walker". No exclamation marks. Don't copy a fact word for word."""

CHAPTER_SYSTEM = """You write the small connecting text in a walker's trek journal. The walker's own words are the journal.
For ONE chapter of the walk, write:
- title: 2 to 6 words, sentence case, naming what this part of the walk was about.
- line: one sentence under 18 words that leads into the walker's notes.
""" + COMPOSE_RULES

OPENER_SYSTEM = """You write the opening of a walker's trek journal. The walker's own words are the journal.
Write opener: exactly two short lines, each under 14 words.
""" + COMPOSE_RULES

CHAPTER_HINTS = {
    "start": "the start of the walk",
    "climb": "the way up",
    "top": "at the top",
    "descent": "the way down",
    "first": "the first part of the walk",
    "middle": "the middle of the walk",
    "last": "the last part of the walk",
}


def _facts(trek: Trek, entries: list[Entry], notes: dict, photos: dict, stats, pack,
           obs: dict[str, list[Observation]] | None = None) -> dict[str, str]:
    """What the model may write from. Notes are given as what the extract step
    found in them (not the raw quote), so the model connects the walker's
    words instead of pasting them; the quotes appear verbatim in the journal."""
    facts: dict[str, str] = {}
    obs = obs or {}
    if stats:
        facts["stats.distance"] = f"walked {stats.distance_m / 1000:.1f} km"
        facts["stats.gain"] = f"climbed {stats.gain_m:.0f} m"
        if stats.high_point_m:
            facts["stats.high_point"] = f"highest point {stats.high_point_m:.0f} m"
        h, m = divmod(stats.total_s // 60, 60)
        facts["stats.time"] = f"{h} h {m} min on the trail"
    for e in entries:
        if e.note_id and e.note_id in notes:
            n = notes[e.note_id]
            found = obs.get(n.id, [])
            if found:
                desc = "noticed " + "; ".join(o.text for o in found)
                if n.mood:
                    desc += f"; sounded {n.mood}"
            else:
                desc = "said: " + (n.translation or n.transcript or "")
            facts[n.id] = f"[chapter {e.chapter}] {desc}"
        for pid in e.photo_ids:
            if pid in photos and photos[pid].caption:
                facts[pid] = f"[chapter {e.chapter}] photo: {photos[pid].caption}"
    if pack:
        for lm in pack.landmarks:
            if any(e.landmark_id == lm.id for e in entries):
                facts[f"landmark.{lm.id}"] = f"passed {lm.name}"
    # Clock times are left out: the 4B model copies them into titles.
    for ch in dict.fromkeys(e.chapter for e in entries):
        eles = [e.ele_m for e in entries if e.chapter == ch and e.ele_m is not None]
        if eles and max(eles) - min(eles) >= 50:
            facts[f"chapter.{ch}"] = f"[chapter {ch}] went from {eles[0]:.0f} m to {eles[-1]:.0f} m"
    facts["trek.place"] = f"the walk was at {trek.title}"
    return facts


# Words that carry no facts. Everything else in a generated line has to come
# from the facts it cites.
GLUE = set("""a an the and but or so of to from at on in into onto up down out over under through along across
toward towards near by with without as for than then there here it its this that these those is are was were be
been being has have had do did done not no just still all some more most very quite only even back again away
walk walked walking walker trail path way day morning journal begins began begin starts started start set setting
first last next part chapter you your we our us they their one two three""".split())

def _content(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z][a-z'-]+", text.lower()) if len(w) >= 3 and w not in GLUE]


def _sentence_case(text: str, vocab: set[str], cited: str = "") -> str:
    """Title Case -> sentence case, keeping places (vocab) and names that
    appear capitalised in the cited facts."""
    names = set(re.findall(r"[\w'-]+", cited))
    out = []
    for i, w in enumerate(text.split(" ")):
        bare = re.sub(r"[^\w'-]", "", w)
        if i > 0 and bare[:1].isupper() and bare.lower() not in vocab and bare not in names:
            w = w.replace(bare, bare.lower())
        out.append(w)
    return " ".join(out)


def _copies(line: str, quote: str, run: int = 6) -> bool:
    """True if the line repeats `run` or more consecutive words of the quote."""
    a = re.findall(r"[a-z0-9']+", line.lower())
    b = " " + " ".join(re.findall(r"[a-z0-9']+", quote.lower())) + " "
    return any(f" {' '.join(a[i:i + run])} " in b for i in range(len(a) - run + 1))


def _restates(line: str, quote: str) -> bool:
    """True if every content word of the line is already in the quote: the
    line only says again, in other words, what the walker said."""
    words = _content(line)
    said = {w[:4] for w in _content(quote)}
    return len(words) >= 2 and all(w[:4] in said for w in words)


def _check(text: str, source_ids: list[str], facts: dict[str, str], vocab: set[str],
           min_overlap: float = 0.5) -> str | None:
    """The grounding rule for one generated line. Returns why it fails, or
    None if it passes:
    - it cites at least one fact, and every cited id exists,
    - every number in it appears in what it cites,
    - every capitalised word (a name) appears in what it cites or in the place vocabulary,
    - most of its content words come from what it cites (prefix match, so dark ~ darkness)."""
    if not source_ids:
        return "cites no facts"
    missing = [s for s in source_ids if s not in facts]
    if missing:
        return f"cites ids that don't exist: {', '.join(missing)}"
    # The [chapter x] tags are for the model, not evidence for words like "descent".
    cited = re.sub(r"\[chapter \w+\]", "", " ".join(facts[s] for s in source_ids)).lower()
    for num in re.findall(r"\d+(?:\.\d+)?", text):
        if num not in cited:
            return f"has the number {num}, which its facts don't"
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        words = re.findall(r"[A-Za-z][\w'-]*", sentence)
        for i, w in enumerate(words):
            if i == 0 or not w[0].isupper() or w in {"I", "I'm", "I'd"}:
                continue
            if w.lower() not in cited and w.lower() not in vocab:
                return f"names '{w}', which its facts don't"
    content = _content(text)
    if not content:
        return "says nothing"
    cited_stems = {w[:4] for w in _content(cited)} | {w[:4] for w in vocab}
    unsupported = [w for w in content if w[:4] not in cited_stems]
    if (len(content) - len(unsupported)) / len(content) < min_overlap:
        return f"uses words its facts don't support: {', '.join(unsupported[:5])}"
    return None


def _grounded(text: str, source_ids: list[str], facts: dict[str, str], vocab: set[str], min_overlap: float = 0.5) -> bool:
    return _check(text, source_ids, facts, vocab, min_overlap) is None


MAX_WORDS = {"opener": 16, "chapter_title": 7, "chapter_line": 20}


def compose(trek_id: str) -> dict:
    entries = build_entries(trek_id)
    with db.connect() as conn:
        trek = _trek(conn, trek_id)
        conn.execute("DELETE FROM entry WHERE trek_id = ?", (trek_id,))
        db.upsert_many(conn, entries)
        notes = {n.id: n for n in db.fetch(conn, VoiceNote, "trek_id = ?", (trek_id,))}
        photos = {p.id: p for p in db.fetch(conn, Photo, "trek_id = ?", (trek_id,))}
        track = db.fetch(conn, TrackPoint, "trek_id = ?", (trek_id,), "t")
        kept = {g.id: g for g in db.fetch(conn, GeneratedLine, "trek_id = ? AND user_edited = 1", (trek_id,))}
        obs: dict[str, list[Observation]] = {}
        for o in db.fetch(conn, Observation, "note_id IN (SELECT id FROM voice_note WHERE trek_id = ?)", (trek_id,), "id"):
            obs.setdefault(o.note_id, []).append(o)

    pack = fortpack.load(trek.fort_pack_id) if trek.fort_pack_id else None
    facts = _facts(trek, entries, notes, photos, gpx.stats(track), pack, obs)
    chapters = [c for c in CHAPTER_ORDER if any(e.chapter == c for e in entries)]
    vocab = {w.lower() for w in re.findall(r"[A-Za-z][\w'-]*", " ".join(_glossary(trek) + [trek.title]))}

    chapter_of: dict[str, str] = {}
    for e in entries:
        if e.note_id:
            chapter_of[e.note_id] = e.chapter
        for pid in e.photo_ids:
            chapter_of[pid] = e.chapter
        chapter_of[f"chapter.{e.chapter}"] = e.chapter
    # The opener may use only walk-level facts. Notes and photos belong to
    # their chapters; an opener built from them just repeats the journal.
    shared = [k for k in facts if k not in chapter_of and (k.startswith("stats.") or k.startswith("landmark.") or k == "trek.place")]

    def judge(role: str, chapter: str | None, item: LineOut) -> tuple[str, str | None]:
        """Normalise a line and run every check on it."""
        text = re.sub(r"\s+", " ", item.text).strip().rstrip(".")
        if role == "chapter_title":
            text = _sentence_case(text, vocab, " ".join(facts.get(i, "") for i in item.source_ids))
            text = text[:1].upper() + text[1:]
        elif text:
            text += "."
        if not text:
            return text, "is empty"
        if len(text.split()) > MAX_WORDS[role]:
            return text, f"is longer than {MAX_WORDS[role]} words"
        if role == "opener":
            bound = [sid for sid in item.source_ids if sid in chapter_of]
            if bound:
                return text, f"cites {', '.join(bound)}, which belong to a chapter; the opener may only use the walk's totals"
        if chapter is not None:
            other = [sid for sid in item.source_ids if sid in chapter_of and chapter_of[sid] != chapter]
            if other:
                return text, f"cites {', '.join(other)} from another chapter"
        if any(_copies(text, notes[sid].transcript or "") for sid in item.source_ids if sid in notes):
            return text, "repeats the walker's words instead of connecting them"
        # The quote sits right above the chapter line; a paraphrase of it adds
        # nothing. A line has to bring in another fact (the climb, a photo, a landmark).
        own = [sid for sid in item.source_ids if sid in notes]
        if role == "chapter_line" and own and len(own) == len(item.source_ids) and any(
                _restates(text, notes[sid].transcript or "") for sid in own):
            return text, ("only says again what the walker said; connect it to another fact "
                          "(the climb, a photo, a landmark), or leave it empty")
        if any(_norm(text) == _norm(a.text) for a in accepted.values()):
            return text, "says the same as another line"
        return text, _check(text, item.source_ids, facts, vocab, 0.34 if role == "chapter_title" else 0.5)

    accepted: dict[str, GeneratedLine] = {}
    rejected: list[dict] = []
    rounds = 0

    def fact_list(ids: list[str]) -> str:
        return "\n".join(f"- {k}: {re.sub(r'\[chapter \w+\] ', '', facts[k])}" for k in ids)

    def write(system: str, schema: type[BaseModel], user: str,
              slots: Callable[[BaseModel], list[tuple[str, str, str | None, LineOut]]]) -> list[dict]:
        """Ask, judge, then ask once more to fix whatever failed the grounding rule.
        Returns the lines that still failed."""
        nonlocal rounds
        prompt, failed = user, []
        for attempt in (1, 2):
            rounds += 1
            raw = llm.chat(config.load().llm_model, system, prompt, schema=schema.model_json_schema(),
                           task="compose", temperature=0.4)
            try:
                candidates = slots(schema.model_validate_json(raw))
            except ValidationError:
                candidates = []
            failed = []
            for lid, role, chapter, item in candidates:
                if lid in accepted:
                    continue
                text, reason = judge(role, chapter, item)
                if reason is None:
                    accepted[lid] = GeneratedLine(id=lid, trek_id=trek_id, role=role, chapter=chapter, text=text,
                                                  source_ids=item.source_ids)
                elif text:
                    failed.append({"id": lid, "text": text, "why": reason})
            if not failed:
                break
            prompt = (user + "\n\nYou already wrote this. Keep the same JSON shape and fix these lines, which broke the rules:\n"
                      + "\n".join(f'- "{r["text"]}" {r["why"]}' for r in failed)
                      + "\nIf a line can't be written from its facts, leave its text empty.")
        return failed

    # One small call per chapter: a 4B model keeps a short list of facts
    # straight far better than the whole walk at once.
    for ch in chapters:
        ids = [k for k in facts if chapter_of.get(k) == ch]
        if not ids:
            continue
        user = f"This chapter is {CHAPTER_HINTS.get(ch, ch)}.\nFACTS:\n{fact_list(ids)}"
        rejected += write(CHAPTER_SYSTEM, ChapterOut, user, lambda out, ch=ch: [
            (f"{trek_id}-{ch}-title", "chapter_title", ch, out.title),
            (f"{trek_id}-{ch}-line", "chapter_line", ch, out.line)])
    if entries:
        user = f"FACTS:\n{fact_list(shared)}"
        rejected += write(OPENER_SYSTEM, OpenerOut, user, lambda out: [
            (f"{trek_id}-opener-{i}", "opener", None, item) for i, item in enumerate(out.opener[:2])])

    with db.connect() as conn:
        conn.execute("DELETE FROM generated_line WHERE trek_id = ? AND user_edited = 0", (trek_id,))
        db.upsert_many(conn, [ln for ln in accepted.values() if ln.id not in kept])
    return {"entries": len(entries), "chapters": chapters, "lines": len(accepted), "dropped_unsourced": len(rejected),
            "rounds": rounds, "rejected": rejected}


def recompose_structure_only(trek_id: str) -> None:
    """Rebuild entries without calling the model (used before any processing)."""
    entries = build_entries(trek_id)
    with db.connect() as conn:
        conn.execute("DELETE FROM entry WHERE trek_id = ?", (trek_id,))
        db.upsert_many(conn, entries)


def dump(obj) -> str:
    return json.dumps(obj, default=str)
