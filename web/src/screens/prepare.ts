// Screen 2: before you go. Pick the fort, allow the mic and location, then one
// big button that starts the trek on the server.

import { fortPack, fortPacks } from "../lib/fortpacks";
import { ago, plural } from "../lib/format";
import { html, type Raw } from "../lib/html";
import { icons } from "../lib/icons";
import { accessCode, ApiError, errorText, setAccessCode, siteHealth } from "../lib/api";
import { appBar, bindSiteHeader } from "../lib/site";
import {
  allWalks, currentWalkId, endWalk, forgetCurrentWalk, getWalk, startWalk, storageUse, waitingUploads, walkNotes, walkPhotos,
} from "../lib/walk";
import type { Walk } from "../lib/idb";

type State = "ok" | "todo" | "blocked";

interface Checks {
  mic: State;
  location: State;
  storage: string;
}

async function permission(name: "microphone" | "geolocation"): Promise<State> {
  try {
    const p = await navigator.permissions.query({ name: name as PermissionName });
    return p.state === "granted" ? "ok" : p.state === "denied" ? "blocked" : "todo";
  } catch {
    return "todo";
  }
}

async function checks(): Promise<Checks> {
  const use = await storageUse();
  const free = use ? use.quota - use.used : null;
  return {
    mic: await permission("microphone"),
    location: await permission("geolocation"),
    storage: free == null ? "Free space unknown" : `${Math.floor(free / 1e6).toLocaleString()} MB free for notes and photos`,
  };
}

function check(state: State, label: string, note: string, action?: Raw): Raw {
  return html`<li class="check ${state === "ok" ? "check--ok" : ""}">
    <span class="step-mark" aria-hidden="true"></span>
    <span>${label}<span class="visually-hidden">${state === "ok" ? ", ready" : ", not ready"}</span></span>
    ${action ?? ""}
    <span class="check-note">${note}</span>
  </li>`;
}

async function walksOnPhone(): Promise<Raw> {
  // Only walks with something still to upload; the rest live on the shelf.
  const all = (await allWalks()).filter((w) => w.endedAt && w.trekId);
  const walks: Walk[] = [];
  for (const w of all) if (await waitingUploads(w.id)) walks.push(w);
  if (!walks.length) return html``;
  const rows = await Promise.all(
    walks.map(async (w: Walk) => {
      const n = (await walkNotes(w.id)).length;
      const p = (await walkPhotos(w.id)).length;
      const day = new Date(w.startedAt).toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
      return html`<li class="check">
        <span class="step-mark" aria-hidden="true"></span>
        <span>${w.title}</span>
        <a class="btn btn--small" href="#/trail/done/${w.id}">Upload</a>
        <span class="check-note">${day} · ${plural(n, "voice note")} · ${plural(p, "photo")}</span>
      </li>`;
    }),
  );
  return html`<h2 class="section-title">Not uploaded yet</h2>
    <p class="mono" style="margin-top:.75rem">These walks have notes or photos still on this phone.</p>
    <ul class="checklist">${rows}</ul>`;
}

/** A walk that never ended (the tab was closed, the phone died). Offer both ways out. */
async function walkInProgress(root: HTMLElement, walk: Walk) {
  const n = (await walkNotes(walk.id)).length;
  const p = (await walkPhotos(walk.id)).length;
  root.innerHTML = html`<main class="screen">
    ${appBar({ href: "#/shelf", label: "Your shelf" })}
    <header class="screen-head">
      <p class="mono eyebrow">Still walking?</p>
      <h1>Your walk is still going</h1>
      <p class="lede">${walk.title}, started ${ago(walk.startedAt)}. ${plural(n, "voice note")} and ${plural(p, "photo")} so far.</p>
    </header>
    <div style="margin-top:2rem">
      <a class="btn btn--primary" href="#/trail"><span>Back to the walk</span>${icons.arrowRight}</a>
    </div>
    <div class="panel">
      <p class="mono">Finished walking?</p>
      <p style="margin-top:.4rem">End it to upload what's left and make the journal. Nothing recorded so far is lost.</p>
      <button class="btn" type="button" id="end" style="margin-top:1rem">End this walk</button>
    </div>
  </main>`.value;
  bindSiteHeader(root);
  root.querySelector("#end")?.addEventListener("click", async () => {
    const ended = await endWalk(walk);
    location.hash = `#/trail/done/${ended.id}`;
  });
}

