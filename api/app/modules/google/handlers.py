# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Outbox handlers wiring Phase 3 activity/ownership events to ST-02's one-way calendar sync.
A meeting is only ever synced while it's still in the future -- a past meeting activity (logged
after the fact) was never "booked", so nothing is pushed to Google for it."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.modules.activities import events as activity_events
from app.modules.activities.models import Activity
from app.modules.google.calendar_sync import move_event_to_new_owner, sync_activity_to_google
from app.modules.leads import events as lead_events
from app.modules.leads.models import Deal
from app.platform.notifications import service as notifications
from app.platform.outbox.models import OutboxEvent
from app.platform.outbox.service import on_event

CALENDAR_MOVED_TYPE = "google.calendar_moved"


async def _deal_and_activity(
    session: AsyncSession, payload: dict[str, str]
) -> tuple[Deal, Activity] | None:
    deal_id, activity_id = payload.get("deal_id"), payload.get("activity_id")
    if not deal_id or not activity_id:
        return None
    deal = await session.get(Deal, uuid.UUID(deal_id))
    activity = await session.get(Activity, uuid.UUID(activity_id))
    if deal is None or activity is None or deal.owner_id is None:
        return None
    return deal, activity


@on_event(activity_events.ACTIVITY_LOGGED, activity_events.ACTIVITY_EDITED)
async def activity_changed(session: AsyncSession, event: OutboxEvent) -> None:
    if event.payload.get("type") != "meeting":
        return
    found = await _deal_and_activity(session, event.payload)
    if found is None:
        return
    deal, activity = found
    if activity.occurred_at <= utc_now() or activity.deleted_at is not None:
        return  # not a future meeting (any more) -- nothing to push
    assert deal.owner_id is not None
    company = (event.payload.get("company") or "").strip() or "the deal"
    await sync_activity_to_google(
        session, deal.owner_id, deal.id, deal.account_id, company, activity
    )


@on_event(activity_events.ACTIVITY_DELETED)
async def activity_deleted(session: AsyncSession, event: OutboxEvent) -> None:
    if event.payload.get("type") != "meeting":
        return
    found = await _deal_and_activity(session, event.payload)
    if found is None:
        return
    deal, activity = found
    assert deal.owner_id is not None
    await sync_activity_to_google(
        session, deal.owner_id, deal.id, deal.account_id, "the deal", activity, cancelled=True
    )


@on_event(lead_events.DEAL_OWNER_CHANGED)
async def owner_changed(session: AsyncSession, event: OutboxEvent) -> None:
    """Future linked meetings move to the new owner's calendar; both are notified (PRD)."""
    from sqlalchemy import select

    from app.modules.google.models import GoogleEventLink

    deal_id = event.payload.get("deal_id")
    if not deal_id:
        return
    deal = await session.get(Deal, uuid.UUID(deal_id))
    if deal is None:
        return
    from_id = uuid.UUID(event.payload["from_id"]) if event.payload.get("from_id") else None
    to_id = uuid.UUID(event.payload["to_id"]) if event.payload.get("to_id") else None
    if to_id is None:
        return
    links = (
        await session.execute(
            select(GoogleEventLink).where(
                GoogleEventLink.deal_id == deal.id, GoogleEventLink.status == "active"
            )
        )
    ).scalars().all()  # fmt: skip
    for link in links:
        activity = await session.get(Activity, link.activity_id)
        if activity is None or activity.occurred_at <= utc_now():
            continue
        await move_event_to_new_owner(
            session, activity, deal.id, deal.account_id, event.payload.get("company", "the deal"),
            from_id, to_id,
        )  # fmt: skip
        company = event.payload.get("company", "A deal")
        for uid in (u for u in (from_id, to_id) if u):
            await notifications.notify(
                session, deal.org_id, [uid], CALENDAR_MOVED_TYPE,
                "A meeting moved calendars",
                f"{company}'s meeting moved with the deal's new owner.",
                f"/deals/{deal.id}",
                dedupe_key=f"{CALENDAR_MOVED_TYPE}:{link.activity_id}:{uid}",
            )  # fmt: skip
