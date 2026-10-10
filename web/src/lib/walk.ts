// Trail-mode logic: walks, voice notes, photos, crash recovery and upload.
// The walk is a trek on the server from the first tap. Each note and photo is
// uploaded as soon as it's saved; IndexedDB only holds it until the server
// has it, so a dropped connection or a killed app loses nothing.

import { api, ApiError } from "./api";
import { idb, newId, type Chunk, type Note, type PhotoRec, type Walk } from "./idb";

export const MAX_NOTE_S = 90;
const CURRENT = "ww-current-walk";

export function currentWalkId(): string | null {
  try {
    return localStorage.getItem(CURRENT);
  } catch {
    return null;
  }
}

/** Drop a pointer to a walk this browser no longer has. */
export const forgetCurrentWalk = () => setCurrent(null);

function setCurrent(id: string | null) {
  try {
    if (id) localStorage.setItem(CURRENT, id);
    else localStorage.removeItem(CURRENT);
  } catch {
    // Private mode: the walk still records, it just won't resume after a reload.
  }
}

/** Creates the trek on the server, then the local walk that feeds it. */
export async function startWalk(title: string, titleMr: string | null, fortPackId: string | null): Promise<Walk> {
  const trek = await api.createTrek(title, fortPackId);
  const walk: Walk = {
    id: newId("walk"), title: trek.title, titleMr: trek.title_mr ?? titleMr, fortPackId, startedAt: Date.now(),
    endedAt: null, screenSeconds: 0, trekId: trek.id,
  };
  await idb.put("walks", walk);
  setCurrent(walk.id);
  navigator.storage?.persist?.().catch(() => undefined);
  return walk;
}

export async function endWalk(walk: Walk): Promise<Walk> {
  const ended = { ...walk, endedAt: Date.now() };
  await idb.put("walks", ended);
  setCurrent(null);
  return ended;
}

// ---- Upload -------------------------------------------------------------------

