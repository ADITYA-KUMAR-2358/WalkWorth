// Screen 8: Export. Four files and a copy-to-DEV button, sizes shown.

import { api, errorText, type ExportOut } from "../lib/api";
import { html } from "../lib/html";
import { appBar, bindSiteHeader } from "../lib/site";
import "./export.css";

const KINDS = [
  { kind: "html", label: "Journal website", note: "A folder that opens in any browser, with no server needed." },
  { kind: "pdf", label: "PDF for print", note: "A4, typeset with WeasyPrint." },
  { kind: "gpx", label: "GPX track", note: "The route, for any map app." },
  { kind: "markdown", label: "Markdown", note: "Plain text with your quotes and photo captions." },
];

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export async function exportScreen(root: HTMLElement, trekId: string) {
  document.title = "Export · Walkworth";
  let title = trekId;
  const trimM = 300;
  try {
    title = (await api.trek(trekId)).trek.title;
  } catch {
    // Errors show per file below.
  }
  let trim = true;
  const built: Record<string, ExportOut | { error: string } | null> = {};
  let copied = "";

  function render() {
    root.innerHTML = html`<main class="screen">
      ${appBar({ href: `/journal/${trekId}`, label: "The journal" })}
      <header class="screen-head">
        <p class="mono eyebrow">${title} · keep it</p>
        <h1>Take the journal with you</h1>
        <p class="lede">These files hold only the journal. Raw audio isn't included, and the route is trimmed at both ends unless you turn that off.</p>
      </header>

      <label class="ex-trim">
        <input type="checkbox" id="trim" ${trim ? "checked" : ""}>
        <span>Trim ${trimM} m from each end of the route, so the start and finish (home, a hostel) aren't shown</span>
      </label>

      <ul class="ex-list">
        ${KINDS.map((k) => {
          const b = built[k.kind];
          const ready = b && "url" in b;
          return html`<li class="ex-row">
            <div>
              <p class="ex-label">${k.label}</p>
              <p class="mono">${b && "error" in b ? b.error : k.note}</p>
            </div>
            ${ready
              ? html`<a class="btn" href="${(b as ExportOut).url}" download="${(b as ExportOut).name}">Download <span class="mono">${size((b as ExportOut).bytes)}</span></a>`
              : b && "error" in b ? html`<button class="btn" data-retry="${k.kind}">Try again</button>`
              : html`<span class="btn" aria-disabled="true">Building…</span>`}
          </li>`;
        })}
      </ul>

      <h2 class="section-title">For your DEV post</h2>
      <p style="margin-top:.75rem">Copies the journal as Markdown, with image links that point at this site.</p>
      <div class="ex-actions">
        <button class="btn" type="button" id="dev">Copy for DEV</button>
        <span class="mono" role="status">${copied}</span>
      </div>
      <p class="mono" style="margin-top:1.25rem">Uploads are kept until the site is next redeployed. Download anything you want to keep.</p>
    </main>`.value;

    bindSiteHeader(root);
    root.querySelector<HTMLInputElement>("#trim")?.addEventListener("change", (e) => {
      trim = (e.target as HTMLInputElement).checked;
      buildAll();
    });
    root.querySelectorAll<HTMLButtonElement>("[data-retry]").forEach((b) => b.addEventListener("click", () => build(b.dataset.retry!)));
    root.querySelector("#dev")?.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(await api.devMarkdown(trekId));
        copied = "Copied. Paste it into the DEV editor.";
      } catch {
        copied = "Couldn't reach the clipboard. Use the Markdown file instead.";
      }
      render();
    });
  }

  async function build(kind: string) {
    built[kind] = null;
    render();
    try {
      built[kind] = await api.buildExport(trekId, kind, trim);
    } catch (e) {
      built[kind] = { error: errorText(e) };
    }
    render();
  }

  async function buildAll() {
    for (const k of KINDS) built[k.kind] = null;
    render();
    // Quick ones first; the PDF takes a few seconds.
    for (const k of ["html", "gpx", "markdown", "pdf"]) await build(k);
  }

  await buildAll();
}
