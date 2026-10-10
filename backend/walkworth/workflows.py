"""Temporal workflows.

ProcessTrekWorkflow runs the whole post-trek pipeline. Each step is an
activity with retries; transcription and captioning fan out in parallel. If
the worker dies, finished activities are not redone: Temporal replays their
results from history and only the unfinished ones run again.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

with workflow.unsafe.imports_passed_through():
    from walkworth import activities

TASK_QUEUE = "walkworth"

# Activity code (HTTP clients, SQLite) never runs inside the workflow
# sandbox, so let those modules through instead of re-importing them there.
PASSTHROUGH = ["walkworth.activities", "walkworth.steps", "walkworth.config", "walkworth.db", "walkworth.schema",
               "walkworth.llm", "walkworth.stt", "walkworth.gpx", "walkworth.fortpack", "walkworth.media",
               "pydantic", "httpx", "PIL"]


def runner() -> SandboxedWorkflowRunner:
    return SandboxedWorkflowRunner(restrictions=SandboxRestrictions.default.with_passthrough_modules(*PASSTHROUGH))

RETRY = RetryPolicy(initial_interval=timedelta(seconds=2), backoff_coefficient=2.0, maximum_attempts=3)


def _opts(minutes: int) -> dict:
    return {
        "start_to_close_timeout": timedelta(minutes=minutes),
        "heartbeat_timeout": timedelta(seconds=20),
        "retry_policy": RETRY,
    }


STEPS = [
    ("import", "Import"),
    ("transcribe", "Transcribe"),
    ("align", "Align to the trail"),
    ("extract", "Pick out what you saw"),
    ("caption", "Caption photos"),
    ("compose", "Write the journal"),
]


@workflow.defn
class ProcessTrekWorkflow:
    def __init__(self) -> None:
        self.steps = {k: {"key": k, "label": label, "status": "waiting", "done": 0, "total": 0, "model": None, "detail": None}
                      for k, label in STEPS}

    @workflow.query
    def progress(self) -> list[dict]:
        return list(self.steps.values())

    def _start(self, key: str, total: int, model: str | None = None) -> None:
        self.steps[key].update(status="running", total=total, done=0, model=model)

    def _finish(self, key: str, detail: str | None = None) -> None:
        self.steps[key].update(status="done", done=self.steps[key]["total"], detail=detail)

    async def _fan_out(self, key: str, fn, ids: list[str], minutes: int) -> None:
        async def one(i: str) -> None:
            await workflow.execute_activity(fn, i, **_opts(minutes))
            self.steps[key]["done"] += 1

        await asyncio.gather(*(one(i) for i in ids))

    @workflow.run
    async def run(self, trek_id: str, models: dict) -> dict:
        try:
            self._start("import", 1)
            plan = await workflow.execute_activity(activities.prepare, trek_id, **_opts(2))
            self._finish("import", f"{'GPX' if plan['track_points'] else 'No GPX'}, {len(plan['notes'])} notes, {len(plan['photos'])} photos")

            self._start("transcribe", len(plan["to_transcribe"]), f"ElevenLabs {models['stt']}")
            await self._fan_out("transcribe", activities.transcribe, plan["to_transcribe"], 15)
            self._finish("transcribe")

            self._start("align", 1, None)
            aligned = await workflow.execute_activity(activities.align, trek_id, **_opts(2))
            self._finish("align", "GPX and fort pack" if aligned["track_points"] else "Note GPS fixes only (approximate)")

            self._start("extract", len(plan["notes"]), models["llm"])
            await self._fan_out("extract", activities.extract, plan["notes"], 10)
            self._finish("extract")

            self._start("caption", len(plan["photos"]), f"{models['vision']} vision")
            await self._fan_out("caption", activities.caption, plan["photos"], 10)
            self._finish("caption")

            self._start("compose", 1, f"{models['llm']}, grounded")
            result = await workflow.execute_activity(activities.compose, trek_id, **_opts(15))
            self._finish("compose", f"{result['lines']} lines kept, {result['dropped_unsourced']} dropped by the grounding check")
            return result
        except Exception:
            for step in self.steps.values():
                if step["status"] == "running":
                    step["status"] = "failed"
            raise


@workflow.defn
class RecomposeWorkflow:
    """After edits in Review: rerun only the compose step."""

    def __init__(self) -> None:
        self.status = {"key": "compose", "label": "Write the journal", "status": "running", "done": 0, "total": 1,
                       "model": None, "detail": None}

    @workflow.query
    def progress(self) -> list[dict]:
        return [self.status]

    @workflow.run
    async def run(self, trek_id: str, models: dict) -> dict:
        self.status["model"] = f"{models['llm']}, grounded"
        result = await workflow.execute_activity(activities.compose, trek_id, **_opts(15))
        self.status.update(status="done", done=1)
        return result
