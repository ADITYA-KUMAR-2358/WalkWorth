// npm test: backend unit/API tests, web typecheck and build. No API keys or
// network needed. The log is saved to test/unit-<timestamp>.log (git-ignored).
import { spawnSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const stamp = new Date().toISOString().replace(/[-:]/g, "").replace("T", "-").slice(0, 15);
const log = [];
let failed = 0;

for (const [name, cmd, args, cwd] of [
  ["backend tests", "uv", ["run", "python", "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"], join(root, "backend")],
  ["web typecheck", "npm", ["--prefix", "web", "run", "typecheck"], root],
  ["web build", "npm", ["--prefix", "web", "run", "build"], root],
]) {
  const r = spawnSync(cmd, args, { cwd, shell: process.platform === "win32", encoding: "utf8" });
  const ok = r.status === 0;
  failed += ok ? 0 : 1;
  const out = `${r.stdout ?? ""}${r.stderr ?? ""}`;
  log.push(`==== ${name}: ${ok ? "pass" : "FAIL"}\n${out}`);
  console.log(`${ok ? "pass" : "FAIL"}  ${name}`);
  if (!ok) console.log(out.split("\n").slice(-30).join("\n"));
}
mkdirSync(join(root, "test"), { recursive: true });
const file = join(root, "test", `unit-${stamp}.log`);
writeFileSync(file, log.join("\n"));
console.log(`\n${failed ? `${failed} failed` : "all passed"}. Log: test/unit-${stamp}.log`);
process.exit(failed ? 1 : 0);
