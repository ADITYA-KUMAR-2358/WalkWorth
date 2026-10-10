"""Generates fixtures/sinhagad-sample/: a synthetic GPX, sample transcripts,
and the photo manifest.

SAMPLE DATA. The path follows the real "Sinhagad Trail" ways from
OpenStreetMap (fortpacks/osm/sinhagad-trail.json) and the gate positions from
the fort pack, but every timestamp, elevation and transcript here is invented
for testing. Photos are other people's openly licensed Wikimedia Commons
images, credited by name. All of it is replaced by a real trek.

    python scripts/make_sample_fixture.py
"""

from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fixtures" / "sinhagad-sample"
IST = timezone(timedelta(hours=5, minutes=30))
STEP_S = 5

rng = random.Random(1010)


def lerp(a: float, b: float, f: float) -> float:
    return a + (b - a) * f


def dist_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    dy = (b[0] - a[0]) * 111_320
    dx = (b[1] - a[1]) * 111_320 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


def resample(path: list[tuple[float, float]], n: int) -> list[tuple[float, float]]:
    """n points evenly spaced by distance along `path`."""
    cum = [0.0]
    for a, b in zip(path, path[1:]):
        cum.append(cum[-1] + dist_m(a, b))
    total = cum[-1]
    out, j = [], 0
    for i in range(n):
        target = total * i / (n - 1)
        while j < len(cum) - 2 and cum[j + 1] < target:
            j += 1
        seg = cum[j + 1] - cum[j] or 1
        f = (target - cum[j]) / seg
        out.append((lerp(path[j][0], path[j + 1][0], f), lerp(path[j][1], path[j + 1][1], f)))
    return out


def trail_up() -> list[tuple[float, float]]:
    ways = {
        e["id"]: [(g["lat"], g["lon"]) for g in e["geometry"]]
        for e in json.loads((ROOT / "fortpacks/osm/sinhagad-trail.json").read_text(encoding="utf-8"))["elements"]
    }
    # 856781949 starts at the trailhead, 856781948 ends at Pune Gate.
    lower, upper = ways[856781949], ways[856781948]
    if dist_m(lower[-1], upper[0]) > dist_m(lower[0], upper[0]):
        lower = lower[::-1]
    if dist_m(upper[-1], lower[-1]) < dist_m(upper[0], lower[-1]):
        upper = upper[::-1]
    return lower + upper[1:]


def landmark(pack: dict, lid: str) -> tuple[float, float]:
    lm = next(l for l in pack["landmarks"] if l["id"] == lid)
    return (lm["lat"], lm["lon"])


def build_track() -> list[tuple[datetime, float, float, float]]:
    pack = json.loads((ROOT / "fortpacks/sinhagad.json").read_text(encoding="utf-8"))
    up = trail_up()
    top = [landmark(pack, "pune-darwaja"), landmark(pack, "sinhagad-fort"), landmark(pack, "kalyan-darwaja"),
           landmark(pack, "sinhagad-fort"), landmark(pack, "pune-darwaja")]

    start = datetime(2026, 10, 10, 6, 5, tzinfo=IST)
    legs = [
        # (path, minutes, start ele, end ele)  -- elevations are invented
        (up, 60, 720.0, 1235.0),
        (top, 85, 1235.0, 1235.0),
        (up[::-1], 55, 1235.0, 720.0),
    ]
    pts: list[tuple[datetime, float, float, float]] = []
    t = start
    for path, minutes, e0, e1 in legs:
        n = minutes * 60 // STEP_S
        for i, (lat, lon) in enumerate(resample(path, n)):
            f = i / (n - 1)
            if e0 == e1:
                # Up and down a little on the plateau.
                ele = e0 + 40 * math.sin(math.pi * f) + rng.uniform(-1.5, 1.5)
            else:
                # Steeper in the middle, like the real climb.
                s = (1 - math.cos(math.pi * f)) / 2
                ele = lerp(e0, e1, s) + rng.uniform(-1.5, 1.5)
            jitter = 0.000025
            pts.append((t, lat + rng.uniform(-jitter, jitter), lon + rng.uniform(-jitter, jitter), ele))
            t += timedelta(seconds=STEP_S)
    return pts


