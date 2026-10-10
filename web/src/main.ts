// Self-hosted fonts: no third-party requests.
import "@fontsource-variable/familjen-grotesk/wght.css";
import "@fontsource-variable/source-serif-4/opsz-italic.css";
import "@fontsource/tiro-devanagari-marathi/400.css";

import "./styles/tokens.css";
import "./styles/base.css";

import "./styles/components.css";

import { removeOldServiceWorker } from "./lib/pwa";
import { exportScreen } from "./screens/export";
import { importScreen } from "./screens/import";
import { prepareScreen } from "./screens/prepare";
import { processingScreen } from "./screens/processing";
import { reviewScreen } from "./screens/review";
import { homeScreen } from "./screens/home";
import { shelfScreen } from "./screens/shelf";
import { trailDoneScreen, trailScreen } from "./screens/trail";

const root = document.getElementById("app")!;

type Route = { pattern: RegExp; show: (m: RegExpMatchArray) => void };

const routes: Route[] = [
  { pattern: /^\/?$/, show: () => homeScreen(root) },
  { pattern: /^\/shelf$/, show: () => shelfScreen(root) },
  { pattern: /^\/prepare$/, show: () => prepareScreen(root) },
  { pattern: /^\/trail$/, show: () => trailScreen(root) },
  { pattern: /^\/trail\/done\/([\w-]+)$/, show: (m) => trailDoneScreen(root, m[1]) },
  { pattern: /^\/import$/, show: () => importScreen(root, null) },
  { pattern: /^\/import\/([\w-]+)$/, show: (m) => importScreen(root, m[1]) },
  { pattern: /^\/processing\/([\w-]+)$/, show: (m) => processingScreen(root, m[1]) },
  { pattern: /^\/review\/([\w-]+)$/, show: (m) => reviewScreen(root, m[1]) },
  // The journal is rendered by the server from the same Jinja2 template as the exports.
  { pattern: /^\/journal\/([\w-]+)$/, show: (m) => location.assign(`/journal/${m[1]}`) },
  { pattern: /^\/export\/([\w-]+)$/, show: (m) => exportScreen(root, m[1]) },
];

function navigate() {
  const path = location.hash.replace(/^#/, "") || "/";
  for (const r of routes) {
    const m = path.match(r.pattern);
    if (m) {
      r.show(m);
      window.scrollTo(0, 0);
      return;
    }
  }
  // Unknown link: go back to the shelf.
  location.replace("#/");
}

addEventListener("hashchange", navigate);
removeOldServiceWorker();
navigate();
