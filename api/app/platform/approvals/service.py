# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Approval engine (PRD 4.5).

A blocked action becomes a request (never an error). Requests never expire. An approved request
is executed by the registered executor for its type, with `on_behalf_of` = requester and the
approval id recorded in the audit row. The request row is locked while deciding, so two approvers
clicking at once execute it exactly once.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import Conflict, NotFound, PermissionDenied, ReasonRequired
from app.core.time import to_ist, utc_now
from app.modules.users.models import Team, User
from app.platform.approvals.models import ApprovalRequest
from app.platform.context import Actor, RequestInfo
from app.platform.notifications import service as notifications
from app.platform.permissions.matrix import ROUTE_TEAM_LEADER
from app.platform.permissions.roles import Role
from app.platform.reasons import policy
from app.platform.uow import UnitOfWork

Executor = Callable[[UnitOfWork, ApprovalRequest], Awaitable[None]]
EXECUTORS: dict[str, Executor] = {}
# Optional per-type clean-up when a request is rejected or cancelled (for example, a discount
# line that was waiting goes back to normal).
ON_CLOSE: dict[str, Executor] = {}


def register_type(type_: str, executor: Executor, on_close: Executor | None = None) -> None:
    """Later phases register new request types here (discounts, value changes, transfers...)."""
    EXECUTORS[type_] = executor
    if on_close is not None:
        ON_CLOSE[type_] = on_close


def _is_admin_pool(role: str) -> bool:
    return role in (Role.ADMIN, Role.SUPER_ADMIN)


async def eligible_approvers(session: AsyncSession, req: ApprovalRequest) -> list[uuid.UUID]:
    """Who may decide this request right now."""
    ids: set[uuid.UUID] = set()
    admins = (
        await session.execute(
            select(User).where(
                User.org_id == req.org_id,
                User.is_active.is_(True),
                User.role.in_([Role.ADMIN, Role.SUPER_ADMIN]),
            )
        )
    ).scalars()
    for u in admins:
        if u.id == req.requested_by and u.role != Role.SUPER_ADMIN:
            continue  # an Admin never approves their own request
        if u.role == Role.ADMIN and req.record_owner_id == u.id:
            continue  # nor one about a record they own
        ids.add(u.id)
    if req.routed_to == ROUTE_TEAM_LEADER and req.team_id is not None:
        leader = (
            await session.execute(select(Team.leader_id).where(Team.id == req.team_id))
        ).scalar_one_or_none()
        if leader is not None and leader != req.requested_by:
            ids.add(leader)
    return sorted(ids, key=str)


def _check_can_decide(actor: Actor, req: ApprovalRequest) -> None:
    if req.requested_by == actor.id and actor.role != Role.SUPER_ADMIN:
        raise PermissionDenied("You cannot approve your own request")
    if actor.role == Role.ADMIN and req.record_owner_id == actor.id:
        raise PermissionDenied("This request is about your own record; another Admin must decide")
    if _is_admin_pool(actor.role):
        return
    if (
        actor.role == Role.TEAM_LEADER
        and req.routed_to == ROUTE_TEAM_LEADER
        and req.team_id is not None
        and req.team_id == actor.team_id
    ):
        return
    raise PermissionDenied("This request is not yours to decide")


async def create_request(
    uow: UnitOfWork,
    *,
    type_: str,
    action_key: str,
    summary: str,
    payload: dict[str, Any],
    reason: str | None,
    routed_to: str,
    team_id: uuid.UUID | None = None,
    entity: tuple[str, str] | None = None,
    record_owner_id: uuid.UUID | None = None,
) -> ApprovalRequest:
    """Turn a blocked action into a pending request. The requester's reason is mandatory."""
    if type_ not in EXECUTORS:
        raise ValueError(f"no executor registered for approval type {type_!r}")
    cleaned = policy.normalise_reason(reason)
    if cleaned is None:
        raise ReasonRequired("A reason is required to request approval")
    req = ApprovalRequest(
        org_id=uow.actor.org_id,
        type=type_,
        action_key=action_key,
        target_entity_type=entity[0] if entity else None,
        target_entity_id=entity[1] if entity else None,
        record_owner_id=record_owner_id,
        payload={**payload, "summary": summary},
        requested_by=uow.actor.id,
        request_reason=cleaned,
        routed_to=routed_to,
        team_id=team_id,
    )
    uow.session.add(req)
    await uow.session.flush()
    await uow.audit(
        action_key,
        entity[0] if entity else "approval",
        entity[1] if entity else str(req.id),
        outcome="approval_requested",
        new=payload,
        reason=cleaned,
        approval_id=req.id,
    )
    approvers = await eligible_approvers(uow.session, req)
    await uow.emit(
        "approval.requested",
        {"approval_id": str(req.id)},
        rt={
            "entity_type": "approval",
            "entity_id": str(req.id),
            "owner_id": req.requested_by,
            "team_id": team_id,
            "scope_key": "approval.decide",
            "user_ids": [str(i) for i in [*approvers, req.requested_by]],
        },
    )
    return req


