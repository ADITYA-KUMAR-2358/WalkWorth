// Screen 6: Review. Fix a word, delete a note, move a photo, rewrite a line.
// Edits are kept; rebuilding the journal never overwrites them.

import { api, ApiError, errorText, fileUrl } from "../lib/api";
import { html, type Raw } from "../lib/html";
import { icons } from "../lib/icons";
import { appBar, bindSiteHeader } from "../lib/site";
import type { Entry, GeneratedLine, Photo, VoiceNote } from "../types/schema";
import "./review.css";

const LOW = 0.6;
const CHAPTER_NAMES: Record<string, string> = {
  start: "Setting out", climb: "The climb", top: "At the top", descent: "The way down",
  first: "Setting out", middle: "Along the way", last: "The way back",
};

const time = (iso: string, tz = "Asia/Kolkata") =>
  new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: tz }).format(new Date(iso));

function transcriptHtml(n: VoiceNote): Raw {
  if (!n.transcript) return html`<span class="mono">${n.skipped === "no speech" ? "No speech heard in this clip." : "Not transcribed yet."}</span>`;
  if (n.user_edited || !n.words.length) return html`${n.transcript}`;
  // Underline words speech to text wasn't sure about.
  return html`${n.words.map((w, i) => html`${i ? " " : ""}${w.p != null && w.p < LOW ? html`<u title="Speech to text wasn't sure (${Math.round(w.p * 100)}%)">${w.w}</u>` : w.w}`)}`;
}

