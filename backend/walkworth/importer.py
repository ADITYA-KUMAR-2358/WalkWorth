"""Import: GPX tracks, photos (JPEG/HEIC/PNG), loose audio, and live captures
from Trail mode. Everything lands under data/treks/<trek_id>/.

Re-importing the same file is a no-op: notes and photos are keyed by a hash
of their content.
"""

from __future__ import annotations

import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from . import config, db, fortpack, gpx, media
from .schema import ImportResult, Photo, Trek, VoiceNote

AUDIO_EXT = {".webm", ".ogg", ".opus", ".m4a", ".mp4", ".mp3", ".wav", ".aac", ".flac"}
PHOTO_EXT = {".jpg", ".jpeg", ".heic", ".heif", ".png"}
MAX_NOTE_S = 90


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "walk"


def trek_dir(trek_id: str) -> Path:
    return config.load().data_dir / "treks" / trek_id


def rel(path: Path) -> str:
    """How a media path is stored (see config.store_path)."""
    return config.store_path(path)


def create_trek(conn: sqlite3.Connection, title: str, fort_pack_id: str | None) -> Trek:
    cfg = config.load()
    pack = fortpack.load(fort_pack_id) if fort_pack_id else None
    trek = Trek(
        id=f"{slug(title)}-{secrets.token_hex(6)}",
        title=title.strip() or (pack.name if pack else "A walk"),
        title_mr=pack.name_mr if pack and title.strip() in ("", pack.name) else None,
        fort_pack_id=pack.id if pack else None,
        started_at=datetime.now(timezone.utc),
        tz=cfg.timezone,
        model_versions={"stt": cfg.stt_model, "llm": cfg.llm_model, "vision": cfg.vision_model},
    )
    db.upsert(conn, trek)
    return trek


def refresh_bounds(conn: sqlite3.Connection, trek_id: str) -> None:
    """started_at/ended_at span every track point, note and photo."""
    row = conn.execute(
        """SELECT MIN(t), MAX(t) FROM (
             SELECT t FROM track_point WHERE trek_id = :id
             UNION ALL SELECT t FROM voice_note WHERE trek_id = :id
             UNION ALL SELECT t FROM photo WHERE trek_id = :id)""",
        {"id": trek_id},
    ).fetchone()
    if row[0]:
        conn.execute("UPDATE trek SET started_at = ?, ended_at = ? WHERE id = ?", (row[0], row[1], trek_id))


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    t = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return t.astimezone(timezone.utc) if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _add_photo(conn, trek: Trek, name: str, data: bytes, fallback_t: datetime | None,
               lat: float | None = None, lon: float | None = None) -> ImportResult:
    digest = media.sha256(data)
    if conn.execute("SELECT 1 FROM photo WHERE trek_id = ? AND sha256 = ?", (trek.id, digest)).fetchone():
        return ImportResult(name=name, kind="photo", status="duplicate", detail="Already imported")
    try:
        info = media.read_photo(data, trek.tz)
    except Exception as e:  # noqa: BLE001 - any decoder failure means "not a usable photo"
        return ImportResult(name=name, kind="photo", status="skipped", detail=f"Couldn't read this image ({e.__class__.__name__})")
    t = info.t or (fallback_t.astimezone(timezone.utc) if fallback_t else None)
    if t is None:
        return ImportResult(name=name, kind="photo", status="skipped", detail="No capture time in the photo or file")
    pid = f"{trek.id}-p-{digest[:10]}"
    path = trek_dir(trek.id) / "photos" / f"{pid}.jpg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(info.jpeg)
    has_exif_gps = info.lat is not None
    db.upsert(conn, Photo(
        id=pid, trek_id=trek.id, t=t,
        lat=info.lat if has_exif_gps else lat,
        lon=info.lon if has_exif_gps else lon,
        location_source="fix" if (has_exif_gps or lat is not None) else "none",
        ele_m=info.ele_m, path=rel(path), sha256=digest,
    ))
    detail = None if info.t else "No time in the photo; used the time it was taken or uploaded"
    return ImportResult(name=name, kind="photo", status="added", detail=detail)


