"""Loads fixtures/sinhagad-sample/ into SQLite.

    uv run --project backend python -m walkworth.fixtures
"""

from __future__ import annotations

import hashlib
import json

from . import config, db, gpx, media
from .schema import Entry, GeneratedLine, Observation, Photo, Trek, VoiceNote

FIXTURE_DIR = config.REPO_ROOT / "fixtures" / "sinhagad-sample"
SNAPSHOT = FIXTURE_DIR / "processed.json"
AUDIO_EXTS = (".m4a", ".mp3", ".ogg", ".opus", ".wav", ".webm", ".aac", ".flac")


def _audio(note: dict) -> tuple[str | None, float]:
    """The note's recording and its length. A real recording dropped in as
    audio/note-01.m4a (any format above) replaces the manifest's file."""
    for ext in AUDIO_EXTS:
        path = FIXTURE_DIR / "audio" / f"{note['id']}{ext}"
        if path.is_file():
            rel = path.relative_to(FIXTURE_DIR).as_posix()
            if rel == note.get("audio"):
                return rel, note["duration_s"]
            return rel, round(media.audio_duration(path), 2)
    return None, note.get("duration_s") or 0.0


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot() -> int:
    """Save the sample's pipeline results (transcripts, captions, journal lines)
    so a fresh deploy shows the sample journal without spending API credits.

        uv run --project backend python -m walkworth.fixtures --snapshot

    Each note keeps the hash of the audio it was made from; load() applies a
    note's results only while that audio is unchanged."""
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    tid = manifest["trek"]["id"]
    with db.connect() as conn:
        notes = db.fetch(conn, VoiceNote, "trek_id = ?", (tid,), "id")
        photos = db.fetch(conn, Photo, "trek_id = ?", (tid,), "id")
        obs = db.fetch(conn, Observation, "note_id IN (SELECT id FROM voice_note WHERE trek_id = ?)", (tid,), "id")
        entries = db.fetch(conn, Entry, "trek_id = ?", (tid,), "id")
        lines = db.fetch(conn, GeneratedLine, "trek_id = ?", (tid,), "id")
        versions = db.get(conn, Trek, tid).model_versions
    if not any(n.transcript for n in notes) or not lines:
        raise SystemExit("Run the pipeline on the sample first (Make the journal), then snapshot.")
    keep_note = ("transcript", "words", "lang", "translation", "confidence", "skipped", "quote", "mood",
                 "lat", "lon", "ele_m", "location_source", "landmark_id")
    keep_photo = ("caption", "caption_source_ids", "lat", "lon", "ele_m", "location_source", "landmark_id")
    out = {
        "model_versions": versions,
        "notes": {n.id: {"audio_sha256": _sha(config.media_path(n.audio_path)) if n.audio_path else None,
                         **n.model_dump(mode="json", include=set(keep_note))} for n in notes},
        "photos": {p.id: p.model_dump(mode="json", include=set(keep_photo)) for p in photos},
        "observations": [o.model_dump(mode="json") for o in obs],
        "entries": [e.model_dump(mode="json") for e in entries],
        "lines": [ln.model_dump(mode="json") for ln in lines],
    }
    SNAPSHOT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(lines)


def load() -> str:
    cfg = config.load()
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    meta = manifest["trek"]
    trek_id = meta["id"]

    track = gpx.read_track(FIXTURE_DIR / "track.gpx", trek_id)
    trek = Trek(
        id=trek_id,
        title=meta["title"],
        title_mr=meta.get("title_mr"),
        fort_pack_id=meta.get("fort_pack_id"),
        started_at=track[0].t,
        ended_at=track[-1].t,
        tz=cfg.timezone,
        screen_seconds=meta.get("screen_seconds"),
        model_versions={"stt": cfg.stt_model, "llm": cfg.llm_model, "vision": cfg.vision_model},
        is_sample=True,
    )
    notes = []
    for n in manifest["notes"]:
        audio, duration = _audio(n)
        notes.append(VoiceNote(
            id=n["id"], trek_id=trek_id, t=n["t"], lat=n["lat"], lon=n["lon"],
            location_source="fix" if n["lat"] is not None else "none",
            duration_s=duration,
            audio_path=f"fixtures/sinhagad-sample/{audio}" if audio else None,
            # Without audio, fall back to the script so the journal still has words.
            transcript=None if audio else n["script"],
            lang=None if audio else "en",
        ))
    photos = [
        Photo(
            id=p["id"],
            trek_id=trek_id,
            t=p["t"],
            lat=p["lat"],
            lon=p["lon"],
            path=f"fixtures/sinhagad-sample/{p['path']}",
            sha256=hashlib.sha256((FIXTURE_DIR / p["path"]).read_bytes()).hexdigest(),
            credit=p.get("credit"),
        )
        for p in manifest["photos"]
    ]

    snap = json.loads(SNAPSHOT.read_text(encoding="utf-8")) if SNAPSHOT.is_file() else None
    reused = False
    if snap:
        # Use the saved results only for notes whose audio hasn't changed.
        fresh = []
        for n in notes:
            saved = snap["notes"].get(n.id)
            same = saved and n.audio_path and saved["audio_sha256"] == _sha(config.media_path(n.audio_path))
            # Validate rather than model_copy, so saved words become Word models.
            restored = {k: v for k, v in saved.items() if k != "audio_sha256"} if same else None
            fresh.append(VoiceNote.model_validate({**n.model_dump(), **restored}) if restored else n)
        reused = all(a is not b for a, b in zip(fresh, notes))
        if reused:
            notes = fresh
            photos = [p.model_copy(update=snap["photos"].get(p.id, {})) for p in photos]
            trek = trek.model_copy(update={"model_versions": snap.get("model_versions") or trek.model_versions})

    with db.connect() as conn:
        conn.execute("DELETE FROM observation WHERE note_id IN (SELECT id FROM voice_note WHERE trek_id = ?)", (trek_id,))
        conn.execute("DELETE FROM trek WHERE id = ?", (trek_id,))
        db.upsert(conn, trek)
        db.upsert_many(conn, gpx.downsample(track, 5.0))
        db.upsert_many(conn, notes)
        db.upsert_many(conn, photos)
        if reused:
            db.upsert_many(conn, [Observation.model_validate(o) for o in snap["observations"]])
            db.upsert_many(conn, [Entry.model_validate(e) for e in snap["entries"]])
            db.upsert_many(conn, [GeneratedLine.model_validate(ln) for ln in snap["lines"]])
    return trek_id


if __name__ == "__main__":
    import sys

    if "--snapshot" in sys.argv:
        print(f"saved {snapshot()} journal lines to {SNAPSHOT.relative_to(config.REPO_ROOT)}")
        raise SystemExit(0)
    tid = load()
    print(f"loaded sample trek '{tid}' into {config.load().database.relative_to(config.REPO_ROOT)}")
