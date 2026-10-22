# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Cross-team transfers (PRD 4.4 F). A Team Leader asks to give a deal to another team or take
one from another team; an Admin decides through the normal approval flow.

- One open request per deal (row lock + partial unique index).
- The other Team Leader is told and may leave a comment for the Admin.
- On approval the deal is re-checked (still in the same team with the same owner), then moved.
  The previous owner loses access at once because scope follows the deal's team.
- A rejected or cancelled request is closed lazily the next time anyone touches that deal.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, OutOfScope, PermissionDenied, ValidationFailed
from app.core.time import utc_now
from app.modules.accounts.models import Account
from app.modules.deals.common import load_deal
from app.modules.leads import events
from app.modules.leads.models import Deal, TransferRequest
from app.modules.leads.ownership import names, team_names
from app.modules.leads.service import move_deal_to_team
from app.modules.users.models import Team, User
from app.platform.approvals import service as approvals
from app.platform.approvals.models import ApprovalRequest
from app.platform.context import Actor, RequestInfo
from app.platform.permissions.matrix import ROUTE_ADMIN_POOL
from app.platform.permissions.roles import Role
from app.platform.uow import UnitOfWork

TYPE = "deal.team_transfer"
KEY = "deal.team_transfer.request"
MOVABLE = ("open", "pooled")


async def _close_finished(session: AsyncSession, deal_id: uuid.UUID) -> None:
    """Close an 'open' transfer whose approval was rejected or cancelled."""
    rows = (
        await session.execute(
            select(TransferRequest, ApprovalRequest.status)
            .join(ApprovalRequest, ApprovalRequest.id == TransferRequest.approval_id)
            .where(TransferRequest.deal_id == deal_id, TransferRequest.status == "open")
            .with_for_update(of=TransferRequest)
        )
    ).all()
    for tr, approval_status in rows:
        if approval_status != "pending":
            tr.status, tr.decided_at = approval_status, tr.decided_at or utc_now()
    await session.flush()


async def _leader_of(session: AsyncSession, team_id: uuid.UUID) -> uuid.UUID | None:
    return (
        await session.execute(select(Team.leader_id).where(Team.id == team_id))
    ).scalar_one_or_none()


async def lookup(session: AsyncSession, actor: Actor, q: str) -> list[dict[str, Any]]:
    """Deals in other teams, by company name. A Team Leader sees company and team only; this is
    the one place a lookup crosses team scope, so nothing else about the deal is returned."""
    if actor.role != Role.TEAM_LEADER:
        raise PermissionDenied("Only Team Leaders look up deals in other teams")
    term = q.strip()
    if len(term) < 2:
        return []
    pattern = "%" + term.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"
    rows = (
        await session.execute(
            select(Deal.id, Account.name, Deal.team_id)
            .join(Account, Account.id == Deal.account_id)
            .where(
                Deal.org_id == actor.org_id,
                Deal.deleted_at.is_(None),
                Deal.status.in_(MOVABLE),
                Deal.team_id.is_not(None),
                Deal.team_id != actor.team_id,
                Account.name.ilike(pattern, escape="\\"),
            )
            .order_by(Account.name)
            .limit(20)
        )
    ).all()
    teams = await team_names(session, {r[2] for r in rows})
    return [
        {"deal_id": r[0], "company": r[1], "team_name": teams.get(r[2]) if r[2] else None}
        for r in rows
    ]


