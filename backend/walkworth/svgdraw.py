"""SVG drawings for the journal, generated from the trek's own data.

Colours are CSS custom properties so one drawing works in light, dark and
print. Coordinates are rounded to 0.1 to keep the markup small.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

XY = tuple[float, float]


def f(n: float) -> str:
    return f"{n:.1f}".rstrip("0").rstrip(".")


@dataclass
class Projection:
    """Equirectangular, north up, fitted into w x h with padding."""

    min_x: float
    min_y: float
    scale: float
    off_x: float
    off_y: float
    cos0: float

    @classmethod
    def fit(cls, latlons: list[tuple[float, float]], w: float, h: float, pad: float) -> "Projection":
        lat0 = math.radians(sum(p[0] for p in latlons) / len(latlons))
        cos0 = math.cos(lat0)
        xs = [lon * cos0 for _, lon in latlons]
        ys = [-lat for lat, _ in latlons]
        span_x = (max(xs) - min(xs)) or 1e-9
        span_y = (max(ys) - min(ys)) or 1e-9
        scale = min((w - 2 * pad) / span_x, (h - 2 * pad) / span_y)
        return cls(min(xs), min(ys), scale, (w - span_x * scale) / 2, (h - span_y * scale) / 2, cos0)

    def __call__(self, lat: float, lon: float) -> XY:
        return (self.off_x + (lon * self.cos0 - self.min_x) * self.scale,
                self.off_y + (-lat - self.min_y) * self.scale)


def polyline(pts: list[XY]) -> str:
    return "".join(f"{'L' if i else 'M'}{f(x)},{f(y)}" for i, (x, y) in enumerate(pts))


def smooth(pts: list[XY]) -> str:
    """Catmull-Rom through the points, as cubic Béziers."""
    if len(pts) < 2:
        return ""
    d = f"M{f(pts[0][0])},{f(pts[0][1])}"
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        d += f" C{f(c1[0])},{f(c1[1])} {f(c2[0])},{f(c2[1])} {f(p2[0])},{f(p2[1])}"
    return d


def contours(eles: list[float], lines: int = 6, w: float = 1000, gap: float = 34, amp: float = 120) -> str:
    """The walk's elevation profile, offset downward: contour lines that come
    from this trek, not from nowhere."""
    if len(eles) < 4:
        return ""
    n = 14
    prof = [eles[round(i / (n - 1) * (len(eles) - 1))] for i in range(n)]
    lo, hi = min(prof), max(prof)
    norm = [(e - lo) / ((hi - lo) or 1) for e in prof]
    paths = []
    for k in range(lines):
        a = amp * (1 - k * 0.09)
        pts = [(i / (n - 1) * w, 40 + k * gap + (1 - v) * a) for i, v in enumerate(norm)]
        paths.append(f'<path d="{smooth(pts)}" vector-effect="non-scaling-stroke"/>')
    height = 40 + lines * gap + amp
    return (f'<svg class="contours" viewBox="0 0 {f(w)} {f(height)}" preserveAspectRatio="none" aria-hidden="true">'
            f'<g fill="none" stroke="var(--contour)" stroke-width="1.25">{"".join(paths)}</g></svg>')


@dataclass
class Profile:
    """Elevation over time, drawn as an ink strip with a mist fill."""

    times: list[datetime]
    eles: list[float]
    w: float = 600
    h: float = 64
    pad: float = 4

    def __post_init__(self) -> None:
        self.t0, self.t1 = self.times[0], self.times[-1]
        self.lo, self.hi = min(self.eles), max(self.eles)

    def x(self, t: datetime) -> float:
        span = (self.t1 - self.t0).total_seconds() or 1
        frac = min(1.0, max(0.0, (t - self.t0).total_seconds() / span))
        return self.pad + frac * (self.w - 2 * self.pad)

    def y(self, ele: float) -> float:
        rng = (self.hi - self.lo) or 1
        return self.h - self.pad - (ele - self.lo) / rng * (self.h - 2 * self.pad)

    def points(self) -> list[XY]:
        step = max(1, len(self.times) // 200)
        return [(self.x(t), self.y(e)) for t, e in list(zip(self.times, self.eles))[::step]]

    def pct(self, t: datetime, ele: float) -> tuple[float, float]:
        """Position as percentages, for an HTML dot over the stretched SVG."""
        return round(self.x(t) / self.w * 100, 2), round(self.y(ele) / self.h * 100, 2)

    def svg(self, highlight: tuple[datetime, datetime] | None = None, cls: str = "strip") -> str:
        pts = self.points()
        line = polyline(pts)
        area = f"{line}L{f(pts[-1][0])},{f(self.h)}L{f(pts[0][0])},{f(self.h)}Z"
        hl = ""
        if highlight:
            x0, x1 = self.x(highlight[0]), self.x(highlight[1])
            hl = (f'<clipPath id="c{abs(hash(highlight)) % 10**8}"><rect x="{f(x0)}" y="0" width="{f(max(x1 - x0, 2))}" height="{f(self.h)}"/></clipPath>'
                  f'<path d="{area}" fill="var(--strip-hl)" clip-path="url(#c{abs(hash(highlight)) % 10**8})"/>')
        return (f'<svg class="{cls}" viewBox="0 0 {f(self.w)} {f(self.h)}" preserveAspectRatio="none" aria-hidden="true">'
                f'<path d="{area}" fill="var(--mist)"/>{hl}'
                f'<path d="{line}" fill="none" stroke="var(--ink)" stroke-width="1.25" vector-effect="non-scaling-stroke"/>'
                "</svg>")
