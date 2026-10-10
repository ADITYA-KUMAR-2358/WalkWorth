// Screen 5: making the journal. Live progress from the Temporal workflow,
// with the model doing each step named. That's the open-source story.

import { api, errorText } from "../lib/api";
import { html } from "../lib/html";
import { icons } from "../lib/icons";
import { appBar, bindSiteHeader } from "../lib/site";
import type { PipelineStep, ProcessStatus } from "../types/schema";

const POLL_MS = 1500;

function count(s: PipelineStep): string {
  if (s.status === "done") return s.total > 1 ? `${s.total} / ${s.total}` : "done";
  if (s.status === "failed") return "stopped";
  if (s.status === "waiting") return "waiting";
  return s.total > 1 ? `${s.done} / ${s.total}` : "working";
}

function step(s: PipelineStep) {
  const pct = s.total ? Math.round((s.done / s.total) * 100) : 0;
  return html`<li class="step step--${s.status}">
    <span class="step-mark" aria-hidden="true"></span>
    <span>
      <span class="step-name">${s.label}</span>
      ${s.model || s.detail ? html`<br><span class="step-model">${s.detail ?? s.model}</span>` : ""}
    </span>
    <span class="step-count">${count(s)}</span>
    ${s.status === "running" && s.total > 1 ? html`<span class="step-bar" role="progressbar" aria-valuenow="${pct}" aria-valuemin="0" aria-valuemax="100"><span style="width:${pct}%"></span></span>` : ""}
  </li>`;
}

export async function processingScreen(root: HTMLElement, trekId: string) {
  document.title = "Making the journal · Walkworth";
  let title = trekId;
  let canEdit = false;
  try {
    const s = await api.trek(trekId);
    title = s.trek.title;
    canEdit = s.can_edit;
  } catch {
    // Shown in the error state below.
  }
  let timer = 0;
  let alive = true;
  addEventListener("hashchange", () => {
    alive = false;
    clearTimeout(timer);
  }, { once: true });

  function render(st: ProcessStatus | null, error?: string) {
    const done = st?.state === "completed";
    const failed = st?.state === "failed";
    const notStarted = st?.state === "not_started";
    root.innerHTML = html`<main class="screen">
      ${appBar({ href: "#/shelf", label: "Your shelf" }, canEdit && !st?.state.match(/running|completed/) ? html`<a href="#/import/${trekId}">Add files</a>` : "")}
      <header class="screen-head">
        <p class="mono eyebrow">${title}</p>
        <h1>${done ? "Your journal is ready" : "Turning your walk into a journal"}</h1>
      </header>

      ${st ? html`<ol class="steps">${st.steps.map(step)}</ol>` : html`<ol class="steps" aria-busy="true">${["Import", "Transcribe", "Align to the trail", "Pick out what you saw", "Caption photos", "Write the journal"].map((l) => step({ key: l, label: l, status: "waiting", done: 0, total: 0, model: null, detail: null }))}</ol>`}

      ${error || st?.error ? html`<div class="notice" role="alert">
        <p>${error ?? st?.error}</p>
        ${failed ? html`<p class="mono">Fix it, then start again. Steps that finished won't run twice.</p>` : ""}
      </div>` : ""}

      <div style="margin-top:2rem">
        ${done ? html`<a class="btn btn--primary" href="/journal/${trekId}"><span>Read your journal</span>${icons.arrowRight}</a>
          <p class="mono" style="margin-top:.75rem"><a href="#/review/${trekId}">Review words and photos first</a></p>`
        : (failed || notStarted) && canEdit ? html`<button class="btn btn--primary" type="button" id="start"><span>${failed ? "Start again" : "Make the journal"}</span>${icons.arrowRight}</button>`
        : failed || notStarted ? html`<p class="notice notice--quiet">Only the browser that made this walk can make its journal.</p>`
        : ""}
      </div>

      <div class="panel">
        <p class="mono">Gemma on the Gemini API · ElevenLabs Scribe · Temporal</p>
        <p style="margin-top:.4rem">Safe to close this tab. Temporal picks up exactly where it stopped.</p>
      </div>
    </main>`.value;
    bindSiteHeader(root);
    root.querySelector("#start")?.addEventListener("click", async (e) => {
      (e.currentTarget as HTMLButtonElement).disabled = true;
      try {
        render(await api.process(trekId));
        poll();
      } catch (err) {
        render(st, errorText(err));
      }
    });
  }

  async function poll() {
    if (!alive) return;
    try {
      const st = await api.processStatus(trekId);
      render(st);
      if (st.state === "running") timer = window.setTimeout(poll, POLL_MS);
    } catch (e) {
      render(null, errorText(e));
      timer = window.setTimeout(poll, POLL_MS * 3);
    }
  }

  render(null);
  await poll();
}
