import type { RoutePoint } from "../types/schema";
import { raw, type Raw } from "./html";

type XY = [number, number];

/** Equirectangular projection, scaled to fit a box with padding, north up. */
function project(route: RoutePoint[], w: number, h: number, pad: number): XY[] {
  const lat0 = (route.reduce((s, p) => s + p.lat, 0) / route.length) * (Math.PI / 180);
  const pts = route.map((p) => [p.lon * Math.cos(lat0), -p.lat] as XY);
  const xs = pts.map((p) => p[0]);
  const ys = pts.map((p) => p[1]);
  const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const scale = Math.min((w - 2 * pad) / (maxX - minX || 1), (h - 2 * pad) / (maxY - minY || 1));
  const offX = (w - (maxX - minX) * scale) / 2;
  const offY = (h - (maxY - minY) * scale) / 2;
  return pts.map(([x, y]) => [offX + (x - minX) * scale, offY + (y - minY) * scale]);
}

const f = (n: number) => n.toFixed(1);

/** Catmull-Rom through the points, as cubic Béziers. */
function smoothPath(pts: XY[]): string {
  if (pts.length < 2) return "";
  let d = `M${f(pts[0][0])},${f(pts[0][1])}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] ?? pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] ?? p2;
    const c1: XY = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6];
    const c2: XY = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6];
    d += ` C${f(c1[0])},${f(c1[1])} ${f(c2[0])},${f(c2[1])} ${f(p2[0])},${f(p2[1])}`;
  }
  return d;
}

/** Small route drawing for a shelf row: start in ink, high point in laterite. */
export function routeThumb(route: RoutePoint[], size = 56): Raw {
  if (route.length < 2) return raw("");
  const pts = project(route, size, size, 5);
  const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${f(x)},${f(y)}`).join("");
  let hi = 0;
  route.forEach((p, i) => {
    if ((p.ele_m ?? -Infinity) > (route[hi].ele_m ?? -Infinity)) hi = i;
  });
  const [sx, sy] = pts[0];
  const [hx, hy] = pts[hi];
  return raw(`<svg class="route-thumb" viewBox="0 0 ${size} ${size}" width="${size}" height="${size}" aria-hidden="true">
    <path d="${d}" fill="none" stroke="var(--ink)" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${f(sx)}" cy="${f(sy)}" r="3.5" fill="var(--ink)"/>
    <circle cx="${f(hx)}" cy="${f(hy)}" r="4.5" fill="var(--laterite)"/>
  </svg>`);
}

/**
 * Contour lines drawn from a route's elevation profile: the profile of the
 * walk, offset downward a few times. Not real terrain, by design.
 */
export function contours(route: RoutePoint[], lines = 6): Raw {
  const eles = route.map((p) => p.ele_m).filter((e): e is number => e != null);
  if (eles.length < 4) return raw("");
  const samples = 14;
  const prof = Array.from({ length: samples }, (_, i) => eles[Math.round((i / (samples - 1)) * (eles.length - 1))]);
  const lo = Math.min(...prof);
  const hi = Math.max(...prof);
  const norm = prof.map((e) => (e - lo) / (hi - lo || 1));
  const W = 1000;
  const gap = 34;
  const amp = 120;
  const paths = Array.from({ length: lines }, (_, k) => {
    const a = amp * (1 - k * 0.09);
    const pts = norm.map((n, i) => [(i / (samples - 1)) * W, 40 + k * gap + (1 - n) * a] as XY);
    return `<path d="${smoothPath(pts)}" vector-effect="non-scaling-stroke"/>`;
  }).join("");
  return raw(`<svg class="contours" viewBox="0 0 ${W} ${40 + lines * gap + amp}" preserveAspectRatio="none" aria-hidden="true">
    <g fill="none" stroke="var(--contour)" stroke-width="1.25">${paths}</g>
  </svg>`);
}

/** A bigger route drawing for the home page: the walk as a line, start and
 * high point marked. Drawn from the real GPX, north up. */
export function routeMap(route: RoutePoint[], w = 320, h = 240): Raw {
  if (route.length < 2) return raw("");
  const pts = project(route, w, h, 18);
  let hi = 0;
  route.forEach((p, i) => {
    if ((p.ele_m ?? -Infinity) > (route[hi].ele_m ?? -Infinity)) hi = i;
  });
  const [sx, sy] = pts[0];
  const [hx, hy] = pts[hi];
  return raw(`<svg class="route-map" viewBox="0 0 ${w} ${h}" role="img" aria-label="The Sinhagad route, drawn from the sample GPX, north up">
    <path d="${smoothPath(pts)}" fill="none" stroke="var(--monsoon)" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>
    <circle cx="${f(sx)}" cy="${f(sy)}" r="6" fill="var(--paper)" stroke="var(--ink)" stroke-width="2.5"/>
    <circle cx="${f(hx)}" cy="${f(hy)}" r="7" fill="var(--accent)" stroke="var(--ink)" stroke-width="2"/>
  </svg>`);
}
