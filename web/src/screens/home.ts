// Home: what Walkworth does, told with the Sinhagad sample. The quotes and
// journal lines below are the sample's real transcripts and what the pipeline
// wrote from them; stats and the route come live from the API.

import { api } from "../lib/api";
import { km, metres } from "../lib/format";
import { html, type Raw } from "../lib/html";
import { icons } from "../lib/icons";
import { contours, routeMap } from "../lib/route-svg";
import { SAMPLE_JOURNAL, bindSiteHeader, siteFooter, siteHeader } from "../lib/site";
import type { TrekSummary } from "../types/schema";
import "./home.css";

const photo = (name: string) => `/home/${name}.jpg`;

/** Sample notes as they were spoken, and what each became in the journal. */
const SAID = [
  { id: "01", at: "06:12", text: "Started from Atkarwadi, still dark, chai stall just opening." },
  { id: "03", at: "06:48", text: "Sun's up behind us. The whole valley went orange for about a minute." },
  { id: "06", at: "07:48", text: "Fog rolling in from the valley, can't see the road anymore." },
];

function hero(): Raw {
  return html`<section class="home-hero">
    <div class="home-hero-copy">
      <h1>Talk on the trail. Keep it as a journal.</h1>
      <p>Phone in your pocket, you speak. Walkworth turns your voice notes, photos and route into a journal you can keep.</p>
      <div class="home-actions">
        <a class="pill pill--gold" href="#/prepare">${icons.microphone}<span>Start a walk</span></a>
        <a class="pill" href="${SAMPLE_JOURNAL}">${icons.bookOpen}<span>Read the sample journal</span></a>
      </div>
    </div>
    <figure class="home-prints">
      <div class="prints">
        <img class="print print--back" src="${photo("sunrise")}" alt="Sunrise over the Sahyadri ridges from Sinhagad" width="1400" height="932">
        <img class="print print--mid" src="${photo("gate")}" alt="A stone gate in the Sinhagad fort wall" width="788" height="1400">
        <img class="print print--front" src="${photo("fog-edge")}" alt="Fog over the cliff edge at Sinhagad" width="788" height="1400">
      </div>
      <figcaption>Sinhagad sample photos by Suraj Digrase (CC0) and Tanaya Badgandi (CC BY-SA 4.0), Wikimedia Commons.</figcaption>
    </figure>
  </section>`;
}

function saidBecomes(): Raw {
  return html`<section class="home-section said">
    <h2>What you said becomes the journal</h2>
    <p class="home-lede">Your words stay exactly as spoken. Gemma writes only the small lines between them, and every line names the note it came from.</p>
    <div class="said-grid">
      <div class="said-panel">
        <p class="said-head">${icons.microphone}<span>On the trail</span></p>
        <ol class="said-notes">
          ${SAID.map((n) => html`<li data-note="${n.id}" tabindex="0">
            <span class="said-time">${n.at}</span>
            <span class="said-text">${n.text}</span>
          </li>`)}
        </ol>
      </div>
      <span class="said-arrow" aria-hidden="true">${icons.arrowRight}</span>
      <article class="said-panel said-journal" aria-label="The same notes in the journal">
        <p class="said-head">${icons.bookOpen}<span>In the journal</span></p>
        <p class="said-line">Walked 8.6 km at Sinhagad. <span class="cite">cites the GPX</span></p>
        <h3>Starting in the dark</h3>
        <blockquote data-note="01">Started from Atkarwadi, still dark, chai stall just opening.</blockquote>
        <blockquote data-note="03">The whole valley went orange for about a minute.</blockquote>
        <p class="said-line" data-note="06">Fog rolls in from the valley until the road is gone. <span class="cite">cites note 06</span></p>
      </article>
    </div>
  </section>`;
}

function howItWorks(route: TrekSummary["route"] | null): Raw {
  return html`<section class="how">
    <div class="how-contours">${route ? contours(route, 9) : ""}</div>
    <div class="how-inner">
      <h2>How it works</h2>
      <ol class="how-steps">
        <li>
          <span class="how-num">1</span>
          <h3>Walk and talk</h3>
          <p>Tap once and speak. The phone buzzes when it listens, so it can stay in your pocket.</p>
        </li>
        <li>
          <span class="how-num">2</span>
          <h3>It uploads as you go</h3>
          <p>Each note and photo goes up with its time and GPS fix. Add the OpenTracks GPX when you're home.</p>
        </li>
        <li>
          <span class="how-num">3</span>
          <h3>Make the journal</h3>
          <p>ElevenLabs writes down what you said, Gemma reads it, and you fix anything before you print.</p>
        </li>
      </ol>
    </div>
  </section>`;
}

