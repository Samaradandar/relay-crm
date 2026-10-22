# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Outbox: events saved with each change, then dispatched by a worker (at-least-once).

Delivery is at-least-once: a crash after a handler ran but before the row is marked done means
the handler runs again, so every handler must be idempotent (see notifications dedupe keys).
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.time import utc_now
from app.platform.outbox.models import OutboxEvent

log = logging.getLogger("relay.outbox")

MAX_ATTEMPTS = 10
Handler = Callable[[AsyncSession, OutboxEvent], Awaitable[None]]
HANDLERS: dict[str, list[Handler]] = {}


def on_event(*types: str) -> Callable[[Handler], Handler]:
    """Register a handler for one or more event types (use '*' for every event)."""

    def deco(fn: Handler) -> Handler:
        for t in types:
            HANDLERS.setdefault(t, []).append(fn)
        return fn

    return deco


async def enqueue(
    session: AsyncSession, org_id: uuid.UUID, type_: str, payload: dict[str, Any] | None = None
) -> OutboxEvent:
    """Insert an event in the caller's transaction (so it exists iff the change commits)."""
    event = OutboxEvent(org_id=org_id, type=type_, payload=payload or {})
    session.add(event)
    await session.flush()
    return event


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(2**attempts, 3600))


async def _run_handlers(session: AsyncSession, event: OutboxEvent) -> None:
    for handler in [*HANDLERS.get("*", []), *HANDLERS.get(event.type, [])]:
        await handler(session, event)


async def dispatch_batch(factory: async_sessionmaker[AsyncSession], limit: int = 50) -> int:
    """Process due events. Each event runs in its own transaction with its own row lock
    (FOR UPDATE SKIP LOCKED), so several workers never process the same event twice at once.
    Returns the number of events handled (done or failed)."""
    handled = 0
    for _ in range(limit):
        async with factory() as session, session.begin():
            event = (
                await session.execute(
                    select(OutboxEvent)
                    .where(OutboxEvent.status == "pending", OutboxEvent.available_at <= func.now())
                    .order_by(OutboxEvent.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if event is None:
                return handled
            try:
                async with session.begin_nested():
                    await _run_handlers(session, event)
                event.status, event.processed_at = "done", utc_now()
            except Exception as exc:  # noqa: BLE001 - any handler failure is retried
                event.attempts += 1
                event.last_error = f"{type(exc).__name__}: {exc}"[:2000]
                if event.attempts >= MAX_ATTEMPTS:
                    event.status = "failed"
                    log.error("outbox event %s failed permanently: %s", event.id, event.last_error)
                else:
                    event.available_at = utc_now() + backoff(event.attempts)
            handled += 1
    return handled


async def notify_listeners(session: AsyncSession, channel: str, payload: str) -> None:
    await session.execute(text("SELECT pg_notify(:c, :p)"), {"c": channel, "p": payload})


async def list_failed(
    session: AsyncSession, org_id: uuid.UUID, limit: int = 100
) -> list[OutboxEvent]:
    return list(
        (
            await session.execute(
                select(OutboxEvent)
                .where(OutboxEvent.org_id == org_id, OutboxEvent.status == "failed")
                .order_by(OutboxEvent.id.desc())
                .limit(limit)
            )
        ).scalars()
    )


async def retry_failed(session: AsyncSession, org_id: uuid.UUID, event_id: int) -> bool:
    res = await session.execute(
        update(OutboxEvent)
        .where(
            OutboxEvent.id == event_id, OutboxEvent.org_id == org_id, OutboxEvent.status == "failed"
        )
        .values(status="pending", attempts=0, available_at=func.now())
    )
    return (getattr(res, "rowcount", 0) or 0) > 0
