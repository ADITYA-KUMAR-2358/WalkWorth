"""Temporal activities. Each one runs a step from steps.py in a thread and
heartbeats while it works, so a killed worker is noticed within seconds and
the activity is retried on the next worker, not after a long timeout."""

from __future__ import annotations

import asyncio
import functools
from typing import Callable

from temporalio import activity

from . import steps

HEARTBEAT_EVERY_S = 3


async def _run(fn: Callable, *args):
    loop = asyncio.get_running_loop()
    task = loop.run_in_executor(None, functools.partial(fn, *args))
    while True:
        done, _ = await asyncio.wait({task}, timeout=HEARTBEAT_EVERY_S)
        if done:
            return task.result()
        activity.heartbeat()


@activity.defn
async def prepare(trek_id: str) -> dict:
    return await _run(steps.prepare, trek_id)


@activity.defn
async def transcribe(note_id: str) -> str:
    return await _run(steps.transcribe_note, note_id)


@activity.defn
async def align(trek_id: str) -> dict:
    return await _run(steps.align, trek_id)


@activity.defn
async def extract(note_id: str) -> str:
    return await _run(steps.extract_note, note_id)


@activity.defn
async def caption(photo_id: str) -> str:
    return await _run(steps.caption_photo, photo_id)


@activity.defn
async def compose(trek_id: str) -> dict:
    return await _run(steps.compose, trek_id)


ALL = [prepare, transcribe, align, extract, caption, compose]