def _add_audio(conn, trek: Trek, name: str, data: bytes, t: datetime | None,
               lat: float | None = None, lon: float | None = None, duration_s: float | None = None,
               ended_at: datetime | None = None) -> ImportResult:
    digest = media.sha256(data)
    nid = f"{trek.id}-n-{digest[:10]}"
    if conn.execute("SELECT 1 FROM voice_note WHERE id = ?", (nid,)).fetchone():
        return ImportResult(name=name, kind="audio", status="duplicate", detail="Already imported")
    ext = PurePosixPath(name).suffix.lower() or ".webm"
    path = trek_dir(trek.id) / "audio" / f"{nid}{ext}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    try:
        dur = duration_s or media.audio_duration(path)
    except Exception:  # noqa: BLE001
        path.unlink(missing_ok=True)
        return ImportResult(name=name, kind="audio", status="skipped", detail="Couldn't decode this audio file")
    if t is None and ended_at is not None:
        t = ended_at - timedelta(seconds=dur)
    if t is None:
        path.unlink(missing_ok=True)
        return ImportResult(name=name, kind="audio", status="skipped", detail="No recording time")
    db.upsert(conn, VoiceNote(
        id=nid, trek_id=trek.id, t=t, lat=lat, lon=lon,
        location_source="fix" if lat is not None else "none",
        audio_path=rel(path), duration_s=round(min(dur, MAX_NOTE_S + 5), 2),
    ))
    return ImportResult(name=name, kind="audio", status="added")


def _add_gpx(conn, trek: Trek, name: str, data: bytes) -> ImportResult:
    try:
        points = gpx.read_track(data, trek.id)
    except Exception:  # noqa: BLE001
        return ImportResult(name=name, kind="gpx", status="skipped", detail="Not a readable GPX file")
    if len(points) < 2:
        return ImportResult(name=name, kind="gpx", status="skipped", detail="No timed track points")
    before = conn.execute("SELECT COUNT(*) FROM track_point WHERE trek_id = ?", (trek.id,)).fetchone()[0]
    db.upsert_many(conn, gpx.downsample(points, 5.0))
    after = conn.execute("SELECT COUNT(*) FROM track_point WHERE trek_id = ?", (trek.id,)).fetchone()[0]
    if after == before:
        return ImportResult(name=name, kind="gpx", status="duplicate", detail="Already imported")
    stats = gpx.stats(points)
    return ImportResult(name=name, kind="gpx", status="added",
                        detail=f"{after - before} points, {stats.distance_m / 1000:.1f} km" if stats else None)


def capture(conn: sqlite3.Connection, trek: Trek, kind: str, name: str, data: bytes, t: datetime,
            lat: float | None, lon: float | None, duration_s: float | None) -> ImportResult:
    """One note or photo straight from Trail mode, with the phone's own time
    and GPS fix. Safe to retry: a file already stored comes back "duplicate"."""
    if kind == "note":
        result = _add_audio(conn, trek, name, data, t, lat, lon, duration_s)
    else:
        result = _add_photo(conn, trek, name, data, t, lat, lon)
    refresh_bounds(conn, trek.id)
    return result


def import_file(conn: sqlite3.Connection, trek: Trek, name: str, data: bytes,
                last_modified: datetime | None = None) -> ImportResult:
    ext = PurePosixPath(name).suffix.lower()
    if ext == ".gpx":
        result = _add_gpx(conn, trek, name, data)
    elif ext in PHOTO_EXT:
        result = _add_photo(conn, trek, name, data, last_modified)
    elif ext in AUDIO_EXT:
        # A loose file's modified time is when the recording ended.
        result = _add_audio(conn, trek, name, data, None, ended_at=last_modified)
    else:
        result = ImportResult(name=name, kind="unknown", status="skipped",
                              detail="Walkworth reads .gpx, photos and audio files")
    refresh_bounds(conn, trek.id)
    return result
