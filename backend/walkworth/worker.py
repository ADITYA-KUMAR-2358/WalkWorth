"""The Temporal worker that runs the pipeline.

    npm run worker

Kill it mid-run and start it again: finished steps are not redone.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from temporalio.worker import Worker

from . import activities, temporal
from .workflows import TASK_QUEUE, ProcessTrekWorkflow, RecomposeWorkflow, runner


async def run(stop: asyncio.Event | None = None) -> None:
    client = await temporal.client()
    # Two at a time keeps us under the free-tier rate limits of both APIs.
    with ThreadPoolExecutor(max_workers=4) as pool:
        worker = Worker(
            client,
            task_queue=TASK_QUEUE,
            workflows=[ProcessTrekWorkflow, RecomposeWorkflow],
            activities=activities.ALL,
            activity_executor=pool,
            max_concurrent_activities=2,
            workflow_runner=runner(),
        )
        if stop is None:
            print(f"Walkworth worker listening on task queue '{TASK_QUEUE}'.")
            await worker.run()
        else:
            async with worker:
                await stop.wait()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(run())
