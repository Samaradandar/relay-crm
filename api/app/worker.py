# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Starts the Procrastinate worker and scheduled jobs.

Two things run side by side: the outbox dispatcher loop (drains events within about a second,
woken instantly by NOTIFY when possible) and the Procrastinate worker for the schedules in
app.platform.jobs.app (daily approval reminder, nightly audit chain check, minute-level safety net).
"""

import asyncio
import contextlib
import logging
import sys

from app.core.db import dispose_engine, get_sessionmaker
from app.platform import registry
from app.platform.jobs.app import app as jobs_app
from app.platform.outbox.service import dispatch_batch

if sys.platform == "win32":  # psycopg (job queue) needs the selector event loop
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("relay.worker")


async def outbox_loop(stop: asyncio.Event, interval: float = 1.0) -> None:
    while not stop.is_set():
        try:
            handled = await dispatch_batch(get_sessionmaker())
        except Exception:  # noqa: BLE001 - keep the worker alive, the next tick retries
            log.exception("outbox dispatch failed")
            handled = 0
        if handled == 0:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=interval)


async def main() -> None:
    registry.setup()
    stop = asyncio.Event()
    loop_task = asyncio.create_task(outbox_loop(stop))
    log.info("worker started")
    try:
        async with jobs_app.open_async():
            await jobs_app.run_worker_async(install_signal_handlers=True)
    finally:
        stop.set()
        await loop_task
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
