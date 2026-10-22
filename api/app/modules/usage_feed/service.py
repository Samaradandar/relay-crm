# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Hourly pull from the voice platform: insert once, draw the ledger, save the cursor last.

Each page is applied in one transaction: every record in it is inserted (duplicates by
`source_record_id` are ignored) and drawn against the ledger, and only once that commits does the
job move the cursor forward. If the process dies before the commit, the next run re-fetches the
same page and finds the same already-committed records to be no-ops (unique constraint), so
nothing is lost or double-counted. Built in: Phase 4.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.time import utc_now
from app.modules.billing.packs import draw
from app.modules.customers.models import Customer
from app.modules.usage_feed.models import UnmatchedUsage, UsageRecord, UsageSyncState

log = logging.getLogger("relay.usage_feed")

PAGE_SIZE = 1000
ALERT_AFTER_FAILURES = 3

Fetcher = Callable[[str | None, int], Awaitable["Page"]]


@dataclass(frozen=True)
class UsageRow:
    source_record_id: str
    voice_customer_id: str
    occurred_at: Any  # datetime, tz-aware
    minutes: Decimal
    revision: int = 1


@dataclass(frozen=True)
class Page:
    records: list[UsageRow]
    next_cursor: str | None


async def _state(session: AsyncSession, org_id: Any) -> UsageSyncState:
    await session.execute(pg_insert(UsageSyncState).values(org_id=org_id).on_conflict_do_nothing())
    row = await session.get(UsageSyncState, org_id, populate_existing=True)
    assert row is not None
    return row


async def _apply_record(session: AsyncSession, org_id: Any, row: UsageRow) -> None:
    customer_id = (
        await session.execute(
            select(Customer.id).where(
                Customer.org_id == org_id,
                Customer.voice_platform_customer_id == row.voice_customer_id,
                Customer.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if customer_id is None:
        await session.execute(
            pg_insert(UnmatchedUsage)
            .values(
                org_id=org_id,
                source_record_id=row.source_record_id,
                voice_customer_id=row.voice_customer_id,
                occurred_at=row.occurred_at,
                minutes=row.minutes,
                revision=row.revision,
            )
            .on_conflict_do_nothing(
                index_elements=[UnmatchedUsage.org_id, UnmatchedUsage.source_record_id]
            )
        )
        return

    existing = (
        await session.execute(
            select(UsageRecord).where(
                UsageRecord.org_id == org_id, UsageRecord.source_record_id == row.source_record_id
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        rec = UsageRecord(
            org_id=org_id,
            customer_id=customer_id,
            source_record_id=row.source_record_id,
            occurred_at=row.occurred_at,
            minutes=row.minutes,
            revision=row.revision,
        )
        session.add(rec)
        await session.flush()
        await draw(session, customer_id, rec.id, row.minutes, kind="draw")
        return

    if row.revision <= existing.revision:
        return  # the same revision seen again: ignored, never double-counted
    diff = row.minutes - existing.minutes
    existing.minutes, existing.revision = row.minutes, row.revision
    if diff != 0:
        await draw(session, customer_id, existing.id, diff, kind="correction")


async def run_sync(
    org_id: Any, factory: async_sessionmaker[AsyncSession], fetch: Fetcher
) -> dict[str, Any]:
    """Pull pages until the feed is caught up, or report the first failure. Each page is one
    commit; the cursor only advances with that commit, never before it."""
    pulled = 0
    async with factory() as s, s.begin():
        state = await _state(s, org_id)
        cursor = state.cursor
    try:
        while True:
            page = await fetch(cursor, PAGE_SIZE)
            if not page.records:
                break
            async with factory() as s, s.begin():
                for row in page.records:
                    await _apply_record(s, org_id, row)
                state = await _state(s, org_id)
                state.cursor = page.next_cursor
                state.last_success_at, state.last_attempt_at = utc_now(), utc_now()
                state.consecutive_failures, state.last_error = 0, None
            pulled += len(page.records)
            cursor = page.next_cursor
            if page.next_cursor is None or len(page.records) < PAGE_SIZE:
                break
    except Exception as exc:  # noqa: BLE001 - recorded, then re-raised for the caller to alert on
        async with factory() as s, s.begin():
            state = await _state(s, org_id)
            state.last_attempt_at = utc_now()
            state.consecutive_failures += 1
            state.last_error = f"{type(exc).__name__}: {exc}"[:2000]
        log.exception("usage sync failed for org %s", org_id)
        raise
    return {"pulled": pulled, "cursor": cursor}