async def decide(
    actor: Actor,
    request: RequestInfo,
    approval_id: uuid.UUID,
    *,
    approve: bool,
    reason: str | None,
) -> ApprovalRequest:
    async with UnitOfWork(actor, request) as probe:
        req = await probe.session.get(ApprovalRequest, approval_id)
        if req is None or req.org_id != actor.org_id:
            raise NotFound("Approval request not found")
        requester = req.requested_by
    async with UnitOfWork(actor, request, on_behalf_of=requester, approval_id=approval_id) as uow:
        req = (
            await uow.session.execute(
                select(ApprovalRequest)
                .where(ApprovalRequest.id == approval_id, ApprovalRequest.org_id == actor.org_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if req is None:
            raise NotFound("Approval request not found")
        await uow.authorize(
            "approval.decide", entity=("approval", str(approval_id)), check_scope=False
        )
        if req.status != "pending":
            raise Conflict(f"This request was already {req.status}")
        _check_can_decide(actor, req)
        # PRD 4.5: Admin must give a reason, Super admin optional, a TL gives one when rejecting.
        if actor.role == Role.TEAM_LEADER and approve:
            cleaned = policy.normalise_reason(reason)
        else:
            cleaned = uow.reason("approval.decide", reason)
        old = {"status": req.status}
        if approve:
            await EXECUTORS[req.type](uow, req)
        elif req.type in ON_CLOSE:
            await ON_CLOSE[req.type](uow, req)
        req.status = "approved" if approve else "rejected"
        req.decided_by, req.decision_reason, req.decided_at = actor.id, cleaned, utc_now()
        await uow.audit(
            "approval.decide",
            "approval",
            str(req.id),
            old=old,
            new={"status": req.status, "requested_by": str(req.requested_by), "type": req.type},
            reason=cleaned,
        )
        await uow.emit(
            "approval.decided",
            {"approval_id": str(req.id)},
            rt={
                "entity_type": "approval",
                "entity_id": str(req.id),
                "owner_id": req.requested_by,
                "team_id": req.team_id,
                "scope_key": "approval.decide",
                "user_ids": [str(req.requested_by)],
            },
        )
        return req


async def cancel(actor: Actor, request: RequestInfo, approval_id: uuid.UUID) -> ApprovalRequest:
    async with UnitOfWork(actor, request) as uow:
        req = (
            await uow.session.execute(
                select(ApprovalRequest)
                .where(ApprovalRequest.id == approval_id, ApprovalRequest.org_id == actor.org_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if req is None or req.requested_by != actor.id:
            raise NotFound("Approval request not found")
        if req.status != "pending":
            raise Conflict(f"This request was already {req.status}")
        req.status, req.decided_at = "cancelled", utc_now()
        if req.type in ON_CLOSE:
            await ON_CLOSE[req.type](uow, req)
        await uow.audit(
            "approval.cancel",
            "approval",
            str(req.id),
            old={"status": "pending"},
            new={"status": "cancelled"},
        )
        return req


async def list_requests(
    session: AsyncSession, actor: Actor, tab: str, limit: int = 100, offset: int = 0
) -> list[ApprovalRequest]:
    base = select(ApprovalRequest).where(ApprovalRequest.org_id == actor.org_id)
    if tab == "mine":
        stmt = base.where(ApprovalRequest.requested_by == actor.id)
    else:
        status_filter = (
            ApprovalRequest.status == "pending"
            if tab == "pending"
            else ApprovalRequest.status != "pending"
        )
        if _is_admin_pool(actor.role):
            stmt = base.where(status_filter)
        elif actor.role == Role.TEAM_LEADER:
            stmt = base.where(
                status_filter,
                ApprovalRequest.routed_to == ROUTE_TEAM_LEADER,
                ApprovalRequest.team_id == actor.team_id,
            )
        else:
            return []
    rows = (
        await session.execute(
            stmt.order_by(ApprovalRequest.created_at.desc()).limit(limit).offset(offset)
        )
    ).scalars()
    result = list(rows)
    if tab == "pending":  # hide requests this person may not decide (own, own-record)
        result = [r for r in result if _can_decide_quietly(actor, r)]
    return result


def _can_decide_quietly(actor: Actor, req: ApprovalRequest) -> bool:
    try:
        _check_can_decide(actor, req)
    except PermissionDenied:
        return False
    return True


async def get_request(
    session: AsyncSession, actor: Actor, approval_id: uuid.UUID
) -> ApprovalRequest:
    req = await session.get(ApprovalRequest, approval_id)
    if req is None or req.org_id != actor.org_id:
        raise NotFound("Approval request not found")
    visible = (
        req.requested_by == actor.id
        or _is_admin_pool(actor.role)
        or (
            actor.role == Role.TEAM_LEADER
            and req.routed_to == ROUTE_TEAM_LEADER
            and req.team_id == actor.team_id
        )
    )
    if not visible:
        raise NotFound("Approval request not found")
    return req


async def send_pending_reminders(factory: async_sessionmaker[AsyncSession]) -> int:
    """Daily job: remind every eligible approver of each pending request. Idempotent per day."""
    day = to_ist(utc_now()).date().isoformat()
    sent = 0
    async with factory() as session, session.begin():
        pending = (
            await session.execute(
                select(ApprovalRequest).where(ApprovalRequest.status == "pending")
            )
        ).scalars()
        for req in list(pending):
            approvers = await eligible_approvers(session, req)
            sent += await notifications.notify(
                session,
                req.org_id,
                approvers,
                "approval.reminder",
                "Approval still waiting",
                str(req.payload.get("summary", req.type)),
                f"/approvals/{req.id}",
                dedupe_key=f"reminder:{req.id}:{day}",
            )
    return sent
