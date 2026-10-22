# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Outbox handlers that turn Phase 3 events into notifications (PRD 14). Idempotent: every
notification carries a dedupe key, because the outbox delivers at-least-once."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.deals import today as today_mod
from app.modules.deals.nextsteps import TASK_ASSIGNED
from app.modules.leads.handlers import _leader
from app.modules.pipeline.engine import events
from app.platform.notifications import service as notifications
from app.platform.outbox.models import OutboxEvent
from app.platform.outbox.service import on_event


def _id(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value else None


async def _owner_and_leader(session: AsyncSession, p: dict[str, str | None]) -> list[uuid.UUID]:
    out: list[uuid.UUID] = []
    if owner := _id(p.get("owner_id")):
        out.append(owner)
    if leader := await _leader(session, _id(p.get("team_id"))):
        out.append(leader)
    return out


@on_event(events.CRITERION_REJECTED)
async def criterion_rejected(session: AsyncSession, event: OutboxEvent) -> None:
    p = event.payload
    if owner := _id(p.get("owner_id")):
        await notifications.notify(
            session,
            event.org_id,
            [owner],
            events.CRITERION_REJECTED,
            "A tick was rejected",
            f"{p['by']} rejected '{p['criterion']}' on {p['company']}: {p.get('reason')}",
            f"/deals/{p['deal_id']}",
            dedupe_key=f"criterion.rejected:{event.id}",
        )


@on_event(events.DEAL_REVIEW_NEEDED)
async def review_needed(session: AsyncSession, event: OutboxEvent) -> None:
    p = event.payload
    await notifications.notify(
        session,
        event.org_id,
        await _owner_and_leader(session, p),
        events.DEAL_REVIEW_NEEDED,
        "A deal needs review",
        f"'{p['criterion']}' on {p['company']} was rejected after the deal moved on.",
        f"/deals/{p['deal_id']}",
        dedupe_key=f"deal.review_needed:{event.id}",
    )


@on_event(events.DEAL_PROOF_MISSING)
async def proof_missing(session: AsyncSession, event: OutboxEvent) -> None:
    p = event.payload
    await notifications.notify(
        session,
        event.org_id,
        await _owner_and_leader(session, p),
        events.DEAL_PROOF_MISSING,
        "Proof is missing on a deal",
        f"{p['company']}: the proof behind {', '.join(p.get('criteria', []))} is gone.",
        f"/deals/{p['deal_id']}",
        dedupe_key=f"deal.proof_missing:{event.id}",
    )


@on_event(events.DEAL_OVERRIDDEN)
async def overridden(session: AsyncSession, event: OutboxEvent) -> None:
    p = event.payload
    owner = _id(p.get("owner_id"))
    if owner and str(owner) != p.get("by_id"):
        await notifications.notify(
            session,
            event.org_id,
            [owner],
            events.DEAL_OVERRIDDEN,
            "Your deal was moved with an override",
            f"{p['by']} moved {p['company']} past unticked criteria.",
            f"/deals/{p['deal_id']}",
            dedupe_key=f"deal.overridden:{event.id}",
        )


@on_event(events.DEAL_WON)
async def won(session: AsyncSession, event: OutboxEvent) -> None:
    p = event.payload
    await notifications.notify(
        session,
        event.org_id,
        await _owner_and_leader(session, p),
        events.DEAL_WON,
        "Deal won",
        f"{p['company']} is won. Onboarding starts.",
        f"/deals/{p['deal_id']}",
        dedupe_key=f"deal.won:{event.id}",
    )


@on_event(events.DEAL_LOST)
async def lost(session: AsyncSession, event: OutboxEvent) -> None:
    p = event.payload
    owner = _id(p.get("owner_id"))
    if owner and str(owner) != p.get("by_id"):
        await notifications.notify(
            session,
            event.org_id,
            [owner],
            events.DEAL_LOST,
            "Your deal was closed as lost",
            f"{p['by']} closed {p['company']} as lost: {p.get('lost_reason')}.",
            f"/deals/{p['deal_id']}",
            dedupe_key=f"deal.lost:{event.id}",
        )


@on_event(events.DEAL_REOPENED)
async def reopened(session: AsyncSession, event: OutboxEvent) -> None:
    p = event.payload
    if owner := _id(p.get("owner_id")):
        await notifications.notify(
            session,
            event.org_id,
            [owner],
            events.DEAL_REOPENED,
            "A lost deal was reopened",
            f"{p['by']} reopened {p['company']}.",
            f"/deals/{p['deal_id']}",
            dedupe_key=f"deal.reopened:{event.id}",
        )


@on_event(TASK_ASSIGNED)
async def task_assigned(session: AsyncSession, event: OutboxEvent) -> None:
    p = event.payload
    await notifications.notify(
        session,
        event.org_id,
        [uuid.UUID(p["user_id"])],
        TASK_ASSIGNED,
        "A task was assigned to you",
        f"{p['by']} asked you to: {p['text']} ({p['company']})",
        f"/deals/{p['deal_id']}",
        dedupe_key=f"task.assigned:{event.id}",
    )


@on_event(today_mod.DIGEST_EVENT)
async def digest(session: AsyncSession, event: OutboxEvent) -> None:
    p = event.payload
    owner = uuid.UUID(p["owner_id"])
    if p.get("nudges"):
        await notifications.notify(
            session,
            event.org_id,
            [owner],
            "nudge.due",
            f"{len(p['nudges'])} customer(s) to nudge",
            ", ".join(n["company"] for n in p["nudges"][:8]),
            "/",
            dedupe_key=f"nudge.due:{owner}:{p['day']}",
        )
    if p.get("stale"):
        await notifications.notify(
            session,
            event.org_id,
            [owner],
            "deal.stale",
            f"{len(p['stale'])} deal(s) gone stale",
            ", ".join(n["company"] for n in p["stale"][:8]),
            "/",
            dedupe_key=f"deal.stale:{owner}:{p['day']}",
        )
