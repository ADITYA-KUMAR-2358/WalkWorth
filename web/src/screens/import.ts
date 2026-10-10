// Screen 4: Import. Drop the GPX, photos and voice notes; fix clock
// drift with one slider; then make the journal.

import { accessCode, api, ApiError, errorText, setAccessCode, siteHealth } from "../lib/api";
import { fortPacks } from "../lib/fortpacks";
import { html, raw, type Raw } from "../lib/html";
import { icons } from "../lib/icons";
import { appBar, bindSiteHeader } from "../lib/site";
import type { ImportResult, Photo, TrackPoint, Trek, VoiceNote } from "../types/schema";
import "./import.css";

const KIND_LABEL: Record<string, string> = { gpx: "GPX track", photo: "Photo", audio: "Voice note", unknown: "Other" };

interface Landed {
  name: string;
  result?: ImportResult;
  error?: string;
}

function project(track: TrackPoint[], w: number, h: number) {
  const lat0 = ((track.reduce((s, p) => s + p.lat, 0) / track.length) * Math.PI) / 180;
  const xs = track.map((p) => p.lon * Math.cos(lat0));
  const ys = track.map((p) => -p.lat);
  const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const scale = Math.min((w - 24) / (maxX - minX || 1), (h - 24) / (maxY - minY || 1));
  const ox = (w - (maxX - minX) * scale) / 2;
  const oy = (h - (maxY - minY) * scale) / 2;
  return (lat: number, lon: number): [number, number] => [ox + (lon * Math.cos(lat0) - minX) * scale, oy + (-lat - minY) * scale];
}

function nearest(track: TrackPoint[], t: number): TrackPoint {
  let lo = 0;
  let hi = track.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (Date.parse(track[mid].t) < t) lo = mid + 1;
    else hi = mid;
  }
  const a = track[Math.max(0, lo - 1)];
  const b = track[lo];
  return Math.abs(Date.parse(a.t) - t) < Math.abs(Date.parse(b.t) - t) ? a : b;
}

function offsetLabel(s: number): string {
  if (s === 0) return "No offset";
  const m = Math.round(Math.abs(s) / 60);
  return `Photos and notes moved ${m} min ${s > 0 ? "later" : "earlier"}`;
}

function cannotImport(root: HTMLElement, trekId: string, message: string, journal: boolean) {
  root.innerHTML = html`<main class="screen">
    ${appBar({ href: "#/shelf", label: "Your shelf" })}
    <header class="screen-head">
      <p class="mono eyebrow">Your walk</p>
      <h1>This walk can't take new files</h1>
    </header>
    <div class="notice" role="alert">
      <p>${message}</p>
      ${journal ? html`<p><a href="/journal/${trekId}">Read its journal</a> or <a href="#/import">import a walk of your own</a>.</p>`
        : html`<p><a href="#/import">Import a walk of your own</a> instead.</p>`}
    </div>
  </main>`.value;
  bindSiteHeader(root);
}