async def request_transfer(actor: Actor, request: RequestInfo, body: Any) -> ApprovalRequest:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        await uow.authorize(KEY)  # Admin and Super admin move directly instead
        if actor.team_id is None:
            raise ValidationFailed("You are not in a team")
        if body.direction == "give":
            if body.to_team_id is None or body.to_team_id == actor.team_id:
                raise ValidationFailed(
                    "Choose another team to give the deal to", {"field": "to_team_id"}
                )
            if body.owner_id is not None:
                raise ValidationFailed(
                    "The receiving Team Leader picks the owner", {"field": "owner_id"}
                )
            deal = await load_deal(uow, body.deal_id, lock=True)
            if deal.team_id != actor.team_id:
                raise NotFound("Deal not found")  # not yours: do not reveal it exists
            from_team, to_team = actor.team_id, body.to_team_id
        else:
            deal = await load_deal(uow, body.deal_id, lock=True)
            if deal.team_id is None:
                raise ValidationFailed(
                    "That lead is not routed to any team yet, so it cannot be taken"
                )
            if deal.team_id == actor.team_id:
                raise ValidationFailed("That deal is already in your team")
            from_team, to_team = deal.team_id, actor.team_id
        if deal.status not in MOVABLE:
            raise Conflict("Only open or pooled deals can be transferred")
        target_team = await s.get(Team, to_team)
        if target_team is None or target_team.org_id != actor.org_id or target_team.deleted_at:
            raise NotFound("Team not found")
        proposed: User | None = None
        if body.owner_id is not None:
            proposed = (
                await s.execute(
                    select(User).where(User.id == body.owner_id, User.org_id == actor.org_id)
                )
            ).scalar_one_or_none()
            if proposed is None or not proposed.is_active or proposed.team_id != actor.team_id:
                raise ValidationFailed(
                    "Choose an active rep in your own team", {"field": "owner_id"}
                )
        await _close_finished(s, deal.id)
        if (
            await s.execute(
                select(TransferRequest.id).where(
                    TransferRequest.deal_id == deal.id, TransferRequest.status == "open"
                )
            )
        ).first():
            raise Conflict("There is already an open transfer request for this deal")
        company = (await s.get(Account, deal.account_id)).name  # type: ignore[union-attr]
        tm = await team_names(s, {from_team, to_team})
        verb = "give" if body.direction == "give" else "take"
        summary = (
            f"{actor.name} asks to {verb} {company} "
            f"({'to ' + tm[to_team] if verb == 'give' else 'from ' + tm[from_team]})"
        )
        tr = TransferRequest(
            org_id=actor.org_id,
            deal_id=deal.id,
            direction=body.direction,
            from_team_id=from_team,
            to_team_id=to_team,
            from_owner_id=deal.owner_id,
            proposed_owner_id=proposed.id if proposed else None,
            requested_by=actor.id,
            reason=(body.reason or "").strip(),
        )
        s.add(tr)
        await s.flush()
        req = await approvals.create_request(
            uow,
            type_=TYPE,
            action_key=KEY,
            summary=summary,
            payload={
                "transfer_id": str(tr.id),
                "deal_id": str(deal.id),
                "direction": body.direction,
                "from_team_id": str(from_team),
                "to_team_id": str(to_team),
            },
            reason=body.reason,
            routed_to=ROUTE_ADMIN_POOL,
            team_id=actor.team_id,
            entity=("deal", str(deal.id)),
            record_owner_id=deal.owner_id,
        )
        tr.approval_id, tr.reason = req.id, req.request_reason
        await s.flush()
        other_team = from_team if body.direction == "take" else to_team
        await uow.emit(
            events.TRANSFER_REQUESTED,
            {
                "transfer_id": str(tr.id),
                "approval_id": str(req.id),
                "company": company,
                "other_leader_id": str(await _leader_of(s, other_team) or ""),
                "summary": summary,
            },
        )
        return req


