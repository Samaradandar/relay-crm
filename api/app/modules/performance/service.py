# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Business rules: targets (P6-04), incentives (P6-05/06), team colours (P6-03), the leaderboard
read (P6-02) and the Admin/SA month-recompute action. Every write goes through UnitOfWork
(permission, scope, reason, audit, outbox) like every other module.
"""

import uuid
from datetime import date
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.core.time import utc_now
from app.modules.accounts.models import Account
from app.modules.billing.payments import customer_payments_received
from app.modules.leads.models import Deal
from app.modules.leads.ownership import names
from app.modules.performance import events, stats
from app.modules.performance.colours import generate_colour
from app.modules.performance.models import Incentive, IncentiveRequest, RepMonthStats, Target
from app.modules.users.models import Team, User
from app.platform.approvals import service as approvals
from app.platform.approvals.models import ApprovalRequest
from app.platform.context import Actor, RequestInfo
from app.platform.notifications import service as notifications
from app.platform.permissions.matrix import ROUTE_ADMIN_POOL
from app.platform.permissions.roles import Role, Scope
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork

TARGET_KEY = "target.set"
INCENTIVE_RATE_KEY = "incentive_rate.set"
INCENTIVE_VIEW_KEY = "incentive.view"
INCENTIVE_REQUEST_KEY = "incentive.request"
INCENTIVE_REQUEST_WON_TYPE = "incentive.request_won_deal_rate"
INCENTIVE_REQUEST_PLEDGE_TYPE = "incentive.request_active_deal_pledge"
COLOUR_KEY = "team.colour.change"
RECOMPUTE_KEY = "performance.recompute"


# ---------------------------------------------------------------------------------- leaderboard
async def leaderboard(
    session: AsyncSession, actor: Actor, month: date | None, team_id: uuid.UUID | None
) -> dict[str, Any]:
    """Visible to everyone (PRD): the one exception to own-scope. No incentive field is ever
    read or returned here."""
    await authorize_read(session, actor, "leaderboard.view")
    month = month or stats.current_ist_month()
    is_current = month == stats.current_ist_month()
    conds = [RepMonthStats.org_id == actor.org_id, RepMonthStats.month == month]
    if team_id is not None:
        conds.append(RepMonthStats.team_id == team_id)
    rows = (
        await session.execute(
            select(RepMonthStats, User.name, User.team_id, Team.name, Team.colour)
            .join(User, User.id == RepMonthStats.user_id)
            .outerjoin(Team, Team.id == User.team_id)
            # Only sales reps are ranked, even if a stale stats row exists for someone whose role
            # changed later (e.g. a rep promoted to Admin) -- recompute_org_month only ever
            # writes rows for Role.USER, but doesn't delete old ones, so this guards the read side.
            .where(
                User.role == Role.USER, *conds, *([User.is_active.is_(True)] if is_current else [])
            )
        )
    ).all()
    out = [
        {
            "user_id": r.user_id,
            "name": name,
            "team_id": team_id_,
            "team_name": team_name,
            "team_colour": team_colour,
            "active_deals": r.active_deals,
            "won_deals": r.won_deals,
            "revenue_best_paise": r.best_to_date_paise,
            "revenue_current_paise": r.revenue_current_paise,
            "revenue_projected_paise": r.revenue_projected_paise,
            "target_paise": r.target_paise,
            "rank": r.rank,
            "is_you": r.user_id == actor.id,
        }
        for r, name, team_id_, team_name, team_colour in rows
    ]
    out.sort(key=lambda r: (r["rank"] if r["rank"] is not None else 10**9, r["name"]))
    return {"month": month.isoformat(), "rows": out}


# -------------------------------------------------------------------------------------- targets
async def get_target(
    session: AsyncSession, actor: Actor, user_id: uuid.UUID, month: date
) -> dict[str, Any] | None:
    await authorize_read(session, actor, "leaderboard.view")  # targets are visible to everyone
    row = (
        await session.execute(
            select(Target).where(
                Target.org_id == actor.org_id, Target.user_id == user_id, Target.month == month
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return {
        "user_id": row.user_id,
        "month": row.month,
        "amount_paise": row.amount_paise,
        "set_by": row.set_by,
        "reason": row.reason,
    }


def _target_editable(actor: Actor, month: date) -> bool:
    if month >= stats.current_ist_month():
        return True
    return actor.is_privileged  # past months are read-only except Admin (reason) / SA


async def set_target(
    actor: Actor,
    request: RequestInfo,
    user_id: uuid.UUID,
    month: date,
    amount_paise: int,
    reason: str | None,
) -> dict[str, Any]:
    if amount_paise < 0:
        raise ValidationFailed("Target cannot be negative", {"field": "amount_paise"})
    if not _target_editable(actor, month):
        raise PermissionDenied("Past months are read-only except for Admin or Super admin")
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        target_user = await s.get(User, user_id)
        if target_user is None or target_user.role != Role.USER:
            raise NotFound("Rep not found")
        await uow.authorize(TARGET_KEY, entity=("user", user_id))
        cleaned = uow.reason(TARGET_KEY, reason)
        assert actor.id is not None  # target.set is denied to the system actor by the matrix
        row = (
            await s.execute(
                select(Target).where(
                    Target.org_id == actor.org_id, Target.user_id == user_id, Target.month == month
                )
            )
        ).scalar_one_or_none()
        old = {"amount_paise": row.amount_paise} if row else None
        if row is None:
            row = Target(org_id=actor.org_id, user_id=user_id, month=month, created_at=utc_now())
            s.add(row)
        row.amount_paise, row.set_by, row.reason = amount_paise, actor.id, cleaned
        await s.flush()
        await uow.audit(
            TARGET_KEY,
            "target",
            row.id,
            old=old,
            new={"amount_paise": amount_paise},
            reason=cleaned,
        )
        await uow.emit(events.TARGET_SET, {"user_id": str(user_id), "month": month.isoformat()})
        return {"user_id": row.user_id, "month": row.month, "amount_paise": row.amount_paise}


async def set_targets_bulk(
    actor: Actor,
    request: RequestInfo,
    month: date,
    items: list[tuple[uuid.UUID, int]],
    reason: str | None,
) -> list[dict[str, Any]]:
    """A TL sets the whole team's targets for a month in one screen; each rep still gets its own
    audit row (PRD), so this simply calls set_target per item inside one outer UnitOfWork is not
    used -- each gets its own UoW/transaction, matching 'one audit row per rep'."""
    return [await set_target(actor, request, uid, month, amt, reason) for uid, amt in items]


# ------------------------------------------------------------------------------------ incentives
def _incentive_scope_ok(actor: Actor, scope: Scope, deal: Deal) -> bool:
    """A pledge lives on a deal before it is Won, so won_by_user_id/won_team_id are still None --
    fall back to the deal's current owner/team for that case (Leaderboard & Incentive module)."""
    if scope == Scope.ALL:
        return True
    owner_id = deal.won_by_user_id if deal.won_by_user_id is not None else deal.owner_id
    team_id = deal.won_team_id if deal.won_team_id is not None else deal.team_id
    if scope == Scope.OWN:
        return owner_id == actor.id
    if scope == Scope.TEAM:
        return team_id == actor.team_id
    return False


