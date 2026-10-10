"""End-to-end check against a running Walkworth, with the real APIs.

    npm run dev            # API + Temporal (or point --base at Render)
    npm run worker
    npm run e2e            # uses LLM and ElevenLabs credits (~25 calls)

Does what a phone does: starts a walk (creates a trek), uploads each voice
note and photo through /capture with its time and GPS fix, finishes the walk,
adds the GPX, runs "Make the journal" through Temporal, then checks the
journal and builds every export. Logs, the journal HTML and the PDF go to
test/<timestamp>/ (git-ignored).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "fixtures" / "sinhagad-sample"


class Log:
    def __init__(self, path: Path):
        self.f = path.open("w", encoding="utf-8")
        self.failed = 0

    def __call__(self, msg: str = "") -> None:
        print(msg)
        self.f.write(msg + "\n")
        self.f.flush()

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.failed += not ok
        self(f"  {'pass' if ok else 'FAIL'}  {name}{f'  ({detail})' if detail else ''}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--keep", action="store_true", help="keep the test trek afterwards")
    ap.add_argument("--code", default=os.environ.get("ACCESS_CODE", ""), help="the site's ACCESS_CODE, if it has one")
    args = ap.parse_args()

    out = ROOT / "test" / datetime.now().strftime("e2e-%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    log = Log(out / "e2e.log")
    # One client for the whole run: it keeps the owner cookie set when the walk starts.
    c = httpx.Client(base_url=args.base, timeout=120, headers={"X-Access-Code": args.code} if args.code else None)
    log(f"Walkworth e2e against {args.base} at {datetime.now():%Y-%m-%d %H:%M:%S}")

    h = c.get("/api/health").json()
    log(f"models: {h['models']}  keys: llm={h['keys']['llm_key']} elevenlabs={h['keys']['elevenlabs_key']}")
    if not (h["keys"]["llm_key"] and h["keys"]["elevenlabs_key"]):
        log("Both API keys must be set on the server.")
        return 1

    manifest = json.loads((FIX / "manifest.json").read_text(encoding="utf-8"))
    log("\n1. Start a walk (Trail mode)")
    trek = c.post("/api/treks", json={"title": "Sinhagad", "fort_pack_id": "sinhagad"}).json()
    tid = trek["id"]
    log(f"  trek {tid}")

    log("\n2. Capture notes and photos as the phone would")
    for n in manifest["notes"]:
        audio = next(p for p in (FIX / "audio").glob(f"{n['id']}.*"))
        form = {"kind": "note", "t": n["t"], "duration_s": str(n["duration_s"])}
        if n["lat"] is not None:
            form |= {"lat": str(n["lat"]), "lon": str(n["lon"])}
        r = c.post(f"/api/treks/{tid}/capture", data=form, files={"file": (audio.name, audio.read_bytes())}).json()
        log.check(f"note {n['id']} uploaded", r.get("status") == "added", str(r.get("detail") or ""))
    for p in manifest["photos"]:
        path = FIX / p["path"]
        form = {"kind": "photo", "t": p["t"], "lat": str(p["lat"]), "lon": str(p["lon"])}
        r = c.post(f"/api/treks/{tid}/capture", data=form, files={"file": (path.name, path.read_bytes())}).json()
        log.check(f"photo {p['id']} uploaded", r.get("status") == "added", str(r.get("detail") or ""))
    r = c.post(f"/api/treks/{tid}/capture", data={"kind": "note", "t": manifest["notes"][0]["t"]},
               files={"file": ("again.ogg", next((FIX / "audio").glob("note-01.*")).read_bytes())}).json()
    log.check("retried upload is a no-op", r.get("status") == "duplicate")
    fin = c.post(f"/api/treks/{tid}/finish", json={"screen_seconds": 140}).json()
    log.check("walk finished", fin.get("screen_seconds") == 140 and fin.get("ended_at") is not None)

    log("\n3. Import the GPX")
    r = c.post(f"/api/treks/{tid}/files", files=[("files", ("track.gpx", (FIX / "track.gpx").read_bytes()))]).json()
    log.check("GPX imported", r[0]["status"] == "added", r[0].get("detail") or "")

    log("\n4. Make the journal (Temporal)")
    started = time.time()
    st = c.post(f"/api/treks/{tid}/process")
    log.check("process started", st.status_code == 200, st.text[:200] if st.status_code != 200 else "")
    last = ""
    while True:
        s = c.get(f"/api/treks/{tid}/process").json()
        line = "  ".join(f"{x['label']} {x['done']}/{x['total']}" for x in s["steps"] if x["status"] == "running")
        if line and line != last:
            log(f"  {line}")
            last = line
        if s["state"] in ("completed", "failed") or time.time() - started > 900:
            break
        time.sleep(2)
    log(f"  state {s['state']} after {time.time() - started:.0f} s; error: {s['error']}")
    for x in s["steps"]:
        log(f"    {x['label']:24} {x['status']:8} {x['model'] or '':28} {x['detail'] or ''}")
    log.check("pipeline completed", s["state"] == "completed")

    log("\n5. Check the journal")
    notes = c.get(f"/api/treks/{tid}/notes").json()
    photos = c.get(f"/api/treks/{tid}/photos").json()
    lines = c.get(f"/api/treks/{tid}/lines").json()
    log.check("every note transcribed", all(n["transcript"] or n["skipped"] for n in notes))
    log.check("every photo captioned", all(p["caption"] for p in photos))
    log.check("every journal line cites a source", bool(lines) and all(ln["source_ids"] for ln in lines), f"{len(lines)} lines")
    log("  transcripts:")
    for n in notes:
        log(f"    [{n['lang']}, {n['confidence']}] {n['transcript']}")
    log("  captions:")
    for p in photos:
        log(f"    {p['caption']}")
    log("  generated lines:")
    for ln in lines:
        log(f"    {ln['role']:13} {ln['chapter'] or '':8} {ln['text']}  {ln['source_ids']}")
    j = c.get(f"/journal/{tid}")
    log.check("journal page renders", j.status_code == 200 and "Sinhagad" in j.text)
    (out / "journal.html").write_text(j.text, encoding="utf-8")

    log("\n6. Exports")
    for kind in ("pdf", "html", "gpx", "markdown"):
        r = c.post(f"/api/treks/{tid}/exports/{kind}")
        ok = r.status_code == 200
        log.check(f"{kind} export", ok, f"{r.json()['bytes']:,} bytes" if ok else r.text[:200])
        if ok and kind in ("pdf", "gpx", "markdown"):
            data = c.get(r.json()["url"]).content
            (out / r.json()["name"]).write_bytes(data)
    (out / "dev.md").write_text(c.get(f"/api/treks/{tid}/dev-markdown").text, encoding="utf-8")

    if not args.keep:
        c.delete(f"/api/treks/{tid}")
        log(f"\n(test trek {tid} deleted; --keep to keep it)")
    log(f"\n{'ALL PASSED' if not log.failed else f'{log.failed} FAILED'}. Output in {out.relative_to(ROOT)}")
    return 0 if not log.failed else 1


if __name__ == "__main__":
    sys.exit(main())