export async function prepareScreen(root: HTMLElement) {
  document.title = "Prepare · Walkworth";
  const resume = currentWalkId();
  if (resume) {
    const walk = await getWalk(resume);
    if (walk && !walk.endedAt) return walkInProgress(root, walk);
    if (walk) await endWalk(walk);
    else forgetCurrentWalk();
  }
  let packId: string | null = fortPacks[0]?.id ?? null;
  const needsCode = await siteHealth().then((h) => h.access_code).catch(() => false);
  let code = accessCode();
  let status = "";

  async function render() {
    const c = await checks();
    const secure = window.isSecureContext;
    const ready = secure && c.mic === "ok" && (!needsCode || !!code);
    const hint = !secure ? "" : c.mic !== "ok" ? "Allow the microphone first." : needsCode && !code ? "Enter the access code first." : "";
    root.innerHTML = html`<main class="screen">
      ${appBar({ href: "#/shelf", label: "Your shelf" })}
      <header class="screen-head">
        <p class="mono eyebrow">Before you go</p>
        <h1>Get ready to walk</h1>
        <p class="lede">Allow the microphone and location once. On the trail you won't need the screen.</p>
      </header>

      ${secure ? "" : html`<div class="notice" role="alert">
        <p>Recording needs a secure page.</p>
        <p class="mono">Open Walkworth from its https link, not a local network address.</p>
      </div>`}

      <div class="field">
        <label for="pack">Where are you walking?</label>
        <select class="select" id="pack">
          ${fortPacks.map((p) => html`<option value="${p.id}" ${p.id === packId ? "selected" : ""}>${p.name}${p.name_mr ? ` (${p.name_mr})` : ""}</option>`)}
          <option value="" ${packId === null ? "selected" : ""}>Somewhere else</option>
        </select>
      </div>

      ${needsCode ? html`<div class="field">
        <label for="code">Access code</label>
        <input class="input" id="code" value="${code}" autocomplete="off" autocapitalize="off" spellcheck="false">
        <p class="mono">This Walkworth is shared with a code. Ask whoever sent you the link.</p>
      </div>` : ""}

      <ul class="checklist">
        ${check(c.mic, "Microphone allowed",
          c.mic === "blocked" ? "Blocked. Allow the microphone for this site in your browser settings." : "So you can tap and talk.",
          c.mic === "ok" ? undefined : html`<button class="btn btn--small" type="button" data-ask="mic">Allow</button>`)}
        ${check(c.location, "Location allowed",
          c.location === "blocked" ? "Blocked. Notes still save; they'll be placed by time." : "One GPS fix per note, if you allow it.",
          c.location === "ok" ? undefined : html`<button class="btn btn--small" type="button" data-ask="location">Allow</button>`)}
      </ul>
      <p class="mono" style="margin-top:1rem">${c.storage}</p>

      <div class="panel">
        <p class="mono">At the start of the trail</p>
        <p style="margin-top:.4rem">Start a recording in OpenTracks too. It keeps the GPS track while your screen is off. No signal on the way? Notes wait on the phone and upload when you're back in range.</p>
      </div>

      <div style="margin-top:2rem">
        <button class="btn btn--primary" type="button" id="go" ${ready ? "" : "disabled"}>
          <span>Phone in pocket</span>${icons.arrowRight}
        </button>
        <p class="mono" id="go-status" role="status" style="margin-top:.6rem">${status || hint}</p>
      </div>

      ${await walksOnPhone()}
    </main>`.value;
    bindSiteHeader(root);

    root.querySelector<HTMLSelectElement>("#pack")?.addEventListener("change", (e) => {
      packId = (e.target as HTMLSelectElement).value || null;
    });
    const codeInput = root.querySelector<HTMLInputElement>("#code");
    codeInput?.addEventListener("input", () => {
      const had = !!code;
      code = codeInput.value.trim();
      status = "";
      if (had !== !!code) {
        void render().then(() => {
          const again = root.querySelector<HTMLInputElement>("#code");
          again?.focus();
          again?.setSelectionRange(again.value.length, again.value.length);
        });
      }
    });
    root.querySelector('[data-ask="mic"]')?.addEventListener("click", async () => {
      try {
        const s = await navigator.mediaDevices.getUserMedia({ audio: true });
        s.getTracks().forEach((t) => t.stop());
      } catch {
        // Denied: the checklist shows how to fix it.
      }
      render();
    });
    root.querySelector('[data-ask="location"]')?.addEventListener("click", () => {
      navigator.geolocation?.getCurrentPosition(() => render(), () => render(), { timeout: 15_000 });
    });
    root.querySelector<HTMLButtonElement>("#go")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget as HTMLButtonElement;
      const line = root.querySelector<HTMLElement>("#go-status")!;
      const p = fortPack(packId);
      btn.disabled = true;
      line.textContent = "Starting your walk…";
      if (needsCode) setAccessCode(code);
      try {
        await startWalk(p?.name ?? "A walk", p?.name_mr ?? null, p?.id ?? null);
        location.hash = "#/trail";
      } catch (err) {
        status = err instanceof ApiError && err.status ? `Walkworth couldn't start the walk. ${err.message}` : errorText(err);
        if (err instanceof ApiError && err.status === 401) setAccessCode("");
        line.textContent = status;
        btn.disabled = false;
      }
    });
  }

  await render();
}