async def recompute_incentive(session: AsyncSession, deal: Deal) -> Incentive | None:
    """Pending/earned amounts, recalculated on read and by the nightly job (no payment-recorded
    outbox event exists today to drive this live -- see events.py's module docstring). Only a row
    with an active, approved rate (status 'approved' or already 'earned') earns anything -- a row
    still 'pending_approval' or (should the deal somehow win before the pledge-apply listener
    runs) 'approved_pending_closure' is left untouched here."""
    if deal.won_by_user_id is None:
        return None
    row = (
        await session.execute(select(Incentive).where(Incentive.deal_id == deal.id))
    ).scalar_one_or_none()
    if row is None or row.status in ("no_rate", "pending_approval", "approved_pending_closure"):
        return row
    if row.status == "earned":
        # frozen once earned: refunds/reversals/rate history after earning are manual (Admin/SA)
        return row
    received = await customer_payments_received(session, deal.id)
    won_value = deal.won_value_paise or 0
    row.won_value_paise = won_value
    row.received_paise = received
    if received >= won_value and won_value > 0:
        row.status = "earned"
        row.earned_amount_paise = won_value * (row.rate_bp or 0) // 10_000
        row.pending_amount_paise = 0
        row.earned_at = utc_now()
    else:
        row.status = "approved"
        row.pending_amount_paise = received * (row.rate_bp or 0) // 10_000
        row.earned_amount_paise = 0
    row.updated_at = utc_now()
    await session.flush()
    return row


