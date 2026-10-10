"""GPX parsing and trek stats, standard library only."""

from __future__ import annotations

import bisect
import math
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from .schema import RoutePoint, TrackPoint, TrekStats

NS = {"g": "http://www.topografix.com/GPX/1/1"}
EARTH_R_M = 6_371_008.8

# A segment slower than this counts as stopped when computing moving time.
MOVING_MIN_MPS = 0.3
# Ignore elevation wiggles smaller than this when summing gain (GPS noise).
GAIN_HYSTERESIS_M = 3.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(math.sqrt(a))


def _parse_time(text: str) -> datetime:
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return t.astimezone(timezone.utc) if t.tzinfo else t.replace(tzinfo=timezone.utc)


def read_track(source: Path | bytes, trek_id: str) -> list[TrackPoint]:
    root = ET.fromstring(source) if isinstance(source, bytes) else ET.parse(source).getroot()
    # Accept GPX 1.0 files too by matching any namespace.
    if root.tag.startswith("{") and not root.tag.startswith("{" + NS["g"]):
        ns = root.tag[1:].split("}")[0]
        return _read(root, trek_id, {"g": ns})
    return _read(root, trek_id, NS)


def _read(root: ET.Element, trek_id: str, ns: dict[str, str]) -> list[TrackPoint]:
    NS = ns  # noqa: N806
    points: list[TrackPoint] = []
    for pt in root.iterfind(".//g:trkpt", NS):
        time_el = pt.find("g:time", NS)
        if time_el is None or not time_el.text:
            continue
        ele_el = pt.find("g:ele", NS)
        points.append(
            TrackPoint(
                trek_id=trek_id,
                t=_parse_time(time_el.text),
                lat=float(pt.attrib["lat"]),
                lon=float(pt.attrib["lon"]),
                ele_m=float(ele_el.text) if ele_el is not None and ele_el.text else None,
            )
        )
    points.sort(key=lambda p: p.t)
    return points


def downsample(points: list[TrackPoint], every_s: float = 5.0) -> list[TrackPoint]:
    """Keep at most one point per `every_s` seconds, always keeping the last."""
    if not points:
        return []
    out = [points[0]]
    for p in points[1:]:
        if (p.t - out[-1].t).total_seconds() >= every_s:
            out.append(p)
    if out[-1] is not points[-1]:
        out.append(points[-1])
    return out


def stats(points: list[TrackPoint]) -> TrekStats | None:
    if len(points) < 2:
        return None
    distance = 0.0
    moving = 0.0
    for a, b in zip(points, points[1:]):
        d = haversine_m(a.lat, a.lon, b.lat, b.lon)
        dt = (b.t - a.t).total_seconds()
        distance += d
        if dt > 0 and d / dt >= MOVING_MIN_MPS:
            moving += dt

    gain = 0.0
    eles = [p.ele_m for p in points if p.ele_m is not None]
    if eles:
        ref = eles[0]
        for e in eles[1:]:
            if e - ref >= GAIN_HYSTERESIS_M:
                gain += e - ref
                ref = e
            elif ref - e >= GAIN_HYSTERESIS_M:
                ref = e

    return TrekStats(
        distance_m=round(distance, 1),
        gain_m=round(gain, 1),
        high_point_m=round(max(eles), 1) if eles else None,
        total_s=int((points[-1].t - points[0].t).total_seconds()),
        moving_s=int(moving),
    )


def nearest_in_time(points: list[TrackPoint], t: datetime, max_gap_s: float = 900) -> TrackPoint | None:
    """The track point closest in time to `t`, if one is within `max_gap_s`."""
    if not points:
        return None
    times = [p.t for p in points]
    i = bisect.bisect_left(times, t)
    candidates = [points[j] for j in (i - 1, i) if 0 <= j < len(points)]
    best = min(candidates, key=lambda p: abs((p.t - t).total_seconds()))
    return best if abs((best.t - t).total_seconds()) <= max_gap_s else None


def trim_ends(points: list[TrackPoint], metres: float) -> list[TrackPoint]:
    """Drop the first and last `metres` of the route (privacy before publishing)."""
    if metres <= 0 or len(points) < 3:
        return points
    cum = [0.0]
    for a, b in zip(points, points[1:]):
        cum.append(cum[-1] + haversine_m(a.lat, a.lon, b.lat, b.lon))
    total = cum[-1]
    if total <= 2 * metres:
        return points
    return [p for p, d in zip(points, cum) if metres <= d <= total - metres]


def route_fractions(points: list[TrackPoint]) -> list[float]:
    """Cumulative distance along the route, 0..1, per point."""
    if len(points) < 2:
        return [0.0] * len(points)
    cum = [0.0]
    for a, b in zip(points, points[1:]):
        cum.append(cum[-1] + haversine_m(a.lat, a.lon, b.lat, b.lon))
    total = cum[-1] or 1.0
    return [c / total for c in cum]


def write_gpx(points: list[TrackPoint], name: str) -> str:
    from xml.sax.saxutils import escape

    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="Walkworth" xmlns="http://www.topografix.com/GPX/1/1">',
        f"  <trk><name>{escape(name)}</name><trkseg>",
    ]
    for p in points:
        ele = f"<ele>{p.ele_m:.1f}</ele>" if p.ele_m is not None else ""
        z = p.t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        lines.append(f'    <trkpt lat="{p.lat:.6f}" lon="{p.lon:.6f}">{ele}<time>{z}</time></trkpt>')
    lines += ["  </trkseg></trk>", "</gpx>", ""]
    return "\n".join(lines)


def thumbnail_route(points: list[TrackPoint], max_points: int = 64) -> list[RoutePoint]:
    if not points:
        return []
    step = max(1, math.ceil(len(points) / max_points))
    picked = points[::step]
    if picked[-1] is not points[-1]:
        picked.append(points[-1])
    return [RoutePoint(lat=p.lat, lon=p.lon, ele_m=p.ele_m) for p in picked]
