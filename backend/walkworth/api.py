"""HTTP API for the web app, and in production the web app itself.

    uv run --project backend uvicorn walkworth.api:app --port 8000

On Render (see render.yaml) the same process also runs the Temporal worker
(WALKWORTH_EMBEDDED_WORKER=1) and pings its own public /api/health every
5 minutes so the free instance doesn't spin down.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
from datetime import datetime, timezone

import httpx
from collections import deque

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import access, config, db, fixtures, fortpack, gpx, importer, llm, render, steps, temporal, worker
from .schema import (
    Entry, FortPack, GeneratedLine, ImportResult, Photo, ProcessStatus, TrackPoint, Trek, TrekSummary, VoiceNote,
)

log = logging.getLogger("walkworth")
KEEP_ALIVE_EVERY_S = 5 * 60
# A 90 s voice note is well under 2 MB and a phone photo under 15 MB; a long
# GPX can reach a few MB. Anything bigger is refused before it's read.
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MAX_FILES_PER_UPLOAD = 100
WEB_DIST = config.REPO_ROOT / "web" / "dist"


async def _keep_alive(url: str) -> None:
    """Render's free plan spins an instance down after 15 idle minutes. A
    request through the public URL every 5 minutes counts as traffic."""
    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            await asyncio.sleep(KEEP_ALIVE_EVERY_S)
            try:
                await client.get(f"{url}/api/health")
            except httpx.HTTPError as e:
                log.warning("keep-alive ping failed: %s", e)


async def _embedded_worker(stop: asyncio.Event) -> None:
    """The worker inside the web process, for single-service deploys. Retries
    until Temporal is up, since both start together."""
    while not stop.is_set():
        try:
            await worker.run(stop)
            return
        except Exception as e:  # noqa: BLE001
            log.warning("worker couldn't start (%s); retrying in 5 s", e)
            temporal._client = None
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), 5)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    # Render's disk is wiped on each deploy, so bring the sample back.
    with db.connect() as conn:
        empty = conn.execute("SELECT 1 FROM trek LIMIT 1").fetchone() is None
    if empty:
        fixtures.load()
    tasks: list[asyncio.Task] = []
    stop = asyncio.Event()
    if os.environ.get("WALKWORTH_EMBEDDED_WORKER") == "1":
        tasks.append(asyncio.create_task(_embedded_worker(stop)))
    if url := os.environ.get("RENDER_EXTERNAL_URL"):
        tasks.append(asyncio.create_task(_keep_alive(url.rstrip("/"))))
    yield
    stop.set()
    for t in tasks:
        if not t.done():
            t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="Walkworth", docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "SAMEORIGIN",
    "Permissions-Policy": "microphone=(self), geolocation=(self), camera=(self)",
}


@app.middleware("http")
async def headers(request: Request, call_next):
    response = await call_next(request)
    for k, v in SECURITY_HEADERS.items():
        response.headers.setdefault(k, v)
    path = request.url.path
    if path.startswith("/assets/"):
        # Vite puts a content hash in every file name under /assets.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path == "/" or path.endswith((".html", ".webmanifest")):
        response.headers["Cache-Control"] = "no-cache"
    return response


def _summary(conn, trek: Trek, request: Request) -> TrekSummary:
    points = db.fetch(conn, TrackPoint, "trek_id = ?", (trek.id,), "t")
    note_count = conn.execute("SELECT COUNT(*) FROM voice_note WHERE trek_id = ?", (trek.id,)).fetchone()[0]
    photo_count = conn.execute("SELECT COUNT(*) FROM photo WHERE trek_id = ?", (trek.id,)).fetchone()[0]
    processed = conn.execute("SELECT 1 FROM entry WHERE trek_id = ? LIMIT 1", (trek.id,)).fetchone() is not None
    return TrekSummary(trek=trek, stats=gpx.stats(points), note_count=note_count, photo_count=photo_count,
                       route=gpx.thumbnail_route(points), processed=processed,
                       can_edit=access.is_mine(conn, trek, request))


def _trek_or_404(conn, trek_id: str) -> Trek:
    trek = db.get(conn, Trek, trek_id)
    if trek is None:
        raise HTTPException(404, f"No trek with id '{trek_id}'.")
    return trek


# ---- Health and fort packs -------------------------------------------------


@app.get("/api/health")
def health() -> dict:
    cfg = config.load()
    return {
        "ok": True,
        "models": {"llm": cfg.llm_model, "vision": cfg.vision_model, "stt": cfg.stt_model},
        "keys": llm.status(),
        "access_code": access.code_required(),
    }


@app.get("/api/fortpacks", response_model=list[FortPack])
def list_fortpacks() -> list[FortPack]:
    return fortpack.available()


@app.get("/api/fortpacks/{pack_id}", response_model=FortPack)
def get_fortpack(pack_id: str) -> FortPack:
    pack = fortpack.load(pack_id)
    if pack is None:
        raise HTTPException(404, f"No fort pack '{pack_id}'.")
    return pack


# ---- Treks ------------------------------------------------------------------


@app.get("/api/treks", response_model=list[TrekSummary])
def list_treks(request: Request) -> list[TrekSummary]:
    """This browser's treks and the sample. Other people's walks never show."""
    with db.connect() as conn:
        treks = [t for t in db.fetch(conn, Trek, order="started_at DESC") if access.on_shelf(conn, t, request)]
        return [_summary(conn, t, request) for t in treks]


class NewTrek(BaseModel):
    title: str = ""
    fort_pack_id: str | None = None


@app.post("/api/treks", response_model=Trek)
def create_trek(body: NewTrek, request: Request, response: Response) -> Trek:
    access.check_code(request)
    owner = access.ensure_token(request, response)
    with db.connect() as conn:
        trek = importer.create_trek(conn, body.title[:120], body.fort_pack_id)
        access.claim(conn, trek.id, owner)
        return trek


@app.get("/api/treks/{trek_id}", response_model=TrekSummary)
def get_trek(trek_id: str, request: Request) -> TrekSummary:
    with db.connect() as conn:
        return _summary(conn, _trek_or_404(conn, trek_id), request)


@app.delete("/api/treks/{trek_id}")
def delete_trek(trek_id: str, request: Request) -> dict:
    with db.connect() as conn:
        access.writable(conn, trek_id, request)
        conn.execute("DELETE FROM observation WHERE note_id IN (SELECT id FROM voice_note WHERE trek_id = ?)", (trek_id,))
        conn.execute("DELETE FROM trek WHERE id = ?", (trek_id,))
    # Media and exports too; the sample's fixtures live elsewhere and stay.
    shutil.rmtree(importer.trek_dir(trek_id), ignore_errors=True)
    shutil.rmtree(render.export_dir(trek_id), ignore_errors=True)
    return {"deleted": trek_id}


@app.post("/api/treks/{trek_id}/files", response_model=list[ImportResult])
async def upload_files(trek_id: str, request: Request, files: list[UploadFile] = File(...),
                       last_modified: str = Form("[]")) -> list[ImportResult]:
    try:
        times = json.loads(last_modified or "[]")
        if not isinstance(times, list):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "last_modified must be a JSON list of millisecond timestamps.") from None
    if len(files) > MAX_FILES_PER_UPLOAD:
        raise HTTPException(413, f"That's {len(files)} files. Add up to {MAX_FILES_PER_UPLOAD} at a time.")
    results = []
    with db.connect() as conn:
        trek = access.writable(conn, trek_id, request)
        for i, f in enumerate(files):
            if _too_big(f):
                results.append(ImportResult(name=f.filename or "file", kind="unknown", status="skipped",
                                            detail=f"Bigger than {MAX_UPLOAD_BYTES // 2**20} MB"))
                continue
            ms = times[i] if i < len(times) and isinstance(times[i], (int, float)) else None
            lm = datetime.fromtimestamp(ms / 1000, tz=timezone.utc) if ms else None
            data = await f.read()
            results.append(importer.import_file(conn, trek, f.filename or "file", data, lm))
            conn.commit()
    return results


def _too_big(f: UploadFile) -> bool:
    return f.size is not None and f.size > MAX_UPLOAD_BYTES


@app.post("/api/treks/{trek_id}/capture", response_model=ImportResult)
async def capture(trek_id: str, request: Request, file: UploadFile = File(...), kind: str = Form(...), t: str = Form(...),
                  lat: float | None = Form(None), lon: float | None = Form(None),
                  duration_s: float | None = Form(None)) -> ImportResult:
    """One voice note or photo from Trail mode, uploaded as soon as it's taken."""
    if kind not in ("note", "photo"):
        raise HTTPException(400, "kind must be note or photo.")
    try:
        when = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(400, "t must be an ISO 8601 time.") from None
    when = when.astimezone(timezone.utc) if when.tzinfo else when.replace(tzinfo=timezone.utc)
    if lat is not None and not (-90 <= lat <= 90) or lon is not None and not (-180 <= lon <= 180):
        raise HTTPException(400, "lat/lon out of range.")
    if _too_big(file):
        raise HTTPException(413, f"That file is bigger than {MAX_UPLOAD_BYTES // 2**20} MB.")
    data = await file.read()
    if not data:
        raise HTTPException(400, "The file is empty.")
    with db.connect() as conn:
        trek = access.writable(conn, trek_id, request)
        return importer.capture(conn, trek, kind, file.filename or kind, data, when, lat, lon, duration_s)