def _incentive_out(row: Incentive) -> dict[str, Any]:
    expected_total = (row.won_value_paise * row.rate_bp // 10_000) if row.rate_bp else None
    pledge_preview = (
        (row.pledge_value_paise * row.rate_bp // 10_000)
        if row.is_pledge and row.rate_bp and row.pledge_value_paise
        else None
    )
    return {
        "deal_id": row.deal_id, "user_id": row.user_id, "rate_bp": row.rate_bp,
        "won_value_paise": row.won_value_paise, "received_paise": row.received_paise,
        "pending_amount_paise": row.pending_amount_paise,
        "earned_amount_paise": row.earned_amount_paise,
        "expected_total_paise": expected_total, "status": row.status, "earned_at": row.earned_at,
        "is_pledge": row.is_pledge, "source": row.source,
        "pledge_value_paise": row.pledge_value_paise, "pledge_preview_paise": pledge_preview,
    }  # fmt: skip


async def get_incentive(session: AsyncSession, actor: Actor, deal_id: uuid.UUID) -> dict[str, Any]:
    deal = await session.get(Deal, deal_id)
    if deal is None or deal.deleted_at is not None:
        raise NotFound("Deal not found")
    decision = await authorize_read(session, actor, INCENTIVE_VIEW_KEY)
    if not _incentive_scope_ok(actor, decision.scope, deal):
        raise NotFound("Not found")
    row = (
        await session.execute(select(Incentive).where(Incentive.deal_id == deal_id))
    ).scalar_one_or_none()
    if deal.won_by_user_id is not None:
        row = await recompute_incentive(session, deal) or row
    if row is None:
        raise NotFound("No incentive on this deal yet")
    return _incentive_out(row)


async def set_incentive_rate(
    actor: Actor, request: RequestInfo, deal_id: uuid.UUID, rate_bp: int, reason: str | None
) -> dict[str, Any]:
    """Admin/SA only now (PRD Leaderboard & Incentive module, approved 6 Oct 2026): a Team Leader
    can no longer set a deal's rate directly, only request_incentive_on_won_deal/
    pledge_incentive_on_active_deal below -- see incentive_rate.set's matrix row."""
    if rate_bp < 0 or rate_bp > 10_000:
        raise ValidationFailed("Rate must be between 0% and 100%", {"field": "rate_bp"})
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        deal = await s.get(Deal, deal_id)
        if deal is None or deal.deleted_at is not None or deal.won_by_user_id is None:
            raise NotFound("Won deal not found")
        decision = await uow.authorize(
            INCENTIVE_RATE_KEY, entity=("deal", deal_id), check_scope=False
        )
        if not _incentive_scope_ok(actor, decision.scope, deal):
            raise PermissionDenied("This deal is outside your scope")
        row = (
            await s.execute(select(Incentive).where(Incentive.deal_id == deal_id))
        ).scalar_one_or_none()
        cleaned = uow.reason(INCENTIVE_RATE_KEY, reason)
        old_rate = row.rate_bp if row else None
        if row is None:
            row = Incentive(
                org_id=deal.org_id, deal_id=deal_id, user_id=deal.won_by_user_id,
                won_value_paise=deal.won_value_paise or 0, updated_at=utc_now(),
            )  # fmt: skip
            s.add(row)
        row.rate_bp, row.set_by = rate_bp, actor.id
        row.status = "approved"
        row.is_pledge, row.pledge_value_paise = False, None
        row.source = "sa_set" if actor.role == Role.SUPER_ADMIN else "admin_set"
        row.reason = cleaned
        was_earned = old_rate is not None and row.earned_at is not None and row.status == "earned"
        await s.flush()
        if was_earned:
            row.earned_amount_paise = (row.won_value_paise or 0) * rate_bp // 10_000
            row.updated_at = utc_now()
        else:
            await recompute_incentive(s, deal)
        await uow.audit(
            INCENTIVE_RATE_KEY,
            "incentive",
            row.id,
            old={"rate_bp": old_rate},
            new={"rate_bp": rate_bp},
            reason=cleaned,
        )
        await uow.emit(
            events.INCENTIVE_RATE_SET,
            {"deal_id": str(deal_id), "user_id": str(deal.won_by_user_id)},
        )
        return _incentive_out(row)


async def _create_incentive_request(
    actor: Actor,
    request: RequestInfo,
    deal_id: uuid.UUID,
    rate_bp: int,
    reason: str | None,
    *,
    request_type: str,
    approval_type: str,
    want_won: bool,
) -> dict[str, Any]:
    if rate_bp < 0 or rate_bp > 10_000:
        raise ValidationFailed("Rate must be between 0% and 100%", {"field": "rate_bp"})
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        deal = await s.get(Deal, deal_id)
        if deal is None or deal.deleted_at is not None:
            raise NotFound("Deal not found")
        is_won = deal.won_by_user_id is not None
        if want_won and not is_won:
            raise ValidationFailed("That deal has not been Won yet")
        if not want_won and is_won:
            raise ValidationFailed("That deal is already Won -- request a rate instead")
        if not want_won and deal.status not in ("open", "pooled"):
            raise ValidationFailed("Only an open deal can get a pledge")
        decision = await uow.authorize(
            INCENTIVE_REQUEST_KEY, entity=("deal", deal_id), check_scope=False
        )
        if decision.scope == Scope.TEAM and deal.team_id != actor.team_id:
            raise PermissionDenied("This deal is outside your scope")
        existing = (
            await s.execute(
                select(IncentiveRequest).where(
                    IncentiveRequest.deal_id == deal_id,
                    IncentiveRequest.request_type == request_type,
                    IncentiveRequest.status == "pending",
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise Conflict("There is already a pending request of this type for this deal")
        cleaned = uow.reason(INCENTIVE_REQUEST_KEY, reason)
        assert cleaned is not None  # important class: policy guarantees a reason or raises
        owner_id = deal.won_by_user_id if want_won else deal.owner_id
        assert owner_id is not None  # want_won checked is_won above; else deal always has an owner
        ir = IncentiveRequest(
            org_id=actor.org_id,
            deal_id=deal_id,
            requested_by=actor.id,
            request_type=request_type,
            rate_bp=rate_bp,
            reason=cleaned,
            created_at=utc_now(),
        )
        s.add(ir)
        await s.flush()
        row = (
            await s.execute(select(Incentive).where(Incentive.deal_id == deal_id))
        ).scalar_one_or_none()
        if row is None:
            row = Incentive(
                org_id=deal.org_id, deal_id=deal_id, user_id=owner_id, updated_at=utc_now(),
            )  # fmt: skip
            s.add(row)
        row.status = "pending_approval"
        row.rate_bp = rate_bp
        row.is_pledge = not want_won
        row.pledge_value_paise = deal.value_paise if not want_won else None
        row.user_id = owner_id
        row.updated_at = utc_now()
        await s.flush()
        req = await approvals.create_request(
            uow,
            type_=approval_type,
            action_key=INCENTIVE_REQUEST_KEY,
            summary=f"{actor.name} {'requests' if want_won else 'pledges'} "
            f"{rate_bp / 100:g}% on this deal",
            payload={"incentive_request_id": str(ir.id), "deal_id": str(deal_id)},
            reason=cleaned,
            routed_to=ROUTE_ADMIN_POOL,
            team_id=actor.team_id,
            entity=("deal", str(deal_id)),
            record_owner_id=owner_id,
        )
        ir.decided_at = None
        await s.flush()
        return _incentive_out(row) | {"approval_id": req.id, "incentive_request_id": ir.id}


async def request_incentive_on_won_deal(
    actor: Actor, request: RequestInfo, deal_id: uuid.UUID, rate_bp: int, reason: str | None
) -> dict[str, Any]:
    """A Team Leader asks for a rate on a deal their team has already Won."""
    return await _create_incentive_request(
        actor, request, deal_id, rate_bp, reason,
        request_type="won_deal_rate", approval_type=INCENTIVE_REQUEST_WON_TYPE, want_won=True,
    )  # fmt: skip


async def pledge_incentive_on_active_deal(
    actor: Actor, request: RequestInfo, deal_id: uuid.UUID, rate_bp: int, reason: str | None
) -> dict[str, Any]:
    """A Team Leader pledges a rate on an Active deal, to auto-apply only if/when it is Won."""
    return await _create_incentive_request(
        actor, request, deal_id, rate_bp, reason,
        request_type="active_deal_pledge", approval_type=INCENTIVE_REQUEST_PLEDGE_TYPE,
        want_won=False,
    )  # fmt: skip


async def _load_incentive_request(
    uow: UnitOfWork, req: ApprovalRequest
) -> tuple[IncentiveRequest, Incentive]:
    ir = await uow.session.get(IncentiveRequest, uuid.UUID(req.payload["incentive_request_id"]))
    if ir is None:
        raise NotFound("Incentive request not found")
    row = (
        await uow.session.execute(select(Incentive).where(Incentive.deal_id == ir.deal_id))
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("Incentive not found")
    return ir, row


async def _tell_rep_approved(uow: UnitOfWork, deal: Deal, row: Incentive, *, pledge: bool) -> None:
    """The incentive is approved: the rep it belongs to is told (nobody else sees the figure)."""
    company = await uow.session.get(Account, deal.account_id)
    name = company.name if company else "your deal"
    pct = f"{(row.rate_bp or 0) / 100:g}%"
    await notifications.notify(
        uow.session, deal.org_id, [row.user_id], "incentive.approved",
        f"Your incentive on {name} was approved",
        f"{pct} of the deal value. "
        + (
            "It starts counting if the deal is won."
            if pledge
            else "It is paid out as the customer's payments come in."
        ),
        "/app/incentives", dedupe_key=f"incentive.approved:{row.id}:{row.rate_bp}",
    )  # fmt: skip


async def _exec_incentive_request_won(uow: UnitOfWork, req: ApprovalRequest) -> None:
    """Approved: the rate goes live immediately, status 'approved' -- same accrual mechanics as
    a direct Admin/SA set, source kept as 'tl_request' so the origin stays visible."""
    ir, row = await _load_incentive_request(uow, req)
    deal = await uow.session.get(Deal, ir.deal_id)
    if deal is None or deal.won_by_user_id is None:
        raise Conflict("This deal is no longer Won")
    ir.status, ir.decided_by = "approved", uow.actor.id
    ir.decision_reason, ir.decided_at = req.decision_reason, utc_now()
    row.rate_bp, row.status, row.source = ir.rate_bp, "approved", "tl_request"
    row.is_pledge, row.pledge_value_paise = False, None
    row.updated_at = utc_now()
    await uow.session.flush()
    await recompute_incentive(uow.session, deal)
    await uow.audit(
        INCENTIVE_RATE_KEY, "incentive", row.id, new={"rate_bp": ir.rate_bp, "status": "approved"},
        note="Approved TL incentive request",
    )  # fmt: skip
    await _tell_rep_approved(uow, deal, row, pledge=False)


async def _exec_incentive_request_pledge(uow: UnitOfWork, req: ApprovalRequest) -> None:
    """Approved: the rate is locked in but accrues nothing until the deal is actually Won --
    see performance/handlers.py's deal.won listener for the auto-apply step."""
    ir, row = await _load_incentive_request(uow, req)
    ir.status, ir.decided_by = "approved", uow.actor.id
    ir.decision_reason, ir.decided_at = req.decision_reason, utc_now()
    row.rate_bp, row.status, row.source = ir.rate_bp, "approved_pending_closure", "tl_request"
    row.is_pledge = True
    row.updated_at = utc_now()
    await uow.session.flush()
    await uow.audit(
        INCENTIVE_RATE_KEY, "incentive", row.id,
        new={"rate_bp": ir.rate_bp, "status": "approved_pending_closure"},
        note="Approved TL incentive pledge",
    )  # fmt: skip
    deal = await uow.session.get(Deal, ir.deal_id)
    if deal is not None:
        await _tell_rep_approved(uow, deal, row, pledge=True)


async def _close_incentive_request(uow: UnitOfWork, req: ApprovalRequest) -> None:
    """Rejected or cancelled: the IncentiveRequest and the live Incentive row both revert."""
    ir, row = await _load_incentive_request(uow, req)
    ir.status = "rejected"
    ir.decided_by, ir.decision_reason, ir.decided_at = (
        uow.actor.id, req.decision_reason, utc_now(),
    )  # fmt: skip
    row.status, row.rate_bp = "no_rate", None
    row.is_pledge, row.pledge_value_paise, row.source = False, None, None
    row.updated_at = utc_now()
    await uow.session.flush()


async def apply_pledge_on_won(session: AsyncSession, deal: Deal) -> None:
    """Called from performance/handlers.py's deal.won listener: an approved pledge on this deal
    accrues from now on, with no second approval (PRD)."""
    if deal.won_by_user_id is None:
        return
    row = (
        await session.execute(select(Incentive).where(Incentive.deal_id == deal.id))
    ).scalar_one_or_none()
    if row is None or row.status != "approved_pending_closure" or not row.is_pledge:
        return
    row.status, row.source = "approved", "pledge_auto_applied"
    row.is_pledge, row.user_id = False, deal.won_by_user_id
    row.updated_at = utc_now()
    await session.flush()
    from app.platform.audit import service as audit_service
    from app.platform.context import system_actor

    await audit_service.append(
        session, system_actor(deal.org_id), INCENTIVE_RATE_KEY, "incentive", row.id,
        new={"status": "approved", "source": "pledge_auto_applied"},
        note="Pledge auto-applied on Won",
    )  # fmt: skip
    await recompute_incentive(session, deal)


def register() -> None:
    approvals.register_type(
        INCENTIVE_REQUEST_WON_TYPE, _exec_incentive_request_won, _close_incentive_request
    )
    approvals.register_type(
        INCENTIVE_REQUEST_PLEDGE_TYPE, _exec_incentive_request_pledge, _close_incentive_request
    )


async def list_incentives(session: AsyncSession, actor: Actor) -> list[dict[str, Any]]:
    """The Incentives page: a rep sees their own, a Team Leader their team's, an Admin all. Each
    row is a Won deal (or a pledge on an open one) with its rate, status, the Team Leader's latest
    request and, for an Admin, the approval waiting on them."""
    from app.modules.pipeline.engine.service import onboarding_done

    decision = await authorize_read(session, actor, INCENTIVE_VIEW_KEY)
    owner_expr = func.coalesce(Deal.won_by_user_id, Deal.owner_id)
    team_expr = func.coalesce(Deal.won_team_id, Deal.team_id)
    conds = [Deal.org_id == actor.org_id, Deal.deleted_at.is_(None)]
    if decision.scope == Scope.OWN:
        conds.append(owner_expr == actor.id)
    elif decision.scope == Scope.TEAM:
        conds.append(team_expr == actor.team_id)
    elif decision.scope != Scope.ALL:
        return []
    found = (
        await session.execute(
            select(Deal, Incentive, Account.name)
            .join(Account, Account.id == Deal.account_id)
            .outerjoin(Incentive, Incentive.deal_id == Deal.id)
            .where(*conds, or_(Deal.won_by_user_id.is_not(None), Incentive.id.is_not(None)))
            .order_by(Deal.won_at.desc().nullslast(), Deal.created_at.desc())
            .limit(500)
        )
    ).all()
    deal_ids = [d.id for d, _, _ in found]
    requests: dict[uuid.UUID, IncentiveRequest] = {}
    if deal_ids:
        for ir in (
            await session.execute(
                select(IncentiveRequest)
                .where(IncentiveRequest.deal_id.in_(deal_ids))
                .order_by(IncentiveRequest.created_at)
            )
        ).scalars():
            requests[ir.deal_id] = ir  # the latest one wins
    can_decide = actor.role in ("admin", "super_admin")
    approvals_by_deal: dict[str, uuid.UUID] = {}
    if can_decide and deal_ids:
        for ap in (
            await session.execute(
                select(ApprovalRequest).where(
                    ApprovalRequest.status == "pending",
                    ApprovalRequest.type.in_(
                        (INCENTIVE_REQUEST_WON_TYPE, INCENTIVE_REQUEST_PLEDGE_TYPE)
                    ),
                )
            )
        ).scalars():
            if ap.target_entity_id:
                approvals_by_deal[ap.target_entity_id] = ap.id
    who = await names(
        session,
        {owner for d, _, _ in found if (owner := d.won_by_user_id or d.owner_id)}
        | {r.requested_by for r in requests.values()},
    )
    out: list[dict[str, Any]] = []
    for deal, inc, company in found:
        if inc is not None and deal.won_by_user_id is not None:
            inc = await recompute_incentive(session, deal) or inc
        req = requests.get(deal.id)
        rep_id = deal.won_by_user_id or deal.owner_id
        out.append(
            {
                "deal_id": deal.id,
                "company": company,
                "stage_key": deal.stage_key,
                "won": deal.won_by_user_id is not None,
                "rep": {"id": rep_id, "name": who.get(rep_id) if rep_id else None},
                "deal_value_paise": deal.won_value_paise or deal.value_paise,
                "won_at": deal.won_at,
                "onboarding_done": await onboarding_done(session, deal),
                "incentive": _incentive_out(inc) if inc is not None else None,
                "request": (
                    {
                        "id": req.id,
                        "type": req.request_type,
                        "status": req.status,
                        "rate_bp": req.rate_bp,
                        "reason": req.reason,
                        "requested_by": who.get(req.requested_by),
                        "decision_reason": req.decision_reason,
                        "created_at": req.created_at,
                        "decided_at": req.decided_at,
                    }
                    if req is not None
                    else None
                ),
                "approval_id": approvals_by_deal.get(str(deal.id)),
            }
        )
    return out


async def missing_rates(session: AsyncSession, actor: Actor) -> list[dict[str, Any]]:
    """TL queue: their team's Won deals with no rate set yet."""
    decision = await authorize_read(session, actor, INCENTIVE_RATE_KEY)
    conds = [
        Deal.org_id == actor.org_id,
        Deal.won_by_user_id.is_not(None),
        Deal.deleted_at.is_(None),
    ]
    if decision.scope == Scope.TEAM:
        conds.append(Deal.won_team_id == actor.team_id)
    elif decision.scope == Scope.OWN:
        conds.append(Deal.won_by_user_id == actor.id)
    elif decision.scope != Scope.ALL:
        return []
    deals = (await session.execute(select(Deal).where(*conds))).scalars().all()
    rated_ids = {
        r[0]
        for r in (
            await session.execute(
                select(Incentive.deal_id).where(
                    Incentive.deal_id.in_([d.id for d in deals]), Incentive.rate_bp.is_not(None)
                )
            )
        ).all()
    }
    return [
        {
            "deal_id": d.id,
            "user_id": d.won_by_user_id,
            "won_value_paise": d.won_value_paise,
            "won_at": d.won_at,
        }
        for d in deals
        if d.id not in rated_ids
    ]


# --------------------------------------------------------------------------------- team colours
async def regenerate_colour(
    actor: Actor, request: RequestInfo, team_id: uuid.UUID, reason: str | None
) -> str:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        team = await s.get(Team, team_id)
        if team is None or team.deleted_at is not None:
            raise NotFound("Team not found")
        await uow.authorize(COLOUR_KEY, entity=("team", team_id))
        cleaned = uow.reason(COLOUR_KEY, reason)
        existing = (
            (
                await s.execute(
                    select(Team.colour).where(
                        Team.org_id == actor.org_id, Team.id != team_id, Team.colour.is_not(None)
                    )
                )
            )
            .scalars()
            .all()
        )
        old = team.colour
        new_colour = generate_colour([c for c in existing if c])
        team.colour = new_colour
        await s.flush()
        await uow.audit(
            COLOUR_KEY,
            "team",
            team_id,
            old={"colour": old},
            new={"colour": new_colour},
            reason=cleaned,
        )
        await uow.emit(events.TEAM_COLOUR_CHANGED, {"team_id": str(team_id)})
        return new_colour


async def backfill_team_colours(session: AsyncSession, org_id: uuid.UUID) -> int:
    teams = (
        (
            await session.execute(
                select(Team).where(
                    Team.org_id == org_id, Team.colour.is_(None), Team.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    existing = (
        (
            await session.execute(
                select(Team.colour).where(Team.org_id == org_id, Team.colour.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    colours = [c for c in existing if c]
    for team in teams:
        new_colour = generate_colour(colours)
        team.colour = new_colour
        colours.append(new_colour)
    await session.flush()
    return len(teams)


# -------------------------------------------------------------------------- admin month recompute
async def recompute_month(
    actor: Actor, request: RequestInfo, month: date, reason: str | None
) -> int:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        await uow.authorize(RECOMPUTE_KEY)
        cleaned = uow.reason(RECOMPUTE_KEY, reason)
        rows = (
            (
                await s.execute(
                    select(RepMonthStats).where(
                        RepMonthStats.org_id == actor.org_id, RepMonthStats.month == month
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.is_closed = False
        await s.flush()
        n = await stats.recompute_org_month(s, actor.org_id, month)
        await uow.audit(
            RECOMPUTE_KEY,
            "org",
            str(actor.org_id),
            new={"month": month.isoformat(), "reps": n},
            reason=cleaned,
        )
        for row in rows:
            row.is_closed = True
        await s.flush()
        return n