function features(route: TrekSummary["route"] | null): Raw {
  return html`<section class="home-section">
    <h2>Made for walking, not for looking at a screen</h2>
    <div class="bento">
      <figure class="bento-cell bento-words">
        <img src="${photo("fog-edge")}" alt="" loading="lazy">
        <div class="bento-words-body">
          <h3>Your words, kept</h3>
          <blockquote>Fog rolling in from the valley, can't see the road anymore.</blockquote>
          <p>Every quote is checked word for word against what you said. It's never rewritten.</p>
        </div>
        <figcaption>Photo: Suraj Digrase, CC0</figcaption>
      </figure>
      <div class="bento-cell bento-honest">
        <p class="struck"><s>Peacocks danced in the golden rain.</s></p>
        <p class="struck-why">Dropped. It cites nothing you said.</p>
        <h3>Nothing invented</h3>
        <p>A line that can't point to a note, a photo or the route is removed before the journal is made.</p>
      </div>
      <div class="bento-cell bento-pocket">
        <span class="bento-icon">${icons.footprints}</span>
        <h3>Phone in your pocket</h3>
        <p>One big button. It buzzes when it starts listening and again when it stops.</p>
      </div>
      <div class="bento-cell bento-route">
        <div class="bento-route-map">${route ? routeMap(route, 360, 260) : html`<span class="bento-icon">${icons.mapPin}</span>`}</div>
        <div>
          <h3>Every note on the route</h3>
          <p>Notes and photos land where you were, from your phone's GPS or the GPX track. The gold dot is the high point.</p>
        </div>
      </div>
    </div>
  </section>`;
}

function band(): Raw {
  return html`<figure class="band">
    <img src="${photo("sunrise")}" alt="Sunrise over the Sahyadri ridges, seen from Sinhagad" loading="lazy">
    <blockquote>Sun's up behind us. The whole valley went orange for about a minute.</blockquote>
    <figcaption>Sunrise from Sinhagad. Photo: Tanaya Badgandi, CC BY-SA 4.0, via Wikimedia Commons.</figcaption>
  </figure>`;
}

function sample(s: TrekSummary | null): Raw {
  const stats = s?.stats
    ? [
        [km(s.stats.distance_m), "walked"],
        [metres(s.stats.gain_m), "climbed"],
        [`${Math.floor(s.stats.total_s / 3600)} h ${Math.round((s.stats.total_s % 3600) / 60)} min`, "on the trail"],
        [`${s.note_count}`, "voice notes"],
      ]
    : [];
  return html`<section class="home-section">
    <a class="sample" href="${SAMPLE_JOURNAL}">
      <img src="${photo("ridge-trail")}" alt="The trail along the ridge to Sinhagad" loading="lazy">
      <div class="sample-body">
        <span class="chip">Sample data</span>
        <h2>Sinhagad <span lang="mr">सिंहगड</span></h2>
        <p>A sample walk up the Sinhagad Trail, from Atkarwadi to the top and back. The route follows OpenStreetMap; the voice notes are invented.</p>
        ${stats.length ? html`<dl class="sample-stats">${stats.map(([v, l]) => html`<div><dt>${l}</dt><dd>${v}</dd></div>`)}</dl>` : ""}
        <span class="pill pill--gold">${icons.bookOpen}<span>Read the sample journal</span></span>
      </div>
    </a>
    <p class="sample-credit">Photo: Suraj Digrase, CC0, via Wikimedia Commons.</p>
  </section>`;
}

function closing(): Raw {
  return html`<section class="closing">
    <h2>The next time you walk, press record.</h2>
    <p>It takes one tap to start. The journal grows one note at a time.</p>
    <div class="home-actions">
      <a class="pill pill--gold" href="#/prepare">${icons.microphone}<span>Start a walk</span></a>
      <a class="pill" href="#/import">${icons.uploadSimple}<span>Import a walk</span></a>
    </div>
  </section>`;
}

/** Hovering a spoken note lights up what it became in the journal. */
function bindPairs(root: HTMLElement) {
  const said = root.querySelector<HTMLElement>(".said");
  if (!said) return;
  const light = (id: string | null) =>
    said.querySelectorAll<HTMLElement>("[data-note]").forEach((el) => el.classList.toggle("is-lit", el.dataset.note === id));
  said.querySelectorAll<HTMLElement>(".said-notes [data-note]").forEach((el) => {
    el.addEventListener("mouseenter", () => light(el.dataset.note ?? null));
    el.addEventListener("focus", () => light(el.dataset.note ?? null));
    el.addEventListener("mouseleave", () => light(null));
    el.addEventListener("blur", () => light(null));
  });
}

export async function homeScreen(root: HTMLElement) {
  document.title = "Walkworth: a spoken trek journal";
  let sampleTrek: TrekSummary | null = null;

  const render = () => {
    root.innerHTML = html`<div class="home">
      ${siteHeader("home")}
      <main>
        ${hero()}
        ${saidBecomes()}
        ${howItWorks(sampleTrek?.route ?? null)}
        ${features(sampleTrek?.route ?? null)}
        ${band()}
        ${sample(sampleTrek)}
        ${closing()}
      </main>
      ${siteFooter()}
    </div>`.value;
    bindSiteHeader(root);
    bindPairs(root);
  };

  render();
  // Live stats and route for the sample; the page reads fine without them.
  try {
    sampleTrek = await api.trek("sinhagad-sample");
    if (location.hash.replace(/^#/, "") === "/" || location.hash === "") render();
  } catch {
    // API unreachable: the copy and photos still tell the story.
  }
}
