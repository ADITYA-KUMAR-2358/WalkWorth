// Screen 3: Trail mode. One giant button. Tap, talk, tap; or hold to talk.
// Nothing here needs to be read mid-walk: haptics say what happened. Notes
// and photos upload as soon as they're saved.

import { api } from "../lib/api";
import { walkLength } from "../lib/format";
import { html } from "../lib/html";
import { icons } from "../lib/icons";
import type { Walk } from "../lib/idb";
import {
  MAX_NOTE_S, MicDenied, NoteRecorder, addPhoto, buzz, currentWalkId, endWalk, getWalk, recoverNotes, storageUse,
  syncWalk, waitingUploads, walkNotes, walkPhotos,
} from "../lib/walk";
import { idb } from "../lib/idb";
import "./trail.css";

const HOLD_MS = 650;

function clock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return `${h}:${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function micDeniedScreen(root: HTMLElement, retry: () => void) {
  root.innerHTML = html`<main class="trail trail--message">
    <h1>Walkworth can't hear you</h1>
    <p>The microphone is blocked for this site. Open your browser's site settings, allow the microphone, then come back.</p>
    <button class="trail-text-btn" type="button" id="retry">Try again</button>
  </main>`.value;
  root.querySelector("#retry")?.addEventListener("click", retry);
}

export async function trailScreen(root: HTMLElement) {
  document.title = "Trail mode · Walkworth";
  document.documentElement.classList.add("is-trail");
  const id = currentWalkId();
  let walk = id ? await getWalk(id) : undefined;
  if (!walk?.trekId) {
    location.replace("#/prepare");
    return;
  }
  const recovered = await recoverNotes(walk.id);
  if (recovered) buzz([30, 40, 30, 40, 30]);

  let notes = (await walkNotes(walk.id)).length;
  let photos = (await walkPhotos(walk.id)).length;
  let pressAt = 0;
  let releasedEarly = false;
  let startedByPress = false;
  let lowStorage = false;
  let waiting = await waitingUploads(walk.id);
  const sync = async () => {
    waiting = await syncWalk(walk!.id);
    paint();
  };

  // Screen-on time: the whole point is to keep it small.
  let visibleSince = document.visibilityState === "visible" ? Date.now() : 0;
  const saveScreenTime = async () => {
    if (!walk || !visibleSince) return;
    const add = (Date.now() - visibleSince) / 1000;
    visibleSince = document.visibilityState === "visible" ? Date.now() : 0;
    walk = { ...walk, screenSeconds: walk.screenSeconds + add };
    await idb.put("walks", walk);
  };
  const onVisibility = () => {
    if (document.visibilityState === "hidden") saveScreenTime();
    else visibleSince = Date.now();
  };
  document.addEventListener("visibilitychange", onVisibility);

  const recorder = new NoteRecorder(
    walk.id,
    () => {
      notes++;
      paint();
    },
    () => void sync(),
  );

  root.innerHTML = html`<main class="trail" aria-live="polite">
    <header class="trail-head mono">
      <span class="trail-place">${walk.title}</span>
      <span class="trail-clock" id="clock">${clock(Date.now() - walk.startedAt)}</span>
      <span id="counts"></span>
      <span id="uploads"></span>
    </header>
    <button class="talk" type="button" id="talk" aria-label="Tap to talk">
      <span class="talk-circle">
        <span class="talk-icon">${icons.microphone}</span>
        <span class="talk-label" id="talk-label">Tap to talk</span>
        <span class="talk-sub mono" id="talk-sub"></span>
      </span>
    </button>
    <label class="snap">
      ${icons.camera}
      <input class="file-input" type="file" accept="image/*" capture="environment" id="photo" aria-label="Take a photo">
    </label>
    <p class="trail-hint">Put me in your pocket.<br>I'll buzz when I'm listening.</p>
    <p class="trail-warning" id="warning" hidden></p>
    <button class="trail-text-btn" type="button" id="end">End walk</button>
  </main>`.value;

  const $ = <T extends HTMLElement>(sel: string) => root.querySelector<T>(sel)!;
  const talk = $<HTMLButtonElement>("#talk");

  function paint() {
    const rec = recorder.recording;
    talk.classList.toggle("is-recording", rec);
    talk.setAttribute("aria-label", rec ? "Recording. Tap to stop" : "Tap to talk");
    $("#talk-label").textContent = rec ? "Listening" : "Tap to talk";
    $("#talk-sub").textContent = rec ? `${Math.floor(recorder.elapsed)} s of ${MAX_NOTE_S}` : "";
    $("#counts").textContent = `${notes} ${notes === 1 ? "note" : "notes"} · ${photos} ${photos === 1 ? "photo" : "photos"}`;
    $("#uploads").textContent = waiting ? `${waiting} waiting to upload` : "all saved";
    $("#clock").textContent = clock(Date.now() - walk!.startedAt);
    const warn = $("#warning");
    warn.hidden = !lowStorage && !recovered;
    warn.textContent = lowStorage
      ? "Phone storage is 80% full. Notes waiting to upload may not fit."
      : recovered ? `Recovered ${recovered} unfinished ${recovered === 1 ? "note" : "notes"}.` : "";
  }

  async function start() {
    try {
      await recorder.start();
    } catch (e) {
      if (e instanceof MicDenied) return micDeniedScreen(root, () => trailScreen(root));
      throw e;
    }
    paint();
  }

  async function stop() {
    if (await recorder.stop()) notes++;
    paint();
  }

  talk.addEventListener("pointerdown", async (e) => {
    e.preventDefault();
    // The event's own clock: the handler can run late while the mic starts,
    // and a quick tap must never be mistaken for a long hold.
    pressAt = e.timeStamp;
    if (recorder.recording) {
      startedByPress = false;
      await stop();
    } else {
      startedByPress = true;
      releasedEarly = false;
      await start();
      // Let go of a long hold while the mic was still starting: stop now.
      if (releasedEarly && recorder.recording) await stop();
      releasedEarly = false;
    }
  });
  talk.addEventListener("pointerup", async (e) => {
    // Held down long enough: hold-to-talk, so letting go stops it.
    const held = e.timeStamp - pressAt;
    const wasHold = startedByPress && held > HOLD_MS;
    startedByPress = false;
    if (!wasHold) return;
    if (recorder.recording) await stop();
    else releasedEarly = true;
  });
  talk.addEventListener("keydown", async (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    if (recorder.recording) await stop();
    else await start();
  });
  talk.addEventListener("contextmenu", (e) => e.preventDefault());

  $<HTMLInputElement>("#photo").addEventListener("change", async (e) => {
    const input = e.target as HTMLInputElement;
    for (const f of Array.from(input.files ?? [])) {
      await addPhoto(walk!.id, f);
      photos++;
      paint();
    }
    input.value = "";
    void sync();
  });

  // Two taps, so a brush in the pocket can't end the walk.
  const endBtn = $<HTMLButtonElement>("#end");
  let armed = 0;
  endBtn.addEventListener("click", async () => {
    if (!armed) {
      buzz(30);
      endBtn.textContent = "Tap again to end the walk";
      endBtn.classList.add("is-armed");
      armed = window.setTimeout(() => {
        armed = 0;
        endBtn.textContent = "End walk";
        endBtn.classList.remove("is-armed");
      }, 4000);
      return;
    }
    clearTimeout(armed);
    if (recorder.recording) await stop();
    await saveScreenTime();
    walk = await endWalk(walk!);
    cleanup();
    location.hash = `#/trail/done/${walk.id}`;
  });

  const tick = window.setInterval(async () => {
    paint();
    const use = await storageUse();
    lowStorage = !!use && use.used / use.quota > 0.8;
  }, 1000);
  // Retry anything that didn't upload: when the connection returns, and every 15 s.
  const onOnline = () => void sync();
  addEventListener("online", onOnline);
  const retry = window.setInterval(() => waiting && void sync(), 15_000);
  if (waiting || recovered) void sync();

  function cleanup() {
    // Remove our hashchange listener too, or it fires after the next screen has
    // switched on its own Trail theme and switches it off again.
    removeEventListener("hashchange", cleanup);
    clearInterval(tick);
    document.removeEventListener("visibilitychange", onVisibility);
    clearInterval(retry);
    removeEventListener("online", onOnline);
    document.documentElement.classList.remove("is-trail");
  }
  addEventListener("hashchange", cleanup);
  paint();
}

