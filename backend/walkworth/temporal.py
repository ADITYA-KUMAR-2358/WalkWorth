"""Temporal client helpers. Local dev server by default; Temporal Cloud when
TEMPORAL_ADDRESS and TEMPORAL_API_KEY are set."""

from __future__ import annotations

from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.service import RPCError

from . import config
from .schema import PipelineStep, ProcessStatus
from .workflows import STEPS, TASK_QUEUE, ProcessTrekWorkflow, RecomposeWorkflow

_client: Client | None = None
# Last progress each workflow reported, shown while no worker can answer.
_last_steps: dict[str, list[PipelineStep]] = {}


async def client() -> Client:
    global _client
    if _client is None:
        cfg = config.load()
        if cfg.temporal_api_key:
            _client = await Client.connect(cfg.temporal_address, namespace=cfg.temporal_namespace,
                                           api_key=cfg.temporal_api_key, tls=True)
        else:
            _client = await Client.connect(cfg.temporal_address, namespace=cfg.temporal_namespace)
    return _client


def models() -> dict:
    cfg = config.load()
    return {"llm": cfg.llm_model, "vision": cfg.vision_model, "stt": cfg.stt_model}


def process_id(trek_id: str) -> str:
    return f"process-{trek_id}"


def recompose_id(trek_id: str) -> str:
    return f"recompose-{trek_id}"


async def start_process(trek_id: str) -> str:
    c = await client()
    handle = await c.start_workflow(
        ProcessTrekWorkflow.run, args=[trek_id, models()], id=process_id(trek_id), task_queue=TASK_QUEUE,
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
    return handle.id


async def start_recompose(trek_id: str) -> str:
    c = await client()
    handle = await c.start_workflow(
        RecomposeWorkflow.run, args=[trek_id, models()], id=recompose_id(trek_id), task_queue=TASK_QUEUE,
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
    )
    return handle.id


STATE = {
    WorkflowExecutionStatus.RUNNING: "running",
    WorkflowExecutionStatus.COMPLETED: "completed",
}


async def status(trek_id: str, which: str = "process") -> ProcessStatus:
    wid = process_id(trek_id) if which == "process" else recompose_id(trek_id)
    blank = [PipelineStep(key=k, label=label) for k, label in STEPS]
    try:
        c = await client()
    except Exception as e:  # noqa: BLE001
        return ProcessStatus(state="failed", steps=blank,
                             error=f"Temporal isn't reachable at {config.load().temporal_address}. Locally, start it with npm run dev. ({e.__class__.__name__})")
    handle = c.get_workflow_handle(wid)
    try:
        desc = await handle.describe()
    except RPCError:
        return ProcessStatus(state="not_started", steps=blank)
    state = STATE.get(desc.status, "failed")
    worker_gone = False
    try:
        steps = [PipelineStep(**s) for s in await handle.query("progress")]
        _last_steps[wid] = steps
    except RPCError:
        # No worker to answer the query (e.g. it was killed). Show what we knew.
        steps = _last_steps.get(wid, blank)
        worker_gone = True
    error = None
    if state == "failed":
        error = f"The workflow ended with status {desc.status.name.lower()}."
        try:
            await handle.result()
        except Exception as e:  # noqa: BLE001
            # Workflow failure -> activity error -> the step's own exception.
            cause: BaseException = e
            while cause.__cause__ is not None:
                cause = cause.__cause__
            error = str(getattr(cause, "message", None) or cause)
    elif state == "running" and worker_gone:
        error = "Waiting for the worker. Start it with npm run worker; finished steps won't run again."
    return ProcessStatus(workflow_id=wid, state=state, steps=steps, error=error)
