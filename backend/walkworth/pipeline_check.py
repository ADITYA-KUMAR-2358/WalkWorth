"""npm run pipeline-check

Runs the whole pipeline on the sample trek in a throwaway Temporal dev server,
against the real APIs (Gemma on the Gemini API, ElevenLabs Scribe), then checks
the results: every note transcribed, every photo captioned, every journal line
sourced, and the PDF built.

Needs LLM_API_KEY and ELEVENLABS_API_KEY in .env, and the Temporal CLI in
tools/temporal. Uses API credits: 8 short transcriptions, about 25 Gemma calls.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from . import activities, config, db, fixtures, render
from .schema import GeneratedLine, Photo, VoiceNote
from .workflows import ProcessTrekWorkflow, runner

TREK = "sinhagad-sample"


def wer(ref: str, hyp: str) -> float:
    r = re.findall(r"[a-z0-9']+", ref.lower())
    h = re.findall(r"[a-z0-9']+", hyp.lower())
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, d[j] = d[j], cur
    return d[len(h)] / max(1, len(r))


async def main() -> int:
    cfg = config.load()
    missing = [k for k, v in (("LLM_API_KEY", cfg.llm_api_key), ("ELEVENLABS_API_KEY", cfg.elevenlabs_api_key)) if not v]
    if missing:
        print(f"Set {' and '.join(missing)} in .env first.")
        return 1
    print(f"Pipeline check: {cfg.llm_model}, ElevenLabs {cfg.stt_model}.\n")
    fixtures.load()

    exe = config.REPO_ROOT / "tools" / "temporal" / ("temporal.exe" if sys.platform == "win32" else "temporal")
    if not exe.is_file():
        print("Temporal CLI not found in tools/temporal. Put temporal.exe (or temporal) there.")
        return 1
    started = time.time()
    async with await WorkflowEnvironment.start_local(dev_server_existing_path=str(exe), ip="127.0.0.1") as env:
        queue = "walkworth-pipeline-check"
        with ThreadPoolExecutor(max_workers=4) as pool:
            async with Worker(env.client, task_queue=queue, workflows=[ProcessTrekWorkflow],
                              activities=activities.ALL, activity_executor=pool, max_concurrent_activities=2,
                              workflow_runner=runner()):
                models = {"llm": cfg.llm_model, "vision": cfg.vision_model, "stt": cfg.stt_model}
                handle = await env.client.start_workflow(
                    ProcessTrekWorkflow.run, args=[TREK, models], id=f"pipeline-check-{uuid.uuid4().hex[:6]}", task_queue=queue)
                last = ""
                waiting = asyncio.create_task(handle.result())
                while True:
                    done, _ = await asyncio.wait({waiting}, timeout=2)
                    if done:
                        result = waiting.result()
                        break
                    steps = await handle.query("progress")
                    line = "  ".join(f"{s['label']} {s['done']}/{s['total']}" for s in steps if s["status"] == "running")
                    if line and line != last:
                        print(f"  {line}")
                        last = line
    elapsed = time.time() - started

    manifest = json.loads((fixtures.FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    scripts = {n["id"]: n["script"] for n in manifest["notes"]}
    with db.connect() as conn:
        notes = db.fetch(conn, VoiceNote, "trek_id = ?", (TREK,), "t")
        photos = db.fetch(conn, Photo, "trek_id = ?", (TREK,))
        lines = db.fetch(conn, GeneratedLine, "trek_id = ?", (TREK,))
    pdf = render.export_pdf(TREK)

    errors = [wer(scripts[n.id], n.transcript or "") for n in notes if n.id in scripts]
    checks = [
        ("every note transcribed", all(n.transcript or n.skipped for n in notes)),
        ("every note read by Gemma", all(n.quote for n in notes if n.transcript)),
        ("every photo captioned", all(p.caption for p in photos)),
        ("journal composed", result["entries"] > 0),
        ("every journal line has a source", all(ln.source_ids for ln in lines)),
        ("PDF exported", pdf.bytes > 0),
    ]
    print(f"\nFinished in {elapsed:.0f} s. {result['lines']} journal lines kept, "
          f"{result['dropped_unsourced']} dropped by the grounding check.")
    print(f"Word error rate against the sample scripts: {sum(errors) / max(1, len(errors)):.1%}")
    for name, ok in checks:
        print(f"  {'pass' if ok else 'FAIL'}  {name}")
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