class Finish(BaseModel):
    screen_seconds: int | None = None


@app.post("/api/treks/{trek_id}/finish", response_model=Trek)
def finish(trek_id: str, body: Finish, request: Request) -> Trek:
    """End of a walk: keep the screen-on time (the shorter the better)."""
    with db.connect() as conn:
        trek = access.writable(conn, trek_id, request)
        update: dict = {}
        if body.screen_seconds is not None:
            update["screen_seconds"] = max(0, body.screen_seconds)
        if trek.ended_at is None:
            update["ended_at"] = datetime.now(timezone.utc)
        trek = trek.model_copy(update=update)
        db.upsert(conn, trek)
        return trek


@app.get("/api/treks/{trek_id}/track", response_model=list[TrackPoint])
def get_track(trek_id: str) -> list[TrackPoint]:
    with db.connect() as conn:
        _trek_or_404(conn, trek_id)
        pts = db.fetch(conn, TrackPoint, "trek_id = ?", (trek_id,), "t")
    step = max(1, len(pts) // 600)
    return pts[::step] + ([pts[-1]] if pts and pts[::step][-1] is not pts[-1] else [])


class Offset(BaseModel):
    seconds: int


@app.put("/api/treks/{trek_id}/time-offset", response_model=Trek)
def set_offset(trek_id: str, body: Offset, request: Request) -> Trek:
    with db.connect() as conn:
        trek = access.writable(conn, trek_id, request)
        trek = trek.model_copy(update={"time_offset_s": max(-6 * 3600, min(6 * 3600, body.seconds))})
        db.upsert(conn, trek)
        return trek


@app.get("/api/treks/{trek_id}/notes", response_model=list[VoiceNote])
def list_notes(trek_id: str) -> list[VoiceNote]:
    with db.connect() as conn:
        return db.fetch(conn, VoiceNote, "trek_id = ?", (trek_id,), "t")


@app.get("/api/treks/{trek_id}/photos", response_model=list[Photo])
def list_photos(trek_id: str) -> list[Photo]:
    with db.connect() as conn:
        return db.fetch(conn, Photo, "trek_id = ?", (trek_id,), "t")


@app.get("/api/treks/{trek_id}/entries", response_model=list[Entry])
def list_entries(trek_id: str) -> list[Entry]:
    with db.connect() as conn:
        _trek_or_404(conn, trek_id)
        entries = db.fetch(conn, Entry, "trek_id = ?", (trek_id,), "t")
    return entries or steps.build_entries(trek_id)


@app.get("/api/treks/{trek_id}/lines", response_model=list[GeneratedLine])
def list_lines(trek_id: str) -> list[GeneratedLine]:
    with db.connect() as conn:
        return db.fetch(conn, GeneratedLine, "trek_id = ?", (trek_id,), "id")


# ---- Processing (Temporal) ---------------------------------------------------


SUBMIT_WINDOW_S = 10 * 60
_TEMPORAL_DOWN = "The journal pipeline isn't up yet. Try again in a minute; finished steps won't run twice."
_submits: dict[str, deque[float]] = {}


def _spend_guard(request: Request) -> None:
    """SUBMIT_LIMIT: requests that spend model quota, per visitor per 10 min."""
    limit = config.load().submit_limit
    if limit <= 0:
        return
    who = request.client.host if request.client else "unknown"
    now = asyncio.get_running_loop().time()
    hits = _submits.setdefault(who, deque())
    while hits and now - hits[0] > SUBMIT_WINDOW_S:
        hits.popleft()
    if len(hits) >= limit:
        wait_min = max(1, round((SUBMIT_WINDOW_S - (now - hits[0])) / 60))
        raise HTTPException(429, f"That's {limit} journal runs in 10 minutes. Try again in about {wait_min} min.")
    hits.append(now)


@app.post("/api/treks/{trek_id}/process", response_model=ProcessStatus)
async def start_process(trek_id: str, request: Request) -> ProcessStatus:
    with db.connect() as conn:
        access.writable(conn, trek_id, request)
    _spend_guard(request)
    try:
        await temporal.start_process(trek_id)
    except Exception as e:  # noqa: BLE001
        log.warning("couldn't start processing %s: %s", trek_id, e)
        raise HTTPException(503, _TEMPORAL_DOWN) from e
    return await temporal.status(trek_id)


@app.get("/api/treks/{trek_id}/process", response_model=ProcessStatus)
async def process_status(trek_id: str) -> ProcessStatus:
    return await temporal.status(trek_id)


@app.post("/api/treks/{trek_id}/recompose", response_model=ProcessStatus)
async def start_recompose(trek_id: str, request: Request) -> ProcessStatus:
    with db.connect() as conn:
        access.writable(conn, trek_id, request)
    _spend_guard(request)
    try:
        await temporal.start_recompose(trek_id)
    except Exception as e:  # noqa: BLE001
        log.warning("couldn't start recompose for %s: %s", trek_id, e)
        raise HTTPException(503, _TEMPORAL_DOWN) from e
    return await temporal.status(trek_id, "recompose")


@app.get("/api/treks/{trek_id}/recompose", response_model=ProcessStatus)
async def recompose_status(trek_id: str) -> ProcessStatus:
    return await temporal.status(trek_id, "recompose")


# ---- Review edits ---------------------------------------------------------------


class NoteEdit(BaseModel):
    transcript: str


@app.patch("/api/notes/{note_id}", response_model=VoiceNote)
def edit_note(note_id: str, body: NoteEdit, request: Request) -> VoiceNote:
    text = body.transcript.strip()
    if not text:
        raise HTTPException(422, "A transcript can't be empty. Delete the note instead.")
    with db.connect() as conn:
        note = db.get(conn, VoiceNote, note_id)
        if note is None:
            raise HTTPException(404, "That voice note no longer exists.")
        access.writable(conn, note.trek_id, request)
        note = note.model_copy(update={"transcript": text, "quote": text, "words": [], "user_edited": True, "skipped": None})
        db.upsert(conn, note)
        conn.execute("UPDATE entry SET quote = ? WHERE note_id = ?", (text, note_id))
        return note


@app.delete("/api/notes/{note_id}")
def delete_note(note_id: str, request: Request) -> dict:
    with db.connect() as conn:
        note = db.get(conn, VoiceNote, note_id)
        if note is None:
            raise HTTPException(404, "That voice note no longer exists.")
        access.writable(conn, note.trek_id, request)
        conn.execute("DELETE FROM voice_note WHERE id = ?", (note_id,))
        conn.execute("DELETE FROM entry WHERE note_id = ?", (note_id,))
        conn.execute("UPDATE photo SET pinned_note_id = NULL WHERE pinned_note_id = ?", (note_id,))
    return {"deleted": note_id}


class PhotoEdit(BaseModel):
    pinned_note_id: str | None = None
    caption: str | None = None


@app.patch("/api/photos/{photo_id}", response_model=Photo)
def edit_photo(photo_id: str, body: PhotoEdit, request: Request) -> Photo:
    with db.connect() as conn:
        photo = db.get(conn, Photo, photo_id)
        if photo is None:
            raise HTTPException(404, "That photo no longer exists.")
        access.writable(conn, photo.trek_id, request)
        update: dict = {}
        if "pinned_note_id" in body.model_fields_set:
            update["pinned_note_id"] = body.pinned_note_id
        if body.caption is not None:
            update.update(caption=body.caption.strip() or None, user_edited=True, caption_source_ids=[photo_id, "user"])
        photo = photo.model_copy(update=update)
        db.upsert(conn, photo)
        return photo


class LineEdit(BaseModel):
    role: str
    chapter: str | None = None
    text: str


@app.put("/api/treks/{trek_id}/lines", response_model=GeneratedLine | None)
def edit_line(trek_id: str, body: LineEdit, request: Request) -> GeneratedLine | None:
    """The user rewrites a line. Their words are their own source."""
    if body.role not in ("opener", "chapter_title", "chapter_line"):
        raise HTTPException(422, "Unknown line type.")
    lid = (f"{trek_id}-opener-0" if body.role == "opener" else
           f"{trek_id}-{body.chapter}-{'title' if body.role == 'chapter_title' else 'line'}")
    with db.connect() as conn:
        access.writable(conn, trek_id, request)
        if not body.text.strip():
            conn.execute("DELETE FROM generated_line WHERE id = ?", (lid,))
            return None
        line = GeneratedLine(id=lid, trek_id=trek_id, role=body.role, chapter=body.chapter, text=body.text.strip(),
                             source_ids=["user"], user_edited=True)
        db.upsert(conn, line)
        return line


# ---- Journal and files ---------------------------------------------------------------


@app.get("/journal/{trek_id}", response_class=HTMLResponse)
def journal_page(trek_id: str) -> str:
    with db.connect() as conn:
        _trek_or_404(conn, trek_id)
    return render.render_app(trek_id)


@app.get("/journal-assets/fonts/{name}")
def journal_font(name: str) -> FileResponse:
    path = render.FONTS / name
    if path.parent != render.FONTS or not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="font/woff2", headers={"Cache-Control": "max-age=31536000"})