def write_gpx(pts) -> None:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="walkworth make_sample_fixture.py" xmlns="http://www.topografix.com/GPX/1/1">',
        "  <metadata>",
        "    <name>Sinhagad (SAMPLE)</name>",
        "    <desc>SYNTHETIC SAMPLE. Path follows OpenStreetMap trail ways (ODbL); times and elevations are invented for testing.</desc>",
        "  </metadata>",
        "  <trk><name>Sinhagad (SAMPLE)</name><trkseg>",
    ]
    for t, lat, lon, ele in pts:
        z = t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines.append(f'    <trkpt lat="{lat:.7f}" lon="{lon:.7f}"><ele>{ele:.1f}</ele><time>{z}</time></trkpt>')
    lines += ["  </trkseg></trk>", "</gpx>", ""]
    (OUT / "track.gpx").write_text("\n".join(lines), encoding="utf-8")


# Fiction, for testing only.
NOTES = [
    ("06:12", 6.1, "Started from Atkarwadi, still dark, chai stall just opening."),
    ("06:31", 7.4, "Steep bit now, loose stones, had to use my hands on a couple of rocks."),
    ("06:48", 5.2, "Sun's up behind us. The whole valley went orange for about a minute."),
    ("07:05", 6.8, "Langurs on the wall near the gate, three of them, totally unbothered."),
    ("07:22", 6.0, "Someone selling taak in clay pots just inside the gate. Ice cold."),
    ("07:48", 5.5, "Fog rolling in from the valley, can't see the road anymore."),
    ("08:10", 3.1, "Kanda bhaji at the top, earned it."),
    ("09:21", 5.9, "Back at the bottom. Knees are done, fog on our shoes."),
]
# This one simulates a GPS timeout on the trail.
NO_FIX = {"07:22"}

# Openly licensed photos from Wikimedia Commons, fetched by
# scripts/fetch_sample_photos.py. Credited on every print; never the user's own.
PHOTOS = [
    ("06:35", "ridge-trail.jpg"),
    ("06:49", "sunrise.jpg"),
    ("07:06", "gate.jpg"),
    ("07:51", "fog-edge.jpg"),
    ("08:05", "valley.jpg"),
    ("08:14", "cannon.jpg"),
]


def audio_seconds(path: Path) -> float:
    import av

    with av.open(str(path)) as c:
        stream = c.streams.audio[0]
        n = sum(f.samples for f in c.decode(stream))
        return round(n / stream.rate, 2)


def nearest(pts, t: datetime):
    return min(pts, key=lambda p: abs((p[0] - t).total_seconds()))


def at(hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 10, 10, h, m, tzinfo=IST)


def main() -> None:
    (OUT / "photos").mkdir(parents=True, exist_ok=True)
    pts = build_track()
    write_gpx(pts)

    notes = []
    for i, (hhmm, dur, text) in enumerate(NOTES, 1):
        t = at(hhmm)
        _, lat, lon, _ = nearest(pts, t)
        fix = hhmm not in NO_FIX
        audio = OUT / "audio" / f"note-{i:02d}.ogg"
        notes.append({
            "id": f"note-{i:02d}",
            "t": t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "lat": round(lat, 6) if fix else None,
            "lon": round(lon, 6) if fix else None,
            "duration_s": audio_seconds(audio) if audio.is_file() else dur,
            # Synthetic speech of `script` (scripts/make_sample_audio.py).
            # ElevenLabs transcribes it in the pipeline.
            "audio": f"audio/{audio.name}" if audio.is_file() else None,
            "script": text,
        })

    credits = {c["file"]: c for c in json.loads((OUT / "photos" / "credits.json").read_text(encoding="utf-8"))}
    photos = []
    for i, (hhmm, name) in enumerate(PHOTOS, 1):
        t = at(hhmm)
        _, lat, lon, _ = nearest(pts, t)
        c = credits[name]
        photos.append({
            "id": f"photo-{i:02d}",
            "t": t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "path": f"photos/{name}",
            "label": c["label"],
            "credit": f"{c['artist']}, {c['licence']}, via Wikimedia Commons",
        })

    manifest = {
        "_comment": "SAMPLE DATA. Voice notes are a computer voice reading invented lines; photos are credited Wikimedia Commons images, not the user's. Replaced by a real trek.",
        "trek": {"id": "sinhagad-sample", "title": "Sinhagad", "title_mr": "सिंहगड", "fort_pack_id": "sinhagad",
                 "screen_seconds": None},
        "notes": notes,
        "photos": photos,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(pts)} track points, {len(notes)} notes, {len(photos)} photos to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
