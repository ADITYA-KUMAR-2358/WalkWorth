"""Builds the journal from the database. One view feeds every format: the
in-app journal, the static HTML export, the PDF and the Markdown.

The grounding rule is enforced here too: a generated line with no sources is
never rendered, whatever is in the database.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from . import config, db, fortpack, gpx, steps, svgdraw
from .schema import Entry, GeneratedLine, Observation, Photo, TrackPoint, Trek, VoiceNote

ORDINALS = ["one", "two", "three", "four", "five", "six", "seven", "eight"]
LANG_NAMES = {"mr": "Marathi", "hi": "Hindi", "en": "English", "gu": "Gujarati", "kn": "Kannada"}
NATURE = {"wildlife", "plant"}


@dataclass
class PhotoView:
    id: str
    src: str
    caption: str | None
    stamp: str
    credit: str | None
    rotate: float


@dataclass
class EntryView:
    id: str
    time: str
    alt: str | None
    chip: str | None
    chip_kind: str | None  # "landmark" | "nature"
    chip_mr: str | None
    quote: str | None
    full_note: str | None
    translation: str | None
    lang_name: str | None
    note_id: str | None
    photos: list[PhotoView]
    route_frac: float
    pin: tuple[float, float] | None
    strip_pct: tuple[float, float] | None
    after_line: str | None = None


@dataclass
class ChapterView:
    key: str
    label: str
    title: str
    title_generated: bool
    line: str | None
    strip: str
    entries: list[EntryView]


@dataclass
class JournalView:
    trek: Trek
    title: str
    title_mr: str | None
    date_label: str
    opener: list[str]
    stats: list[tuple[str, str]]
    route_d: str
    route_viewbox: str
    route_approx: bool
    start_xy: tuple[float, float] | None
    pins: list[dict]
    contours: str
    aside_strip: str
    chapters: list[ChapterView]
    closing: EntryView | None
    colophon: list[str]
    credits: list[str]
    is_sample: bool
    has_content: bool
    sources_dropped: int = 0
    extra: dict = field(default_factory=dict)


def _local(t: datetime, tz: str) -> datetime:
    return t.astimezone(ZoneInfo(tz))


def _rotation(pid: str) -> float:
    h = int(hashlib.sha1(pid.encode()).hexdigest()[:6], 16) / 0xFFFFFF
    return round((h * 2 - 1) * 1.2, 2)


def _hm(seconds: int) -> str:
    h, m = divmod(round(seconds / 60), 60)
    return f"{h}:{m:02d}"


def build(trek_id: str, *, photo_src: Callable[[Photo], str], trim_m: float = 0) -> JournalView:
    cfg = config.load()
    with db.connect() as conn:
        trek = db.get(conn, Trek, trek_id)
        if trek is None:
            raise KeyError(trek_id)
        track = db.fetch(conn, TrackPoint, "trek_id = ?", (trek_id,), "t")
        notes = {n.id: n for n in db.fetch(conn, VoiceNote, "trek_id = ?", (trek_id,), "t")}
        photos = {p.id: p for p in db.fetch(conn, Photo, "trek_id = ?", (trek_id,), "t")}
        entries = db.fetch(conn, Entry, "trek_id = ?", (trek_id,), "t")
        lines = db.fetch(conn, GeneratedLine, "trek_id = ?", (trek_id,))
        obs: dict[str, list[Observation]] = {}
        for o in db.fetch(conn, Observation, "note_id IN (SELECT id FROM voice_note WHERE trek_id = ?)", (trek_id,)):
            obs.setdefault(o.note_id, []).append(o)

    if not entries:
        # Not processed yet: show the structure from what's there, no model text.
        entries = steps.build_entries(trek_id)

    pack = fortpack.load(trek.fort_pack_id) if trek.fort_pack_id else None
    landmarks = {lm.id: lm for lm in pack.landmarks} if pack else {}
    tz = trek.tz
    offset = timedelta(seconds=trek.time_offset_s)

    # The grounding rule, at render time.
    dropped = sum(1 for g in lines if not g.source_ids)
    lines = [g for g in lines if g.source_ids]
    opener = [g.text for g in sorted((g for g in lines if g.role == "opener"), key=lambda g: g.id)]
    ch_title = {g.chapter: g.text for g in lines if g.role == "chapter_title"}
    ch_line = {g.chapter: g.text for g in lines if g.role == "chapter_line"}

    # Route: the GPX if there is one, else the notes' own fixes (approximate).
    draw_track = gpx.trim_ends(track, trim_m) if trim_m else track
    route_approx = False
    latlons = [(p.lat, p.lon) for p in draw_track]
    if len(latlons) < 2:
        route_approx = True
        fixes = sorted([*notes.values(), *photos.values()], key=lambda x: x.t)
        latlons = [(x.lat, x.lon) for x in fixes if x.lat is not None]
    W, H = 600, 340
    route_d, start_xy, proj = "", None, None
    if len(latlons) >= 2:
        proj = svgdraw.Projection.fit(latlons, W, H, 18)
        step = max(1, len(latlons) // 400)
        pts = [proj(lat, lon) for lat, lon in latlons[::step]] + [proj(*latlons[-1])]
        route_d = svgdraw.polyline(pts)
        start_xy = pts[0]
    fracs = gpx.route_fractions(draw_track) if len(draw_track) >= 2 else []

    profile = None
    ele_pts = [(p.t, p.ele_m) for p in track if p.ele_m is not None]
    if len(ele_pts) >= 4:
        profile = svgdraw.Profile([t for t, _ in ele_pts], [e for _, e in ele_pts])

    def locate(t: datetime, lat, lon) -> tuple[float, tuple[float, float] | None]:
        if draw_track:
            p = gpx.nearest_in_time(draw_track, t, max_gap_s=10**9)
            i = draw_track.index(p)
            return fracs[i] if fracs else 0.0, proj(p.lat, p.lon) if proj else None
        if proj and lat is not None:
            return 0.0, proj(lat, lon)
        return 0.0, None

    def entry_view(e: Entry) -> EntryView:
        note = notes.get(e.note_id or "")
        lm = landmarks.get(e.landmark_id or "")
        chip = chip_kind = chip_mr = None
        if lm:
            chip, chip_kind, chip_mr = lm.name, "landmark", lm.name_mr
        elif note and any(o.kind in NATURE for o in obs.get(note.id, [])):
            o = next(o for o in obs[note.id] if o.kind in NATURE)
            chip, chip_kind = o.kind, "nature"
        local = _local(e.t, tz)
        lat = note.lat if note else None
        lon = note.lon if note else None
        frac, pin = locate(e.t, lat, lon)
        pviews = []
        for pid in e.photo_ids:
            ph = photos.get(pid)
            if not ph:
                continue
            pt = _local(ph.t + offset, tz)
            stamp = pt.strftime("%H:%M")
            if ph.ele_m is not None:
                stamp += f" · {ph.ele_m:.0f} m"
            pviews.append(PhotoView(id=ph.id, src=photo_src(ph), caption=ph.caption, stamp=stamp,
                                    credit=ph.credit, rotate=_rotation(ph.id)))
        lang = note.lang if note else None
        return EntryView(
            id=e.id, time=local.strftime("%H:%M"), alt=f"{e.ele_m:.0f} m" if e.ele_m is not None else None,
            chip=chip, chip_kind=chip_kind, chip_mr=chip_mr, quote=e.quote,
            full_note=note.transcript if note and note.transcript and e.quote and e.quote != note.transcript else None,
            translation=note.translation if note and note.translation and lang != "en" else None,
            lang_name=LANG_NAMES.get(lang or "", lang) if lang and lang != "en" else None,
            note_id=e.note_id, photos=pviews, route_frac=round(frac, 4), pin=pin,
            strip_pct=profile.pct(e.t, e.ele_m) if profile and e.ele_m is not None else None,
        )

    # The last voice note closes the journal.
    closing = None
    note_entries = [e for e in entries if e.note_id]
    if len(note_entries) >= 2:
        last = note_entries[-1]
        closing = entry_view(last)
        entries = [e for e in entries if e.id != last.id or e.photo_ids]
        if last.photo_ids:
            # Keep its photos in the last chapter, without repeating the quote.
            entries = [e.model_copy(update={"quote": None, "note_id": None}) if e.id == last.id else e for e in entries]
            closing.photos = []

    chapters: list[ChapterView] = []
    keys = []
    for e in entries:
        if e.chapter not in keys:
            keys.append(e.chapter)
    for i, key in enumerate(keys):
        ces = [e for e in entries if e.chapter == key]
        views = [entry_view(e) for e in ces]
        if key in ch_line and views:
            target = next((v for v in views if v.quote), views[0])
            target.after_line = ch_line[key]
        strip = ""
        if profile:
            strip = profile.svg(highlight=(ces[0].t, ces[-1].t + timedelta(seconds=1)))
        chapters.append(ChapterView(
            key=key, label=f"Chapter {ORDINALS[i] if i < len(ORDINALS) else i + 1}",
            title=ch_title.get(key, steps.DEFAULT_TITLES.get(key, key.title())),
            title_generated=key in ch_title, line=ch_line.get(key), strip=strip, entries=views,
        ))

    pins = []
    for ch in chapters:
        for v in ch.entries:
            if v.pin:
                pins.append({"id": v.id, "x": v.pin[0], "y": v.pin[1], "kind": v.chip_kind or "plain"})
    if closing and closing.pin:
        pins.append({"id": closing.id, "x": closing.pin[0], "y": closing.pin[1], "kind": closing.chip_kind or "plain"})

    stats_obj = gpx.stats(track)
    stats: list[tuple[str, str]] = []
    if stats_obj:
        stats += [(f"{stats_obj.distance_m / 1000:.1f} km", "distance"), (f"{stats_obj.gain_m:.0f} m", "climbed"),
                  (_hm(stats_obj.total_s), "on trail")]
    n_notes = sum(1 for n in notes.values() if n.transcript)
    stats += [(str(n_notes), "voice notes" if n_notes != 1 else "voice note"),
              (str(len(photos)), "photos" if len(photos) != 1 else "photo")]
    if trek.screen_seconds is not None:
        stats.append((f"{max(1, round(trek.screen_seconds / 60))} min", "screen on"))
    elif stats_obj and stats_obj.high_point_m:
        stats.append((f"{stats_obj.high_point_m:.0f} m", "high point"))

    mv = trek.model_versions or {}
    # Older treks were transcribed locally by Whisper; say so truthfully.
    stt = f"ElevenLabs {mv['stt']}" if "stt" in mv else f"whisper {mv['whisper']}" if "whisper" in mv else f"ElevenLabs {cfg.stt_model}"
    colophon = [
        f"Transcribed by {stt}",
        f"Written by {mv.get('llm', cfg.llm_model)}, captions by {mv.get('vision', cfg.vision_model)}",
        "Every generated line cites the notes it came from",
    ]
    if trek.screen_seconds is not None:
        colophon.append(f"Screen on for {max(1, round(trek.screen_seconds / 60))} minutes of the walk")
    if route_approx:
        colophon.append("Route drawn from the voice notes' GPS fixes, so it's approximate")
    credits = sorted({p.credit for p in photos.values() if p.credit})
    if pack and any(lm.osm_id for lm in pack.landmarks):
        credits.append("Landmarks: © OpenStreetMap contributors, ODbL")

    start_local = _local(trek.started_at, tz)
    return JournalView(
        trek=trek, title=trek.title, title_mr=trek.title_mr,
        date_label=start_local.strftime("%a %d %b %Y").replace(" 0", " "),
        opener=opener, stats=stats, route_d=route_d, route_viewbox=f"0 0 {W} {H}", route_approx=route_approx,
        start_xy=start_xy, pins=pins,
        contours=svgdraw.contours([e for _, e in ele_pts]) if ele_pts else "",
        aside_strip=profile.svg(cls="strip strip--aside") if profile else "",
        chapters=chapters, closing=closing, colophon=colophon, credits=credits,
        is_sample=trek.is_sample, has_content=bool(chapters or closing), sources_dropped=dropped,
    )
