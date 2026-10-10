import { api, errorText } from "../lib/api";
import { km, plural, shortDate } from "../lib/format";
import { html, type Raw } from "../lib/html";
import { icons } from "../lib/icons";
import { routeThumb } from "../lib/route-svg";
import { bindSiteHeader, siteFooter, siteHeader } from "../lib/site";
import type { TrekSummary } from "../types/schema";
import "./shelf.css";

interface View {
  treks: TrekSummary[] | null;
  error: string | null;
  confirming: string | null;
  message: string | null;
}

function deleteControl(s: TrekSummary, v: View): Raw {
  if (!s.can_edit) return html``;
  if (v.confirming !== s.trek.id) {
    return html`<button class="spine-delete" type="button" data-ask-delete="${s.trek.id}" aria-label="Delete ${s.trek.title}">${icons.trash}</button>`;
  }
  return html`<div class="spine-confirm" role="group" aria-label="Delete ${s.trek.title}">
    <p>Delete this walk, its notes and photos? This can't be undone.</p>
    <span class="confirm">
      <button class="btn btn--small btn--danger" type="button" data-delete="${s.trek.id}">Delete</button>
      <button class="link-btn" type="button" data-cancel>Keep it</button>
    </span>
  </div>`;
}

function spine(s: TrekSummary, v: View): Raw {
  const meta = [shortDate(s.trek.started_at, s.trek.tz), s.stats ? km(s.stats.distance_m) : null, plural(s.note_count, "voice note")]
    .filter(Boolean)
    .join(" · ");
  return html`<li class="spine-item ${s.can_edit ? "spine-item--own" : ""}">
    <a class="spine" href="${s.processed ? `/journal/${s.trek.id}` : `#/processing/${s.trek.id}`}">
      ${s.route.length > 1 ? routeThumb(s.route) : html`<span class="route-thumb route-thumb--none" title="Add the GPX to draw the route">${icons.mapPin}</span>`}
      <span class="spine-text">
        <span class="spine-title">
          <span class="spine-name">${s.trek.title}</span>
          ${s.trek.title_mr ? html`<span lang="mr" class="spine-mr">${s.trek.title_mr}</span>` : ""}
        </span>
        <span class="spine-meta">${meta}</span>
        ${s.trek.is_sample || !s.processed ? html`<span class="spine-chips">
          ${s.trek.is_sample ? html`<span class="chip">Sample data</span>` : ""}
          ${s.processed ? "" : html`<span class="chip chip--quiet">Journal not made yet</span>`}
        </span>` : ""}
      </span>
    </a>
    ${deleteControl(s, v)}
  </li>`;
}

const nextSpine = html`<li>
  <a class="spine spine--next" href="#/import">
    <span class="next-box" aria-hidden="true"></span>
    <span class="spine-text">
      <span class="next-text">Your next walk goes here</span>
      <span class="spine-meta">Start a walk, or bring one in with Import.</span>
    </span>
  </a>
</li>`;

const skeleton = html`<li class="spine spine--loading" aria-hidden="true">
  <span class="next-box"></span>
  <span class="spine-text"><span class="bar bar--title"></span><span class="bar"></span></span>
</li>`;

function shelfBody(v: View): Raw {
  if (v.error) {
    return html`<div class="shelf-error" role="alert">
      <p>Walkworth can't reach your shelf right now.</p>
      <p class="mono">${v.error}</p>
    </div>`;
  }
  if (!v.treks) return html`<ol class="spines" aria-busy="true">${skeleton}</ol>`;
  return html`<ol class="spines">${v.treks.map((s) => spine(s, v))}${nextSpine}</ol>
    ${v.message ? html`<p class="shelf-note" role="status">${v.message}</p>` : ""}
    <p class="shelf-note">Walks you start are kept for this browser only, until the site is next updated. Anyone you send a journal's link to can read that journal.</p>`;
}

function count(treks: TrekSummary[] | null): string {
  if (!treks) return "";
  return treks.length ? plural(treks.length, "journal") : "No journals yet";
}

function render(root: HTMLElement, v: View) {
  root.innerHTML = html`<div class="shelf-page">
    ${siteHeader("shelf")}
    <main class="shelf-main">
      <h1>Your shelf</h1>
      <p class="shelf-sub">${count(v.treks) || (v.error ? "" : "Loading your journals")}</p>
      ${shelfBody(v)}
    </main>
    ${siteFooter()}
  </div>`.value;
  bindSiteHeader(root);
  const again = (patch: Partial<View>, focus?: string) => {
    render(root, { ...v, ...patch });
    if (focus) root.querySelector<HTMLElement>(focus)?.focus();
  };
  root.querySelectorAll<HTMLButtonElement>("[data-ask-delete]").forEach((b) =>
    b.addEventListener("click", () => again({ confirming: b.dataset.askDelete!, message: null }, "[data-cancel]")));
  root.querySelector("[data-cancel]")?.addEventListener("click", () => {
    const id = v.confirming;
    again({ confirming: null }, id ? `[data-ask-delete="${CSS.escape(id)}"]` : undefined);
  });
  root.querySelector<HTMLButtonElement>("[data-delete]")?.addEventListener("click", async (e) => {
    const id = (e.currentTarget as HTMLButtonElement).dataset.delete!;
    const title = v.treks?.find((t) => t.trek.id === id)?.trek.title ?? "The walk";
    (e.currentTarget as HTMLButtonElement).disabled = true;
    try {
      await api.deleteTrek(id);
      again({ treks: v.treks?.filter((t) => t.trek.id !== id) ?? null, confirming: null, message: `${title} was deleted.` });
    } catch (err) {
      again({ confirming: null, message: `${title} wasn't deleted. ${errorText(err)}` });
    }
  });
}

export async function shelfScreen(root: HTMLElement) {
  document.title = "Your shelf · Walkworth";
  const v: View = { treks: null, error: null, confirming: null, message: null };
  render(root, v);
  try {
    render(root, { ...v, treks: await api.treks() });
  } catch (e) {
    render(root, { ...v, error: errorText(e) });
  }
}

