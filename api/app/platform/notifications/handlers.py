# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Outbox handlers that turn domain events into notifications and emails.

Every handler is idempotent (dedupe keys): the outbox delivers at-least-once.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.users.models import User
from app.platform.approvals.models import ApprovalRequest
from app.platform.approvals.service import eligible_approvers
from app.platform.notifications import email, service
from app.platform.outbox.models import OutboxEvent
from app.platform.outbox.service import on_event


@on_event("email.send")
async def send_email(_: AsyncSession, event: OutboxEvent) -> None:
    p = event.payload
    html_body, text_body = email.render(p["subject"], p.get("body", ""), p.get("link"))
    await email.get_backend().send(p["to"], p["subject"], html_body, text_body)


@on_event("approval.requested")
async def approval_requested(session: AsyncSession, event: OutboxEvent) -> None:
    req = await session.get(ApprovalRequest, uuid.UUID(event.payload["approval_id"]))
    if req is None:
        return
    await service.notify(
        session,
        req.org_id,
        await eligible_approvers(session, req),
        "approval.requested",
        "Approval requested",
        str(req.payload.get("summary", req.type)),
        f"/approvals/{req.id}",
        dedupe_key=f"approval.requested:{req.id}",
    )


@on_event("approval.decided")
async def approval_decided(session: AsyncSession, event: OutboxEvent) -> None:
    req = await session.get(ApprovalRequest, uuid.UUID(event.payload["approval_id"]))
    if req is None:
        return
    decider = await session.get(User, req.decided_by) if req.decided_by else None
    who = decider.name if decider else "An approver"
    body = f"{who} {req.status} your request: {req.payload.get('summary', req.type)}"
    if req.decision_reason:
        body += f" Reason: {req.decision_reason}"
    await service.notify(
        session,
        req.org_id,
        [req.requested_by],
        "approval.decided",
        f"Request {req.status}",
        body,
        f"/approvals/{req.id}",
        dedupe_key=f"approval.decided:{req.id}",
    )


@on_event("user.invited")
async def user_invited(session: AsyncSession, event: OutboxEvent) -> None:
    uid = uuid.UUID(event.payload["user_id"])
    user = await session.get(User, uid)
    if user is None:
        return
    await service.notify(
        session,
        user.org_id,
        [uid],
        "user.invited",
        "You have been added to Relay CRM",
        f"Your role: {user.role.replace('_', ' ')}. Sign in with {user.email}.",
        "/",
        dedupe_key=f"user.invited:{uid}",
    )


@on_event("user.team_changed")
async def user_team_changed(session: AsyncSession, event: OutboxEvent) -> None:
    uid = uuid.UUID(event.payload["user_id"])
    user = await session.get(User, uid)
    if user is None:
        return
    await service.notify(
        session,
        user.org_id,
        [uid],
        "user.team_changed",
        "Your team changed",
        str(event.payload.get("text", "You were moved to another team.")),
        "/",
        dedupe_key=f"user.team_changed:{event.id}",
    )


@on_event("user.role_changed")
async def user_role_changed(session: AsyncSession, event: OutboxEvent) -> None:
    uid = uuid.UUID(event.payload["user_id"])
    user = await session.get(User, uid)
    if user is None:
        return
    await service.notify(
        session,
        user.org_id,
        [uid],
        "user.role_changed",
        "Your role changed",
        str(event.payload.get("text", "Your role was changed.")),
        "/",
        dedupe_key=f"user.role_changed:{event.id}",
    )