export async function importScreen(root: HTMLElement, trekId: string | null) {
  document.title = "Import · Walkworth";
  let trek: Trek | null = null;
  if (trekId) {
    root.innerHTML = html`<main class="screen">${appBar({ href: "#/shelf", label: "Your shelf" })}<p class="loading-line" aria-busy="true">Opening your walk…</p></main>`.value;
    try {
      const s = await api.trek(trekId);
      if (!s.can_edit) {
        return cannotImport(root, trekId, s.trek.is_sample
          ? "The sample is read-only, so nothing can be added to it."
          : "Only the browser that started this walk can add files to it.", true);
      }
      trek = s.trek;
    } catch (e) {
      return cannotImport(root, trekId, e instanceof ApiError && e.status === 404
        ? "There's no walk at this link. It may have been deleted, or the server was redeployed (uploads don't survive a redeploy)."
        : errorText(e), false);
    }
  }
  const needsCode = !trek && await siteHealth().then((h) => h.access_code).catch(() => false);
  let code = accessCode();
  let failure = "";
  let title = trek?.title ?? fortPacks[0]?.name ?? "";
  let packId: string | null = trek?.fort_pack_id ?? fortPacks[0]?.id ?? null;
  const landed: Landed[] = [];
  let track: TrackPoint[] = [];
  let notes: VoiceNote[] = [];
  let photos: Photo[] = [];
  let offset = trek?.time_offset_s ?? 0;
  let busy = 0;

  async function refreshData() {
    if (!trek) return;
    [track, notes, photos] = await Promise.all([api.track(trek.id), api.notes(trek.id), api.photos(trek.id)]);
  }

  function preview(): Raw {
    if (track.length < 2) {
      return html`<p class="mono import-preview-empty">${notes.length || photos.length
        ? "No GPX yet. Without one, notes are placed by their own GPS fixes."
        : "The route appears here once a GPX lands."}</p>`;
    }
    const W = 600;
    const H = 320;
    const p = project(track, W, H);
    const d = track.map((pt, i) => `${i ? "L" : "M"}${p(pt.lat, pt.lon).map((n) => n.toFixed(1)).join(",")}`).join("");
    const pins = (items: { t: string }[], cls: string) =>
      items.map((it) => {
        const pt = nearest(track, Date.parse(it.t) + offset * 1000);
        const [x, y] = p(pt.lat, pt.lon);
        return cls === "photo"
          ? `<rect class="pv-photo" x="${(x - 5).toFixed(1)}" y="${(y - 5).toFixed(1)}" width="10" height="10"/>`
          : `<circle class="pv-note" cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="6"/>`;
      }).join("");
    return html`<svg class="import-preview" viewBox="0 0 ${W} ${H}" role="img" aria-label="Route with ${notes.length} notes and ${photos.length} photos placed by time">
      <path d="${d}" class="pv-route"/>
      <g id="pv-pins">${raw(pins(notes, "note") + pins(photos, "photo"))}</g>
    </svg>`;
  }

  function counts(): string {
    const parts = [
      track.length ? "GPX" : null,
      `${notes.length} ${notes.length === 1 ? "note" : "notes"}`,
      `${photos.length} ${photos.length === 1 ? "photo" : "photos"}`,
    ].filter(Boolean);
    return parts.join(" · ");
  }

  function render() {
    const canProcess = !!trek && (notes.length > 0 || photos.length > 0) && busy === 0;
    root.innerHTML = html`<main class="screen">
      ${appBar({ href: "#/shelf", label: "Your shelf" }, trek ? counts() : "New walk")}
      <header class="screen-head">
        <p class="mono eyebrow">Your walk</p>
        <h1>Bring your walk in</h1>
        <p class="lede">Add the GPX from OpenTracks, and any voice notes or photos you took outside Walkworth.</p>
      </header>

      <div class="import-meta">
        <div class="field">
          <label for="title">Name of the walk</label>
          <input class="input" id="title" value="${title}" ${trek ? "disabled" : ""}>
        </div>
        <div class="field">
          <label for="pack">Fort pack</label>
          <select class="select" id="pack" ${trek ? "disabled" : ""}>
            ${fortPacks.map((p) => html`<option value="${p.id}" ${p.id === packId ? "selected" : ""}>${p.name}</option>`)}
            <option value="" ${packId ? "" : "selected"}>None</option>
          </select>
        </div>
      </div>
      ${needsCode ? html`<div class="field">
        <label for="code">Access code</label>
        <input class="input" id="code" value="${code}" autocomplete="off" autocapitalize="off" spellcheck="false">
        <p class="mono">This Walkworth is shared with a code. Ask whoever sent you the link.</p>
      </div>` : ""}
      ${failure ? html`<div class="notice" role="alert"><p>${failure}</p></div>` : ""}

      <label class="drop" id="drop">
        <input class="file-input" type="file" id="files" multiple accept=".gpx,image/*,audio/*,.heic,.heif">
        ${icons.uploadSimple}
        <span class="drop-title">Drop files here</span>
        <span class="mono">or tap to choose them: the OpenTracks .gpx, photos, voice notes</span>
      </label>

      ${landed.length ? html`<ul class="landed" aria-live="polite">
        ${landed.map((l) => html`<li class="landed-row ${l.error || l.result?.status === "skipped" ? "landed-row--bad" : ""}">
          <span class="landed-name">${l.name}</span>
          <span class="mono">${l.error ? "Failed" : l.result ? `${KIND_LABEL[l.result.kind]}, ${l.result.status}` : "Reading…"}</span>
          ${l.error || l.result?.detail ? html`<span class="mono landed-detail">${l.error ?? l.result?.detail}</span>` : ""}
        </li>`)}
      </ul>` : ""}

      ${trek ? html`<h2 class="section-title">Line up the clocks</h2>
        <p class="mono" style="margin-top:.75rem">If your phone's clock and the GPS disagree, slide until the pins sit where you were.</p>
        <div class="preview-wrap">${preview()}</div>
        <div class="offset">
          <input type="range" id="offset" min="-90" max="90" step="1" value="${Math.round(offset / 60)}" aria-label="Clock offset in minutes" aria-valuetext="${offsetLabel(offset)}" aria-describedby="offset-label" ${track.length < 2 ? "disabled" : ""}>
          <p class="mono" id="offset-label">${offsetLabel(offset)}</p>
        </div>
        <p class="mono preview-key"><span class="key-note"></span> voice note <span class="key-photo"></span> photo</p>` : ""}

      <div style="margin-top:2.5rem">
        <button class="btn btn--primary" type="button" id="make" ${canProcess ? "" : "disabled"}>
          <span>Make the journal</span>${icons.arrowRight}
        </button>
        <p class="mono" style="margin-top:.6rem">${busy ? `Reading ${busy} ${busy === 1 ? "file" : "files"}…` : canProcess ? "Safe to close this tab once it starts." : "Add at least one voice note or photo first."}</p>
      </div>
    </main>`.value;
    bind();
  }

  async function ensureTrek(): Promise<Trek> {
    if (trek) return trek;
    if (needsCode) setAccessCode(code);
    trek = await api.createTrek(title, packId);
    history.replaceState(null, "", `#/import/${trek.id}`);
    return trek;
  }

  async function addFiles(files: File[]) {
    if (!files.length) return;
    let t: Trek;
    try {
      t = await ensureTrek();
      failure = "";
    } catch (e) {
      failure = `Walkworth couldn't start the walk. ${errorText(e)}`;
      if (e instanceof ApiError && e.status === 401) setAccessCode("");
      render();
      return;
    }
    // GPX first, so photos land on a known timeline.
    const order = (f: File) => (/\.gpx$/i.test(f.name) ? 0 : 1);
    files.sort((a, b) => order(a) - order(b));
    const rows = files.map((f) => ({ name: f.name } as Landed));
    landed.unshift(...rows);
    busy += files.length;
    render();
    for (let i = 0; i < files.length; i++) {
      try {
        rows[i].result = (await api.upload(t.id, files[i]))[0];
      } catch (e) {
        rows[i].error = errorText(e);
      }
      busy--;
      await refreshData();
      render();
    }
  }

  function bind() {
    bindSiteHeader(root);
    root.querySelector<HTMLInputElement>("#code")?.addEventListener("input", (e) => (code = (e.target as HTMLInputElement).value.trim()));
    root.querySelector<HTMLInputElement>("#title")?.addEventListener("input", (e) => (title = (e.target as HTMLInputElement).value));
    root.querySelector<HTMLSelectElement>("#pack")?.addEventListener("change", (e) => (packId = (e.target as HTMLSelectElement).value || null));
    const drop = root.querySelector<HTMLElement>("#drop")!;
    const input = root.querySelector<HTMLInputElement>("#files")!;
    input.addEventListener("change", () => addFiles(Array.from(input.files ?? [])));
    drop.addEventListener("dragover", (e) => {
      e.preventDefault();
      drop.classList.add("is-over");
    });
    drop.addEventListener("dragleave", () => drop.classList.remove("is-over"));
    drop.addEventListener("drop", (e) => {
      e.preventDefault();
      drop.classList.remove("is-over");
      addFiles(Array.from(e.dataTransfer?.files ?? []));
    });

    const slider = root.querySelector<HTMLInputElement>("#offset");
    slider?.addEventListener("input", () => {
      offset = Number(slider.value) * 60;
      // Live: move the pins without re-rendering the page.
      const wrap = root.querySelector(".preview-wrap");
      if (wrap) wrap.innerHTML = preview().value;
      const label = root.querySelector("#offset-label");
      if (label) label.textContent = offsetLabel(offset);
      slider.setAttribute("aria-valuetext", offsetLabel(offset));
    });
    slider?.addEventListener("change", () => trek && api.setOffset(trek.id, offset));

    root.querySelector("#make")?.addEventListener("click", async () => {
      if (!trek) return;
      try {
        await api.setOffset(trek.id, offset);
        await api.process(trek.id);
      } catch {
        // The processing screen explains what's missing (Temporal, the worker).
      }
      location.hash = `#/processing/${trek.id}`;
    });
  }

  await refreshData();
  render();
}