async def comment(
    actor: Actor, request: RequestInfo, transfer_id: uuid.UUID, text: str
) -> uuid.UUID:
    """The other Team Leader's note to the Admin who will decide."""
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        tr = (
            await s.execute(
                select(TransferRequest)
                .where(TransferRequest.id == transfer_id, TransferRequest.org_id == actor.org_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if tr is None:
            raise NotFound("Transfer request not found")
        other_team = tr.from_team_id if tr.direction == "take" else tr.to_team_id
        is_other_tl = actor.role == Role.TEAM_LEADER and actor.team_id == other_team
        if not (is_other_tl or actor.role in (Role.ADMIN, Role.SUPER_ADMIN)):
            raise NotFound("Transfer request not found")
        await _close_finished(s, tr.deal_id)  # a rejected or cancelled request is closed now
        await s.refresh(tr)
        if tr.status != "open":
            raise Conflict("This request is already closed")
        tr.other_tl_comment, tr.other_tl_comment_by = text.strip(), actor.id
        await uow.audit(
            KEY,
            "deal",
            tr.deal_id,
            new={"comment": tr.other_tl_comment},
            note="Comment on transfer",
        )
        await uow.emit(
            events.TRANSFER_COMMENTED,
            {"transfer_id": str(tr.id), "approval_id": str(tr.approval_id), "by": actor.name},
        )
        return tr.id


async def exec_transfer(uow: UnitOfWork, req: ApprovalRequest) -> None:
    """Runs inside the approver's decision, with the approval row locked."""
    s = uow.session
    tr = (
        await s.execute(
            select(TransferRequest).where(TransferRequest.approval_id == req.id).with_for_update()
        )
    ).scalar_one_or_none()
    if tr is None:
        raise NotFound("Transfer request not found")
    deal = await load_deal(uow, tr.deal_id, lock=True)
    if (
        deal.team_id != tr.from_team_id
        or deal.owner_id != tr.from_owner_id
        or deal.status not in MOVABLE
    ):
        raise Conflict(
            "This deal has changed since the request was made. Reject it and ask again.",
            {"code": "transfer_stale"},
        )
    # "give": the receiving Team Leader assigns it from the Pool. "take": the named rep gets it.
    await move_deal_to_team(
        uow, deal, tr.to_team_id, tr.proposed_owner_id, req.request_reason, action=KEY
    )
    tr.status, tr.decided_at = "approved", utc_now()
    await uow.emit(
        events.TRANSFER_DECIDED,
        {
            "transfer_id": str(tr.id),
            "deal_id": str(deal.id),
            "from_team_id": str(tr.from_team_id),
            "to_team_id": str(tr.to_team_id),
            "requested_by": str(tr.requested_by),
        },
    )


async def list_transfers(
    session: AsyncSession, actor: Actor, limit: int = 100
) -> list[dict[str, Any]]:
    stmt = select(TransferRequest).where(TransferRequest.org_id == actor.org_id)
    if actor.role == Role.TEAM_LEADER:
        stmt = stmt.where(
            (TransferRequest.from_team_id == actor.team_id)
            | (TransferRequest.to_team_id == actor.team_id)
        )
    elif actor.role not in (Role.ADMIN, Role.SUPER_ADMIN):
        raise OutOfScope("Not available")
    rows = list(
        (
            await session.execute(stmt.order_by(TransferRequest.created_at.desc()).limit(limit))
        ).scalars()
    )
    if not rows:
        return []
    deals = {
        d.id: d
        for d in (
            await session.execute(select(Deal).where(Deal.id.in_({r.deal_id for r in rows})))
        ).scalars()
    }
    accs = {
        a.id: a.name
        for a in (
            await session.execute(
                select(Account).where(Account.id.in_({d.account_id for d in deals.values()}))
            )
        ).scalars()
    }
    approvals_by_id = {
        a.id: a.status
        for a in (
            await session.execute(
                select(ApprovalRequest).where(
                    ApprovalRequest.id.in_({r.approval_id for r in rows if r.approval_id})
                )
            )
        ).scalars()
    }
    tm = await team_names(session, {r.from_team_id for r in rows} | {r.to_team_id for r in rows})
    people = await names(
        session, {r.requested_by for r in rows} | {r.proposed_owner_id for r in rows}
    )
    out = []
    for r in rows:
        d = deals.get(r.deal_id)
        status = (
            r.status
            if r.status != "open"
            else approvals_by_id.get(r.approval_id or uuid.UUID(int=0), "open")
        )
        out.append(
            {
                "id": r.id,
                "deal_id": r.deal_id,
                "company": accs.get(d.account_id) if d else None,
                "direction": r.direction,
                "from_team": tm.get(r.from_team_id),
                "to_team": tm.get(r.to_team_id),
                "proposed_owner": people.get(r.proposed_owner_id) if r.proposed_owner_id else None,
                "requested_by": people.get(r.requested_by, ""),
                "reason": r.reason,
                "other_tl_comment": r.other_tl_comment,
                "status": "open" if status == "pending" else status,
                "approval_id": r.approval_id,
                "created_at": r.created_at,
            }
        )
    return out


def register() -> None:
    approvals.register_type(TYPE, exec_transfer)
