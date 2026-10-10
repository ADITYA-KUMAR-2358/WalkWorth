const DISPLAY_TZ = "Asia/Kolkata";

export function shortDate(iso: string, tz = DISPLAY_TZ): string {
  return new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: tz })
    .format(new Date(iso))
    .replace(",", "");
}

export function km(metres: number): string {
  return `${(metres / 1000).toFixed(1)} km`;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** "just now", "12 min ago", "3 h ago", "2 days ago". */
export function ago(ms: number, now = Date.now()): string {
  const min = Math.round((now - ms) / 60000);
  if (min < 1) return "just now";
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  if (h < 36) return `${h} h ago`;
  return plural(Math.round(h / 24), "day") + " ago";
}

/** Whole metres, rounded half to even like the server's Python, so the home
 * page and the journal show the same number (554.5 m is "554 m" on both). */
export function metres(m: number): string {
  const r = Math.round(m);
  const even = Math.abs(m % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r;
  return `${even} m`;
}

/** A walk's length: "under a minute", "48 min", "3 h 20 min". */
export function walkLength(ms: number): string {
  const min = Math.round(ms / 60000);
  if (min < 1) return "under a minute";
  if (min < 60) return `${min} min`;
  return `${Math.floor(min / 60)} h ${min % 60} min`;
}