const EXT: Record<string, string> = { "audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "image/jpeg": "jpg", "image/heic": "heic", "image/png": "png" };
const ext = (mime: string) => EXT[mime.split(";")[0]] ?? "bin";

let syncing: Promise<number> | null = null;

class TrekGone extends Error {}

const gone = (e: unknown) => e instanceof ApiError && e.status === 404;

async function uploadPending(walk: Walk): Promise<number> {
  let waiting = 0;
  for (const n of await idb.byIndex<Note>("notes", "walkId", walk.id)) {
    if (n.uploaded || !n.blob || n.status === "recording") continue;
    try {
      await api.capture(walk.trekId, "note", n.blob, `${n.id}.${ext(n.mime)}`, n.t, n.lat, n.lon, n.durationS);
      await idb.put("notes", { ...((await idb.get<Note>("notes", n.id)) ?? n), uploaded: true });
    } catch (e) {
      if (gone(e)) throw new TrekGone();
      waiting++;
    }
  }
  for (const p of await idb.byIndex<PhotoRec>("photos", "walkId", walk.id)) {
    if (p.uploaded) continue;
    try {
      await api.capture(walk.trekId, "photo", p.blob, p.name || `${p.id}.${ext(p.mime)}`, p.t, p.lat, p.lon);
      await idb.put("photos", { ...((await idb.get<PhotoRec>("photos", p.id)) ?? p), uploaded: true });
    } catch (e) {
      if (gone(e)) throw new TrekGone();
      waiting++;
    }
  }
  return waiting;
}

/** The server lost the trek (its disk is wiped on every deploy). Start a new
 * one and mark everything this phone still holds for upload again. */
async function restartTrek(walk: Walk): Promise<Walk> {
  const trek = await api.createTrek(walk.title, walk.fortPackId);
  const moved: Walk = { ...walk, trekId: trek.id };
  await idb.put("walks", moved);
  for (const n of await idb.byIndex<Note>("notes", "walkId", walk.id)) if (n.uploaded) await idb.put("notes", { ...n, uploaded: false });
  for (const p of await idb.byIndex<PhotoRec>("photos", "walkId", walk.id)) if (p.uploaded) await idb.put("photos", { ...p, uploaded: false });
  return moved;
}

/** Upload everything of this walk the server doesn't have yet. Returns how
 * many items are still waiting. Concurrent calls share one run. With
 * `check`, first make sure the server still has the trek at all. */
export function syncWalk(walkId: string, check = false): Promise<number> {
  syncing ??= (async () => {
    try {
      let walk = await getWalk(walkId);
      if (!walk) return 0;
      if (check) {
        try {
          await api.trek(walk.trekId);
        } catch (e) {
          if (gone(e)) walk = await restartTrek(walk);
        }
      }
      try {
        return await uploadPending(walk);
      } catch (e) {
        if (!(e instanceof TrekGone)) throw e;
        return await uploadPending(await restartTrek(walk));
      }
    } catch {
      // Offline, or the trek couldn't be started again: everything stays on the phone.
      return waitingUploads(walkId);
    } finally {
      syncing = null;
    }
  })();
  return syncing;
}

export async function waitingUploads(walkId: string): Promise<number> {
  const notes = (await idb.byIndex<Note>("notes", "walkId", walkId)).filter((n) => n.blob && n.status !== "recording" && !n.uploaded);
  const photos = (await idb.byIndex<PhotoRec>("photos", "walkId", walkId)).filter((p) => !p.uploaded);
  return notes.length + photos.length;
}

export const getWalk = (id: string) => idb.get<Walk>("walks", id);
export const walkNotes = async (id: string) =>
  (await idb.byIndex<Note>("notes", "walkId", id)).filter((n) => n.status !== "recording").sort((a, b) => a.t - b.t);
export const walkPhotos = async (id: string) => (await idb.byIndex<PhotoRec>("photos", "walkId", id)).sort((a, b) => a.t - b.t);

export async function allWalks(): Promise<Walk[]> {
  return (await idb.all<Walk>("walks")).sort((a, b) => b.startedAt - a.startedAt);
}

export async function deleteWalk(id: string): Promise<void> {
  for (const n of await idb.byIndex<Note>("notes", "walkId", id)) await idb.delete("notes", n.id);
  for (const p of await idb.byIndex<PhotoRec>("photos", "walkId", id)) await idb.delete("photos", p.id);
  await idb.delete("walks", id);
}

// ---- Location ---------------------------------------------------------------

export interface Fix {
  lat: number;
  lon: number;
  accuracy: number;
}

/** One GPS fix, 15 s timeout. Null on timeout or denial; the note is still saved. */
export function gpsFix(): Promise<Fix | null> {
  return new Promise((resolve) => {
    if (!("geolocation" in navigator)) return resolve(null);
    navigator.geolocation.getCurrentPosition(
      (p) => resolve({ lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy }),
      () => resolve(null),
      { enableHighAccuracy: true, timeout: 15_000, maximumAge: 30_000 },
    );
  });
}

export function buzz(pattern: number | number[]) {
  try {
    navigator.vibrate?.(pattern);
  } catch {
    // Some browsers (iOS) have no vibration; the screen still shows state.
  }
}

// ---- Recording ----------------------------------------------------------------

function pickMime(): string {
  for (const m of ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"]) {
    if (typeof MediaRecorder !== "undefined" && MediaRecorder.isTypeSupported(m)) return m;
  }
  return "";
}

export class MicDenied extends Error {}

export class NoteRecorder {
  private rec: MediaRecorder | null = null;
  private stream: MediaStream | null = null;
  private seq = 0;
  private startedAt = 0;
  private capTimer = 0;
  private fix: Promise<Fix | null> = Promise.resolve(null);
  note: Note | null = null;

  /** onSaved fires once a note is stored with its GPS fix (or none), ready to upload. */
  constructor(private walkId: string, private onAutoStop: () => void, private onSaved: (noteId: string) => void) {}

  get recording() {
    return this.rec?.state === "recording";
  }

  get elapsed() {
    return this.recording ? (Date.now() - this.startedAt) / 1000 : 0;
  }

  async start(): Promise<void> {
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { noiseSuppression: true, echoCancellation: true } });
    } catch (e) {
      if (e instanceof DOMException && (e.name === "NotAllowedError" || e.name === "SecurityError")) throw new MicDenied();
      throw e;
    }
    const mime = pickMime();
    this.rec = new MediaRecorder(this.stream, mime ? { mimeType: mime } : undefined);
    this.startedAt = Date.now();
    this.seq = 0;
    const note: Note = {
      id: newId("note"), walkId: this.walkId, t: this.startedAt, lat: null, lon: null, accuracy: null,
      durationS: 0, mime: this.rec.mimeType || mime || "audio/webm", status: "recording", blob: null,
    };
    this.note = note;
    await idb.put("notes", note);

    // Chunks land in IndexedDB every second, so a killed app loses at most one.
    this.rec.ondataavailable = (ev) => {
      if (ev.data.size) {
        const chunk: Chunk = { key: `${note.id}:${String(this.seq).padStart(4, "0")}`, noteId: note.id, seq: this.seq++, blob: ev.data };
        idb.put("chunks", chunk).catch(() => undefined);
      }
    };
    this.rec.start(1000);
    buzz(80);
    this.capTimer = window.setTimeout(() => {
      this.stop().then(this.onAutoStop);
    }, MAX_NOTE_S * 1000);

    this.fix = gpsFix();
  }

  async stop(): Promise<Note | null> {
    const rec = this.rec;
    const note = this.note;
    if (!rec || !note || rec.state !== "recording") return null;
    window.clearTimeout(this.capTimer);
    const stopped = new Promise<void>((r) => (rec.onstop = () => r()));
    rec.stop();
    await stopped;
    this.stream?.getTracks().forEach((t) => t.stop());
    buzz([40, 60, 40]);
    const saved = await finishNote(note.id, (Date.now() - this.startedAt) / 1000, "done");
    const fix = this.fix;
    this.rec = null;
    this.note = null;
    if (saved) {
      // Attach the fix to the stored note (not a stale copy), then hand it on for upload.
      fix.then(async (f) => {
        if (f) {
          const latest = await idb.get<Note>("notes", saved.id);
          if (latest) await idb.put("notes", { ...latest, lat: f.lat, lon: f.lon, accuracy: f.accuracy });
        }
        this.onSaved(saved.id);
      });
    }
    return saved;
  }
}

