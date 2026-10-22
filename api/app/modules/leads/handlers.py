# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Outbox handlers for Phase 2: the assignment engine safety net, retries and notifications.

Handlers are idempotent (dedupe keys): the outbox delivers at-least-once.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker
from app.modules.leads import events
from app.modules.leads.assignment.service import assign_pooled, retry_waiting
from app.modules.users.models import Team, User
from app.platform.approvals.models import ApprovalRequest
from app.platform.approvals.service import eligible_approvers
from app.platform.notifications import service as notifications
from app.platform.outbox.models import OutboxEvent
from app.platform.outbox.service import on_event
from app.platform.permissions.roles import Role


async def _admins(session: AsyncSession, org_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        (
            await session.execute(
                select(User.id).where(
                    User.org_id == org_id,
                    User.is_active.is_(True),
                    User.role.in_([Role.ADMIN, Role.SUPER_ADMIN]),
                )
            )
        ).scalars()
    )


async def _leader(session: AsyncSession, team_id: uuid.UUID | None) -> uuid.UUID | None:
    if team_id is None:
        return None
    return (
        await session.execute(select(Team.leader_id).where(Team.id == team_id))
    ).scalar_one_or_none()


@on_event(events.DEAL_POOLED)
async def engine_safety_net(_: AsyncSession, event: OutboxEvent) -> None:
    """The router runs the engine inline; this catches anything it missed. Idempotent: a deal that
    already has an owner is skipped by `assign_pooled`."""
    await assign_pooled(event.org_id, uuid.UUID(event.payload["deal_id"]))


@on_event(*events.RETRY_TRIGGERS)
async def retry_on_change(_: AsyncSession, event: OutboxEvent) -> None:
    await retry_waiting(event.org_id, get_sessionmaker())


@on_event(events.LEAD_ASSIGNED)
async def lead_assigned(session: AsyncSession, event: OutboxEvent) -> None:
    p = event.payload
    await notifications.notify(
        session,
        event.org_id,
        [uuid.UUID(p["owner_id"])],
        events.LEAD_ASSIGNED,
        "New lead assigned to you",
        f"{p['company']} was assigned to you.",
        f"/deals/{p['deal_id']}",
        dedupe_key=f"lead.assigned:{event.id}",
    )


@on_event(events.LEAD_WAITING)
async def lead_waiting(session: AsyncSession, event: OutboxEvent) -> None:
    p = event.payload
    team_id = uuid.UUID(p["team_id"]) if p.get("team_id") else None
    leader = await _leader(session, team_id)
    recipients = [leader] if leader else await _admins(session, event.org_id)
    reason = str(p.get("reason") or "no one is eligible")
    await notifications.notify(
        session,
        event.org_id,
        recipients,
        events.LEAD_WAITING,
        "A lead is waiting in the Pool",
        f"{p['company']} could not be assigned: {reason}. It stays in the Pool and is retried.",
        "/pool",
        dedupe_key=f"lead.waiting:{p['deal_id']}",
    )


@on_event(events.REP_LEAD_WAITING)
async def rep_lead_waiting(session: AsyncSession, event: OutboxEvent) -> None:
    """A rep's new lead waits for the Team Leader: tell the leader once and the rep once (the
    dedupe keys make the outbox's at-least-once delivery safe)."""
    p = event.payload
    team_id = uuid.UUID(p["team_id"]) if p.get("team_id") else None
    leader = await _leader(session, team_id)
    recipients = [leader] if leader else await _admins(session, event.org_id)
    await notifications.notify(
        session,
        event.org_id,
        recipients,
        events.REP_LEAD_WAITING,
        "A rep's lead needs your decision",
        f"{p['creator_name']} added {p['company']}. Give it to them, pick another rep, or let "
        "round robin assign it.",
        "/pool",
        dedupe_key=f"lead.rep_waiting:{p['deal_id']}",
    )
    await notifications.notify(
        session,
        event.org_id,
        [uuid.UUID(p["creator_id"])],
        "lead.rep_waiting.creator",
        "Your lead is waiting for your Team Leader",
        f"{p['company']} was sent to your Team Leader to assign.",
        "/leads/new",
        dedupe_key=f"lead.rep_waiting.creator:{p['deal_id']}",
    )


@on_event(events.DEAL_OWNER_CHANGED)
async def deal_owner_changed(session: AsyncSession, event: OutboxEvent) -> None:
    p = event.payload
    link = f"/deals/{p['deal_id']}"
    if p.get("to_id"):
        await notifications.notify(
            session,
            event.org_id,
            [uuid.UUID(p["to_id"])],
            events.DEAL_OWNER_CHANGED,
            "A deal was given to you",
            f"{p['company']} is now yours.",
            link,
            dedupe_key=f"deal.owner_changed.to:{event.id}",
        )
    if p.get("from_id"):
        await notifications.notify(
            session,
            event.org_id,
            [uuid.UUID(p["from_id"])],
            events.DEAL_OWNER_CHANGED,
            "A deal moved away from you",
            f"{p['company']} is no longer assigned to you.",
            "/",
            dedupe_key=f"deal.owner_changed.from:{event.id}",
        )


@on_event(events.DUPLICATE_OUTSIDE_SCOPE)
async def duplicate_outside_scope(session: AsyncSession, event: OutboxEvent) -> None:
    p = event.payload
    await notifications.notify(
        session,
        event.org_id,
        await _admins(session, event.org_id),
        events.DUPLICATE_OUTSIDE_SCOPE,
        "Possible duplicate created",
        f"{p['by']} chose Create anyway for {p['company']}, which resembles a company "
        "in another team.",
        f"/deals/{p['deal_id']}",
        dedupe_key=f"duplicate.outside:{p['deal_id']}",
    )


@on_event(events.TRANSFER_REQUESTED)
async def transfer_requested(session: AsyncSession, event: OutboxEvent) -> None:
    p = event.payload
    if not p.get("other_leader_id"):
        return
    await notifications.notify(
        session,
        event.org_id,
        [uuid.UUID(p["other_leader_id"])],
        events.TRANSFER_REQUESTED,
        "Transfer request involving your team",
        f"{p['summary']}. You can add a comment for the Admin.",
        "/transfers",  # the other leader cannot open the Admin's approval, but sees this list
        dedupe_key=f"transfer.requested:{p['transfer_id']}",
    )


@on_event(events.TRANSFER_COMMENTED)
async def transfer_commented(session: AsyncSession, event: OutboxEvent) -> None:
    p = event.payload
    req = await session.get(ApprovalRequest, uuid.UUID(p["approval_id"]))
    if req is None:
        return
    await notifications.notify(
        session,
        event.org_id,
        await eligible_approvers(session, req),
        events.TRANSFER_COMMENTED,
        "New comment on a transfer request",
        f"{p['by']} commented on: {req.payload.get('summary', 'a transfer request')}",
        f"/approvals/{req.id}",
        dedupe_key=f"transfer.commented:{event.id}",
    )


@on_event(events.TRANSFER_DECIDED)
async def transfer_decided(session: AsyncSession, event: OutboxEvent) -> None:
    p = event.payload
    leaders = [
        await _leader(session, uuid.UUID(p["from_team_id"])),
        await _leader(session, uuid.UUID(p["to_team_id"])),
        uuid.UUID(p["requested_by"]),
    ]
    await notifications.notify(
        session,
        event.org_id,
        [x for x in leaders if x],
        events.TRANSFER_DECIDED,
        "Transfer approved",
        "A deal was moved between teams.",
        f"/deals/{p['deal_id']}",
        dedupe_key=f"transfer.decided:{p['transfer_id']}",
    )