export async function trailDoneScreen(root: HTMLElement, walkId: string) {
  document.title = "Walk saved · Walkworth";
  document.documentElement.classList.add("is-trail");
  const leave = () => {
    removeEventListener("hashchange", leave);
    document.documentElement.classList.remove("is-trail");
  };
  addEventListener("hashchange", leave);
  let walk: Walk | undefined = await getWalk(walkId);
  if (!walk?.trekId) {
    location.replace("#/prepare");
    return;
  }
  const n = (await walkNotes(walk.id)).length;
  const p = (await walkPhotos(walk.id)).length;
  const length = walkLength((walk.endedAt ?? Date.now()) - walk.startedAt);
  root.innerHTML = html`<main class="trail trail--message">
    <p class="mono trail-place">${walk.title}</p>
    <h1 id="done-title">Saving your walk</h1>
    <p>${length} · ${n} voice ${n === 1 ? "note" : "notes"} · ${p} ${p === 1 ? "photo" : "photos"} · screen on ${Math.max(1, Math.round(walk.screenSeconds / 60))} min</p>
    <p class="mono" id="upload-status" role="status">Uploading…</p>
    <button class="trail-save" type="button" id="next" hidden>Add the GPX and make the journal</button>
    <button class="trail-text-btn" type="button" id="retry" hidden>Try again</button>
    <a class="trail-text-btn" href="#/shelf">Back to your shelf</a>
  </main>`.value;
  const status = root.querySelector<HTMLElement>("#upload-status")!;
  const next = root.querySelector<HTMLButtonElement>("#next")!;
  const retry = root.querySelector<HTMLButtonElement>("#retry")!;
  const title = root.querySelector<HTMLElement>("#done-title")!;

  async function upload() {
    retry.hidden = true;
    status.textContent = "Uploading…";
    const waiting = await syncWalk(walk!.id, true);
    // The trek may have been started again if the server had lost it.
    walk = (await getWalk(walkId)) ?? walk;
    if (waiting) {
      status.textContent = `${waiting} ${waiting === 1 ? "item hasn't" : "items haven't"} uploaded yet. They're safe on this phone. Check your connection and try again.`;
      retry.hidden = false;
      return;
    }
    try {
      await api.finish(walk!.trekId, walk!.screenSeconds);
    } catch {
      // The notes are all on the server; the screen-on time is a nice-to-have.
    }
    title.textContent = "Walk saved";
    status.textContent = "Everything is uploaded. Add the GPX from OpenTracks if you have it, then make the journal.";
    next.hidden = false;
  }
  retry.addEventListener("click", upload);
  next.addEventListener("click", () => (location.hash = `#/import/${walk!.trekId}`));
  await upload();
}