@app.get("/files/{path:path}")
def media_file(path: str, request: Request) -> FileResponse:
    """Media by its stored path ("data/treks/..." or "fixtures/..."). Only trek
    media and the sample fixtures; never the database, exports or anything else.
    A walker's raw audio goes only to the browser that recorded it."""
    target = config.media_path(path).resolve()
    treks = (config.load().data_dir / "treks").resolve()
    allowed = [treks, (config.REPO_ROOT / "fixtures").resolve()]
    if not any(target.is_relative_to(a) for a in allowed) or not target.is_file():
        raise HTTPException(404, "No such file.")
    if target.is_relative_to(treks):
        parts = target.relative_to(treks).parts
        if len(parts) > 1 and parts[1] == "audio":
            with db.connect() as conn:
                if not access.can_read_audio(conn, parts[0], request):
                    raise HTTPException(404, "No such file.")
    return FileResponse(target)


# ---- Exports -------------------------------------------------------------------------


class ExportOut(BaseModel):
    kind: str
    name: str
    bytes: int
    url: str


EXPORTERS = {
    "html": render.export_html,
    "pdf": render.export_pdf,
    "gpx": render.export_gpx,
    "markdown": render.export_markdown,
}


@app.post("/api/treks/{trek_id}/exports/{kind}", response_model=ExportOut)
def build_export(trek_id: str, kind: str, trim: bool = True) -> ExportOut:
    with db.connect() as conn:
        _trek_or_404(conn, trek_id)
    if kind not in EXPORTERS:
        raise HTTPException(404, f"Walkworth exports {', '.join(EXPORTERS)}.")
    try:
        f = EXPORTERS[kind](trek_id, trim)
    except RuntimeError as e:
        raise HTTPException(503, str(e)) from e
    return ExportOut(kind=kind, name=f.path.name, bytes=f.bytes, url=f"/api/exports/{trek_id}/{f.path.name}")


@app.get("/api/exports/{trek_id}/{name}")
def download_export(trek_id: str, name: str) -> FileResponse:
    base = render.export_dir(trek_id)
    path = (base / name).resolve()
    if path.parent != base.resolve() or not path.is_file():
        raise HTTPException(404, "Build this export first.")
    return FileResponse(path, filename=name)


@app.get("/api/treks/{trek_id}/dev-markdown", response_class=PlainTextResponse)
def dev_markdown(trek_id: str) -> str:
    with db.connect() as conn:
        _trek_or_404(conn, trek_id)
    return render.dev_markdown(trek_id)


# ---- The web app (production build) -----------------------------------------
# Mounted last so every route above wins. In development Vite serves the app
# on :5173 and proxies to this API instead.
if WEB_DIST.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIST, html=True), name="web")
