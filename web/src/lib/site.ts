// Site chrome shared by the home page and the shelf: the top bar and the
// footer. The working screens keep their own small nav; Trail mode has none.

import { html, type Raw } from "./html";
import { icons } from "./icons";
import { currentTheme, toggleTheme } from "./theme";
import "../styles/site.css";

export const GITHUB_URL = "https://github.com/ADITYA-KUMAR-2358";
export const SAMPLE_JOURNAL = "/journal/sinhagad-sample";

type Page = "home" | "shelf";

function themeButton(): Raw {
  const dark = currentTheme() === "dark";
  return html`<button class="site-theme" type="button" aria-label="${dark ? "Switch to light mode" : "Switch to dark mode"}">
    ${dark ? icons.sun : icons.moon}
  </button>`;
}

export function siteHeader(active: Page): Raw {
  return html`<header class="site-head">
    <a class="site-brand" href="#/" aria-label="Walkworth home">
      <img src="/icon-192.png" alt="" width="36" height="36">
      <span>Walkworth</span>
    </a>
    <nav class="site-nav" aria-label="Main">
      <a href="#/shelf" ${active === "shelf" ? 'aria-current="page"' : ""}>Your shelf</a>
      <a class="site-nav-wide" href="${SAMPLE_JOURNAL}">Sample journal</a>
      ${themeButton()}
      <a class="site-cta" href="#/prepare">${icons.microphone}<span>Start a walk</span></a>
    </nav>
  </header>`;
}

export function siteFooter(): Raw {
  return html`<footer class="site-foot">
    <div class="site-foot-inner">
      <div class="site-foot-about">
        <a class="site-brand" href="#/" aria-label="Walkworth home">
          <img src="/icon-192.png" alt="" width="32" height="32">
          <span>Walkworth</span>
        </a>
        <p class="site-foot-tag quote">Walk it. Say it. Keep it.</p>
        <p>Built for the DEV Hacktoberfest Open-Source AI Challenge, with Gemma at its core.</p>
      </div>
      <nav class="site-foot-cols" aria-label="Footer">
        <div>
          <h2>Make</h2>
          <a href="#/prepare">Start a walk</a>
          <a href="#/import">Import a walk</a>
          <a href="#/shelf">Your shelf</a>
        </div>
        <div>
          <h2>Read</h2>
          <a href="${SAMPLE_JOURNAL}">Sinhagad sample journal</a>
          <a href="${GITHUB_URL}" rel="noopener">Source on GitHub</a>
        </div>
      </nav>
      <p class="site-foot-credits">
        Sinhagad is a sample trek. Its voice notes are invented, and its photos are by
        Suraj Digrase (CC0) and Tanaya Badgandi (CC BY-SA 4.0) via Wikimedia Commons.
        Map data © OpenStreetMap contributors.
      </p>
    </div>
    <div class="site-foot-base">
      <p class="site-foot-by">Built by Aditya Kumar</p>
      <a class="site-foot-gh" href="${GITHUB_URL}" rel="noopener">${icons.githubLogo}<span>Aditya on GitHub</span></a>
    </div>
  </footer>`;
}

/** Compact bar for the working screens: the mark, one way back, the theme. */
export function appBar(back: { href: string; label: string }, aside: string | Raw = ""): Raw {
  return html`<header class="app-bar">
    <a class="app-bar-brand" href="#/" aria-label="Walkworth home"><img src="/icon-192.png" alt="" width="32" height="32"></a>
    <a class="app-bar-back" href="${back.href}">${icons.arrowLeft}<span>${back.label}</span></a>
    <span class="app-bar-aside">${aside}</span>
    ${themeButton()}
  </header>`;
}

/** Wire up the theme toggle in the site header or the app bar. Call after rendering. */
export function bindSiteHeader(root: HTMLElement) {
  const btn = root.querySelector<HTMLButtonElement>(".site-theme");
  btn?.addEventListener("click", () => {
    toggleTheme();
    btn.outerHTML = themeButton().value;
    bindSiteHeader(root);
    root.querySelector<HTMLButtonElement>(".site-theme")?.focus();
  });
}