export async function reviewScreen(root: HTMLElement, trekId: string) {
  document.title = "Review · Walkworth";
  let notes: VoiceNote[] = [];
  let photos: Photo[] = [];
  let entries: Entry[] = [];
  let lines: GeneratedLine[] = [];
  let title = "";
  let tz = "Asia/Kolkata";
  let canEdit = false;
  let isSample = false;
  let editing: string | null = null;
  let confirmDelete: string | null = null;
  let dirty = false;
  let message: { text: string; bad?: boolean } | null = null;

  async function load() {
    const s = await api.trek(trekId);
    title = s.trek.title;
    tz = s.trek.tz;
    canEdit = s.can_edit;
    isSample = s.trek.is_sample;
    [notes, photos, entries, lines] = await Promise.all([api.notes(trekId), api.photos(trekId), api.entries(trekId), api.lines(trekId)]);
  }

  const line = (role: string, chapter: string | null) =>
    lines.find((l) => l.role === role && (l.chapter ?? null) === chapter && (role !== "opener" || l.id.endsWith("-0")));

  function lineField(role: string, chapter: string | null, label: string, placeholder: string): Raw {
    const l = line(role, chapter);
    const id = `line-${role}-${chapter ?? "x"}`;
    if (!canEdit) {
      return html`<div class="field">
        <p class="rv-line-label">${label}${l ? html` <span class="mono">· cites ${l.source_ids.join(", ")}</span>` : ""}</p>
        <p class="rv-line-text">${l?.text ?? html`<span class="mono">None</span>`}</p>
      </div>`;
    }
    return html`<div class="field">
      <label for="${id}">${label}${l && !l.user_edited ? html` <span class="mono">· written by the model, cites ${l.source_ids.join(", ")}</span>` : l?.user_edited ? html` <span class="mono">· yours</span>` : ""}</label>
      <input class="input" id="${id}" data-role="${role}" data-chapter="${chapter ?? ""}" value="${l?.text ?? ""}" placeholder="${placeholder}">
    </div>`;
  }

  function photoCard(p: Photo, hostNoteId: string | null): Raw {
    const others = notes.filter((n) => n.id !== hostNoteId && n.transcript);
    if (!canEdit) {
      return html`<figure class="rv-photo">
        <img src="${fileUrl(p.path)}" alt="${p.caption ?? "Photo"}" loading="lazy">
        <figcaption class="mono">${p.caption ?? "No caption"}</figcaption>
      </figure>`;
    }
    return html`<figure class="rv-photo" draggable="true" data-photo="${p.id}">
      <img src="${fileUrl(p.path)}" alt="${p.caption ?? "Photo"}" loading="lazy">
      <figcaption>
        <input class="input rv-caption" data-caption="${p.id}" value="${p.caption ?? ""}" placeholder="Caption" aria-label="Caption">
        <label class="mono rv-move">Move to
          <select class="select" data-move="${p.id}">
            <option value="">this note</option>
            ${others.map((n) => html`<option value="${n.id}">${time(n.t, tz)} · ${(n.transcript ?? "").slice(0, 32)}</option>`)}
          </select>
        </label>
      </figcaption>
    </figure>`;
  }

  function entryRow(e: Entry): Raw {
    const n = notes.find((x) => x.id === e.note_id);
    const ps = e.photo_ids.map((id) => photos.find((p) => p.id === id)).filter((p): p is Photo => !!p);
    return html`<li class="rv-entry" data-drop="${n?.id ?? ""}">
      <div class="rv-side">
        <p class="mono">${time(e.t, tz)}${e.ele_m != null ? ` · ${Math.round(e.ele_m)} m` : ""}</p>
        ${n?.audio_path && (canEdit || isSample) ? html`<audio controls preload="none" src="${fileUrl(n.audio_path)}" aria-label="Play the voice note from ${time(e.t, tz)}"></audio>` : ""}
        ${n?.lang && n.lang !== "en" ? html`<p class="mono">Heard in ${n.lang}</p>` : ""}
      </div>
      <div class="rv-main">
        ${n ? editing === n.id
          ? html`<textarea class="textarea" id="edit-${n.id}" aria-label="Transcript">${n.transcript ?? ""}</textarea>
              <div class="rv-actions"><button class="btn btn--small" data-save="${n.id}">Save words</button><button class="link-btn" data-cancel>Cancel</button></div>`
          : html`<p class="rv-transcript">${transcriptHtml(n)}</p>
              ${n.translation ? html`<p class="mono">Translation: ${n.translation}</p>` : ""}
              ${canEdit ? html`<div class="rv-actions">
                <button class="link-btn" data-edit="${n.id}">Fix words</button>
                ${confirmDelete === n.id
                  ? html`<span class="rv-confirm">Delete this note? This can't be undone. <button class="link-btn link-btn--danger" data-delete="${n.id}">Delete</button> <button class="link-btn" data-cancel>Keep it</button></span>`
                  : html`<button class="link-btn" data-ask-delete="${n.id}">Delete note</button>`}
              </div>` : ""}`
          : html`<p class="mono">A photo on its own</p>`}
        ${ps.length ? html`<div class="rv-photos">${ps.map((p) => photoCard(p, n?.id ?? null))}</div>` : ""}
      </div>
    </li>`;
  }

  function render() {
    const chapters: string[] = [];
    entries.forEach((e) => chapters.includes(e.chapter) || chapters.push(e.chapter));
    root.innerHTML = html`<main class="screen screen--wide">
      ${appBar({ href: "#/shelf", label: "Your shelf" }, html`<a href="/journal/${trekId}">The journal</a>`)}
      <header class="screen-head">
        <p class="mono eyebrow">${title} · review</p>
        <h1>${canEdit ? "Check the words before you keep them" : "What speech to text heard"}</h1>
        <p class="lede">Underlined words are ones speech to text wasn't sure of.${canEdit ? " Your edits stay put when the journal is rebuilt." : ""}</p>
      </header>
      ${canEdit ? "" : html`<div class="notice notice--quiet">
        <p>${isSample ? "This is the sample, so editing is turned off." : "Only the browser that made this walk can edit it."}</p>
        <p><a href="#/prepare">Start a walk</a> or <a href="#/import">import one</a> to try fixing words, moving photos and rewriting lines.</p>
      </div>`}

      <h2 class="section-title">Lines around your words</h2>
      ${lineField("opener", null, "Opening line", "Leave empty for none")}
      ${chapters.map((c) => html`<div class="rv-chapter-lines">
        ${lineField("chapter_title", c, `Chapter title (${CHAPTER_NAMES[c] ?? c})`, CHAPTER_NAMES[c] ?? "")}
        ${lineField("chapter_line", c, "Line under the first note", "Leave empty for none")}
      </div>`)}

      ${chapters.map((c) => html`<h2 class="section-title">${line("chapter_title", c)?.text ?? CHAPTER_NAMES[c] ?? c}</h2>
        <ol class="rv-list">${entries.filter((e) => e.chapter === c).map(entryRow)}</ol>`)}

      ${entries.length ? "" : html`<p class="notice">Nothing to review yet. <a href="#/import/${trekId}">Import your walk</a> first.</p>`}

      ${canEdit ? html`<div class="rv-bar">
        ${message ? html`<p class="mono ${message.bad ? "rv-bad" : ""}" role="status">${message.text}</p>` : html`<p class="mono">${dirty ? "You have edits the journal doesn't show yet." : "The journal is up to date."}</p>`}
        <button class="btn btn--primary" type="button" id="rebuild"><span>Save and rebuild the journal</span>${icons.arrowRight}</button>
      </div>` : ""}
    </main>`.value;
    bind();
  }

  async function act(fn: () => Promise<unknown>, done: string) {
    try {
      await fn();
      dirty = true;
      message = { text: done };
      await load();
    } catch (e) {
      message = { text: errorText(e), bad: true };
    }
    render();
  }

  function bind() {
    bindSiteHeader(root);
    root.querySelectorAll<HTMLButtonElement>("[data-edit]").forEach((b) => b.addEventListener("click", () => {
      editing = b.dataset.edit!;
      confirmDelete = null;
      render();
      root.querySelector<HTMLTextAreaElement>(`#edit-${CSS.escape(editing)}`)?.focus();
    }));
    root.querySelectorAll("[data-cancel]").forEach((b) => b.addEventListener("click", () => {
      editing = confirmDelete = null;
      render();
    }));
    root.querySelectorAll<HTMLButtonElement>("[data-save]").forEach((b) => b.addEventListener("click", () => {
      const id = b.dataset.save!;
      const text = root.querySelector<HTMLTextAreaElement>(`#edit-${CSS.escape(id)}`)!.value;
      editing = null;
      act(() => api.editNote(id, text), "Words saved.");
    }));
    root.querySelectorAll<HTMLButtonElement>("[data-ask-delete]").forEach((b) => b.addEventListener("click", () => {
      confirmDelete = b.dataset.askDelete!;
      render();
    }));
    root.querySelectorAll<HTMLButtonElement>("[data-delete]").forEach((b) => b.addEventListener("click", () => {
      confirmDelete = null;
      act(() => api.deleteNote(b.dataset.delete!), "Note deleted.");
    }));
    root.querySelectorAll<HTMLInputElement>("[data-role]").forEach((inp) => inp.addEventListener("change", () => {
      act(() => api.editLine(trekId, inp.dataset.role!, inp.dataset.chapter || null, inp.value), "Line saved.");
    }));
    root.querySelectorAll<HTMLInputElement>("[data-caption]").forEach((inp) => inp.addEventListener("change", () => {
      act(() => api.editPhoto(inp.dataset.caption!, { caption: inp.value }), "Caption saved.");
    }));
    root.querySelectorAll<HTMLSelectElement>("[data-move]").forEach((sel) => sel.addEventListener("change", () => {
      if (sel.value) act(() => api.editPhoto(sel.dataset.move!, { pinned_note_id: sel.value }), "Photo moved. Rebuild to see it in place.");
    }));

    // Drag a photo onto another note.
    root.querySelectorAll<HTMLElement>("[data-photo]").forEach((el) => el.addEventListener("dragstart", (e) => {
      e.dataTransfer?.setData("text/plain", el.dataset.photo!);
    }));
    root.querySelectorAll<HTMLElement>("[data-drop]").forEach((row) => {
      if (!row.dataset.drop) return;
      row.addEventListener("dragover", (e) => {
        e.preventDefault();
        row.classList.add("is-over");
      });
      row.addEventListener("dragleave", () => row.classList.remove("is-over"));
      row.addEventListener("drop", (e) => {
        e.preventDefault();
        row.classList.remove("is-over");
        const pid = e.dataTransfer?.getData("text/plain");
        if (pid) act(() => api.editPhoto(pid, { pinned_note_id: row.dataset.drop! }), "Photo moved. Rebuild to see it in place.");
      });
    });

    root.querySelector("#rebuild")?.addEventListener("click", async () => {
      message = { text: "Rebuilding the journal…" };
      render();
      try {
        await api.recompose(trekId);
        for (let i = 0; i < 240; i++) {
          const st = await api.recomposeStatus(trekId);
          if (st.state !== "running") {
            if (st.state === "failed") throw new ApiError(st.error ?? "The rebuild stopped.", 500);
            break;
          }
          await new Promise((r) => setTimeout(r, 1500));
        }
        location.href = `/journal/${trekId}`;
      } catch (e) {
        message = { text: errorText(e), bad: true };
        render();
      }
    });
  }

  root.innerHTML = html`<main class="screen">${appBar({ href: "#/shelf", label: "Your shelf" })}<p class="loading-line" aria-busy="true">Loading the words…</p></main>`.value;
  try {
    await load();
    render();
  } catch (e) {
    root.innerHTML = html`<main class="screen">${appBar({ href: "#/shelf", label: "Your shelf" })}
      <p class="notice" role="alert">${e instanceof ApiError && e.status === 404 ? "There's no walk at this link. It may have been deleted, or the server was redeployed." : errorText(e)}</p></main>`.value;
    bindSiteHeader(root);
  }
}
