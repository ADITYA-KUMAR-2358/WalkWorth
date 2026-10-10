// IndexedDB for Trail mode: the upload buffer. A note is written here while it
// records (so a killed app loses at most a second), then uploaded to the
// server; anything that fails to upload waits here and is retried.

export interface Walk {
  id: string;
  title: string;
  titleMr: string | null;
  fortPackId: string | null;
  startedAt: number;
  endedAt: number | null;
  screenSeconds: number;
  /** The trek on the server; created when the walk starts. */
  trekId: string;
}

export interface Note {
  id: string;
  walkId: string;
  t: number;
  lat: number | null;
  lon: number | null;
  accuracy: number | null;
  durationS: number;
  mime: string;
  status: "recording" | "done" | "recovered";
  blob: Blob | null;
  uploaded?: boolean;
}

export interface Chunk {
  key: string; // `${noteId}:${seq}`
  noteId: string;
  seq: number;
  blob: Blob;
}

export interface PhotoRec {
  id: string;
  walkId: string;
  t: number;
  lat: number | null;
  lon: number | null;
  mime: string;
  name: string;
  blob: Blob;
  uploaded?: boolean;
}

const DB = "walkworth-trail";
const VERSION = 1;
let opening: Promise<IDBDatabase> | null = null;

function open(): Promise<IDBDatabase> {
  opening ??= new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      db.createObjectStore("walks", { keyPath: "id" });
      db.createObjectStore("notes", { keyPath: "id" }).createIndex("walkId", "walkId");
      db.createObjectStore("chunks", { keyPath: "key" }).createIndex("noteId", "noteId");
      db.createObjectStore("photos", { keyPath: "id" }).createIndex("walkId", "walkId");
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
  return opening;
}

type Store = "walks" | "notes" | "chunks" | "photos";

function done<T>(req: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function store(name: Store, mode: IDBTransactionMode = "readonly") {
  return (await open()).transaction(name, mode).objectStore(name);
}

export const idb = {
  async put<T>(name: Store, value: T): Promise<void> {
    await done((await store(name, "readwrite")).put(value));
  },
  async get<T>(name: Store, key: string): Promise<T | undefined> {
    return done((await store(name)).get(key)) as Promise<T | undefined>;
  },
  async all<T>(name: Store): Promise<T[]> {
    return done((await store(name)).getAll()) as Promise<T[]>;
  },
  async byIndex<T>(name: Store, index: string, key: string): Promise<T[]> {
    return done((await store(name)).index(index).getAll(key)) as Promise<T[]>;
  },
  async delete(name: Store, key: string): Promise<void> {
    await done((await store(name, "readwrite")).delete(key));
  },
};

export function newId(prefix: string): string {
  const rand = crypto.getRandomValues(new Uint32Array(2));
  return `${prefix}-${Date.now().toString(36)}-${rand[0].toString(36)}${rand[1].toString(36).slice(0, 3)}`;
}
