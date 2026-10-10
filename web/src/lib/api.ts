import type {
  Entry, FortPack, GeneratedLine, ImportResult, Photo, ProcessStatus, TrackPoint, Trek, TrekSummary, VoiceNote,
} from "../types/schema";

export interface Health {
  ok: boolean;
  models: { llm: string; vision: string; stt: string };
  keys: { llm_key: boolean; elevenlabs_key: boolean; calls_today: { used: number; limit: number } };
  /** True when starting a walk or an import needs the site's access code. */
  access_code: boolean;
}

export interface ExportOut {
  kind: string;
  name: string;
  bytes: number;
  url: string;
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

/** Shown whenever the server can't be reached at all. */
export const OFFLINE = "Walkworth's server didn't answer. Check your connection and try again in a minute.";

/** Any error as a sentence for the screen. */
export const errorText = (e: unknown) => (e instanceof ApiError ? e.message : OFFLINE);

// ---- Access code (only when the site sets ACCESS_CODE) ------------------------

const CODE_KEY = "ww-access-code";

export function accessCode(): string {
  try {
    return localStorage.getItem(CODE_KEY) ?? "";
  } catch {
    return "";
  }
}

export function setAccessCode(code: string) {
  try {
    if (code) localStorage.setItem(CODE_KEY, code);
    else localStorage.removeItem(CODE_KEY);
  } catch {
    // Private mode: the code is asked for again next time.
  }
}

let health: Promise<Health> | null = null;

/** Health, fetched once per page load. */
export function siteHealth(): Promise<Health> {
  health ??= call<Health>("/api/health").catch((e) => {
    health = null;
    throw e;
  });
  return health;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers: { Accept: "application/json", ...(init?.headers ?? {}) } });
  } catch {
    throw new ApiError(OFFLINE, 0);
  }
  if (!res.ok) {
    let detail = `${path} answered ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // Not JSON: keep the generic message.
    }
    throw new ApiError(detail, res.status);
  }
  const type = res.headers.get("content-type") ?? "";
  return (type.includes("json") ? res.json() : res.text()) as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

const t = (id: string) => `/api/treks/${encodeURIComponent(id)}`;

export const api = {
  health: () => call<Health>("/api/health"),
  fortPacks: () => call<FortPack[]>("/api/fortpacks"),
  treks: () => call<TrekSummary[]>("/api/treks"),
  trek: (id: string) => call<TrekSummary>(t(id)),
  createTrek: (title: string, fortPackId: string | null) => {
    const init = json("POST", { title, fort_pack_id: fortPackId });
    const code = accessCode();
    if (code) init.headers = { ...init.headers, "X-Access-Code": code };
    return call<Trek>("/api/treks", init);
  },
  deleteTrek: (id: string) => call<unknown>(t(id), { method: "DELETE" }),
  upload: (id: string, file: File) => {
    const form = new FormData();
    form.append("files", file, file.name);
    form.append("last_modified", JSON.stringify([file.lastModified]));
    return call<ImportResult[]>(`${t(id)}/files`, { method: "POST", body: form });
  },
  capture: (id: string, kind: "note" | "photo", file: Blob, name: string, at: number, lat: number | null, lon: number | null, durationS?: number) => {
    const form = new FormData();
    form.append("file", file, name);
    form.append("kind", kind);
    form.append("t", new Date(at).toISOString());
    if (lat != null && lon != null) {
      form.append("lat", String(lat));
      form.append("lon", String(lon));
    }
    if (durationS != null) form.append("duration_s", String(Math.round(durationS * 10) / 10));
    return call<ImportResult>(`${t(id)}/capture`, { method: "POST", body: form });
  },
  finish: (id: string, screenSeconds: number) => call<Trek>(`${t(id)}/finish`, json("POST", { screen_seconds: Math.round(screenSeconds) })),
  track: (id: string) => call<TrackPoint[]>(`${t(id)}/track`),
  notes: (id: string) => call<VoiceNote[]>(`${t(id)}/notes`),
  photos: (id: string) => call<Photo[]>(`${t(id)}/photos`),
  entries: (id: string) => call<Entry[]>(`${t(id)}/entries`),
  lines: (id: string) => call<GeneratedLine[]>(`${t(id)}/lines`),
  setOffset: (id: string, seconds: number) => call<Trek>(`${t(id)}/time-offset`, json("PUT", { seconds })),
  process: (id: string) => call<ProcessStatus>(`${t(id)}/process`, { method: "POST" }),
  processStatus: (id: string) => call<ProcessStatus>(`${t(id)}/process`),
  recompose: (id: string) => call<ProcessStatus>(`${t(id)}/recompose`, { method: "POST" }),
  recomposeStatus: (id: string) => call<ProcessStatus>(`${t(id)}/recompose`),
  editNote: (noteId: string, transcript: string) => call<VoiceNote>(`/api/notes/${encodeURIComponent(noteId)}`, json("PATCH", { transcript })),
  deleteNote: (noteId: string) => call<unknown>(`/api/notes/${encodeURIComponent(noteId)}`, { method: "DELETE" }),
  editPhoto: (photoId: string, body: { pinned_note_id?: string | null; caption?: string }) =>
    call<Photo>(`/api/photos/${encodeURIComponent(photoId)}`, json("PATCH", body)),
  editLine: (id: string, role: string, chapter: string | null, text: string) =>
    call<GeneratedLine | null>(`${t(id)}/lines`, json("PUT", { role, chapter, text })),
  buildExport: (id: string, kind: string, trim: boolean) => call<ExportOut>(`${t(id)}/exports/${kind}?trim=${trim}`, { method: "POST" }),
  devMarkdown: (id: string) => call<string>(`${t(id)}/dev-markdown`),
};

export const fileUrl = (path: string) => `/files/${path.split("/").map(encodeURIComponent).join("/")}`;
