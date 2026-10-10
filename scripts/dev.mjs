// Runs the API (:8000), the web app (:5173) and the Temporal dev server
// (:7233, UI on :8233) together. Ctrl+C stops all of them.
//
// The API restarts itself when a backend file changes. We don't use uvicorn's
// --reload: on Windows it stops the old server with a console Ctrl+C, which a
// server started from here (piped output, no console) never receives, so the
// old code kept serving.
import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, watch } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

// Temporal's dev server keeps workflow history in data/temporal.db, so a run
// survives restarts. The worker runs separately (npm run worker) so you can
// kill and restart it to watch the pipeline resume.
const temporalExe = join(root, "tools", "temporal", process.platform === "win32" ? "temporal.exe" : "temporal");
mkdirSync(join(root, "data"), { recursive: true });

const SPECS = {
  api: ["uv", ["run", "--project", "backend", "uvicorn", "walkworth.api:app", "--host", "127.0.0.1", "--port", "8000"]],
  web: ["npm", ["--prefix", "web", "run", "dev"]],
  ...(existsSync(temporalExe)
    ? { temporal: [temporalExe, ["server", "start-dev", "--ip", "127.0.0.1", "--db-filename", join(root, "data", "temporal.db"), "--log-level", "error"]] }
    : {}),
};

const procs = {};
let stopping = false;
const restarting = new Set();

function start(name) {
  const [cmd, args] = SPECS[name];
  const shell = process.platform === "win32" && !cmd.endsWith(".exe");
  const p = spawn(cmd, args, { cwd: root, shell, stdio: ["ignore", "pipe", "pipe"] });
  const tag = `[${name}] `;
  const pipe = (stream, out) => stream.on("data", (b) => out.write(b.toString().replace(/^(?=.)/gm, tag)));
  pipe(p.stdout, process.stdout);
  pipe(p.stderr, process.stderr);
  p.on("exit", (code) => {
    if (stopping || restarting.has(name)) return;
    console.log(`${tag}exited with ${code}`);
    stopAll();
    process.exit(code ?? 0);
  });
  procs[name] = p;
}

// On Windows, killing the shell leaves child processes (uvicorn) holding the
// port, so take down the whole process tree.
function kill(p) {
  if (!p || p.exitCode !== null) return;
  if (process.platform === "win32") spawnSync("taskkill", ["/pid", String(p.pid), "/T", "/F"], { stdio: "ignore" });
  else p.kill();
}

function stopAll() {
  stopping = true;
  Object.values(procs).forEach(kill);
}

function restart(name, why) {
  restarting.add(name);
  console.log(`[${name}] ${why}, restarting`);
  kill(procs[name]);
  setTimeout(() => {
    restarting.delete(name);
    start(name);
  }, 300);
}

Object.keys(SPECS).forEach(start);

let timer = null;
watch(join(root, "backend", "walkworth"), { recursive: true }, (_event, file) => {
  if (!file || file.includes("__pycache__") || !/\.(py|j2|css|js)$/.test(file)) return;
  clearTimeout(timer);
  timer = setTimeout(() => restart("api", `${file} changed`), 400);
});
watch(root, (_event, file) => {
  if (file === ".env" || file === "walkworth.config.toml") {
    clearTimeout(timer);
    timer = setTimeout(() => restart("api", `${file} changed`), 400);
  }
});

for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(sig, () => {
    stopAll();
    process.exit(0);
  });
}