/** Assemble a note's chunks into one blob and drop the chunks. */
async function finishNote(noteId: string, durationS: number, status: Note["status"]): Promise<Note | null> {
  // Give the last ondataavailable a moment to land.
  await new Promise((r) => setTimeout(r, 150));
  const note = await idb.get<Note>("notes", noteId);
  if (!note) return null;
  const chunks = (await idb.byIndex<Chunk>("chunks", "noteId", noteId)).sort((a, b) => a.seq - b.seq);
  if (!chunks.length) {
    await idb.delete("notes", noteId);
    return null;
  }
  const blob = new Blob(chunks.map((c) => c.blob), { type: note.mime });
  const saved: Note = { ...note, blob, durationS: Math.min(MAX_NOTE_S, durationS), status };
  await idb.put("notes", saved);
  for (const c of chunks) await idb.delete("chunks", c.key);
  return saved;
}

/** After a crash: turn half-recorded notes back into notes. */
export async function recoverNotes(walkId: string): Promise<number> {
  const stuck = (await idb.byIndex<Note>("notes", "walkId", walkId)).filter((n) => n.status === "recording");
  let recovered = 0;
  for (const n of stuck) {
    const chunks = await idb.byIndex<Chunk>("chunks", "noteId", n.id);
    if (await finishNote(n.id, chunks.length, "recovered")) recovered++;
  }
  return recovered;
}

// ---- Photos ---------------------------------------------------------------------

export async function addPhoto(walkId: string, file: File): Promise<PhotoRec> {
  const t = Date.now();
  buzz(50);
  // The camera's own EXIF GPS wins on the server; this fix is the fallback.
  const fix = await gpsFix();
  const photo: PhotoRec = {
    id: newId("photo"), walkId, t, lat: fix?.lat ?? null, lon: fix?.lon ?? null,
    mime: file.type || "image/jpeg", name: file.name || "photo.jpg", blob: file,
  };
  await idb.put("photos", photo);
  return photo;
}

// ---- Storage ----------------------------------------------------------------------

export async function storageUse(): Promise<{ used: number; quota: number } | null> {
  const est = await navigator.storage?.estimate?.();
  return est?.quota ? { used: est.usage ?? 0, quota: est.quota } : null;
}
