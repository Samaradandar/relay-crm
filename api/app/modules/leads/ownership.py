# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""The one place a deal's owner changes. Assignment (rules, round-robin), manual assignment,
reassignment, bulk reassignment and transfers all go through here, so every owner change moves
the next steps, closes the Pool entry, writes history and tells the right people the same way."""

import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.modules.accounts.models import Account
from app.modules.leads import events
from app.modules.leads.models import Deal, LeadPoolEntry, NextStep
from app.modules.users.models import Team, User
from app.platform.uow import UnitOfWork


def deal_rt(deal: Deal, scope_key: str = "deal.view", **extra: Any) -> dict[str, Any]:
    return {
        "entity_type": "deal",
        "entity_id": str(deal.id),
        "owner_id": deal.owner_id,
        "team_id": deal.team_id,
        "scope_key": scope_key,
        **extra,
    }


def pool_rt(team_id: uuid.UUID | None) -> dict[str, Any]:
    """Wake the Lead Pool page of everyone whose share of the Pool this is."""
    return {
        "entity_type": "pool",
        "entity_id": str(team_id or ""),
        "owner_id": None,
        "team_id": team_id,
        "scope_key": "pool.view",
    }


async def active_lead_counts(
    session: AsyncSession, org_id: uuid.UUID, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """'Active leads' for a cap: deals the rep owns that are open and not deleted.
    One function, so the definition can change in one place (Phase 5 adds the second pipeline)."""
    if not user_ids:
        return {}
    rows = await session.execute(
        select(Deal.owner_id, func.count())
        .where(
            Deal.org_id == org_id,
            Deal.owner_id.in_(user_ids),
            Deal.status == "open",
            Deal.deleted_at.is_(None),
        )
        .group_by(Deal.owner_id)
    )
    counts = {uid: n for uid, n in rows}
    return {uid: counts.get(uid, 0) for uid in user_ids}


async def names(
    session: AsyncSession, user_ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, str]:
    ids = {i for i in user_ids if i}
    if not ids:
        return {}
    return {
        u.id: u.name
        for u in (await session.execute(select(User).where(User.id.in_(ids)))).scalars()
    }


async def team_names(
    session: AsyncSession, team_ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, str]:
    ids = {i for i in team_ids if i}
    if not ids:
        return {}
    return {
        t.id: t.name
        for t in (await session.execute(select(Team).where(Team.id.in_(ids)))).scalars()
    }


async def company_name(session: AsyncSession, deal: Deal) -> str:
    acc = await session.get(Account, deal.account_id)
    return acc.name if acc else "a company"


async def open_pool_entry(
    session: AsyncSession, deal_id: uuid.UUID, lock: bool = False
) -> LeadPoolEntry | None:
    stmt = select(LeadPoolEntry).where(
        LeadPoolEntry.deal_id == deal_id, LeadPoolEntry.left_at.is_(None)
    )
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def apply_owner(
    uow: UnitOfWork,
    deal: Deal,
    target: User,
    *,
    method: str,
    action_key: str,
    by: uuid.UUID | None,
    rule_id: uuid.UUID | None = None,
    reason: str | None = None,
    note: str | None = None,
) -> None:
    """Give `deal` to `target`. `by` is None when the system (a rule or round-robin) decided."""
    s = uow.session
    nm = await names(s, {deal.owner_id, target.id})
    teams = await team_names(s, {deal.team_id, target.team_id})
    prev_owner = deal.owner_id
    old = {
        "owner": nm.get(prev_owner) if prev_owner else None,
        "team": teams.get(deal.team_id) if deal.team_id else None,
        "status": deal.status,
    }
    deal.owner_id, deal.team_id, deal.status = target.id, target.team_id, "open"
    deal.assigned_at, deal.assignment_method = utc_now(), method
    deal.assignment_rule_id, deal.assigned_by = rule_id, by
    deal.version, deal.updated_at = deal.version + 1, utc_now()
    entry = await open_pool_entry(s, deal.id, lock=True)
    if entry is not None:
        entry.left_at, entry.left_reason = utc_now(), "assigned"
    await s.execute(  # open next steps move with the deal
        update(NextStep)
        .where(NextStep.deal_id == deal.id, NextStep.done_at.is_(None))
        .values(responsible_id=target.id)
    )
    await s.flush()
    new = {
        "owner": target.name,
        "team": teams.get(target.team_id) if target.team_id else None,
        "status": "open",
        "method": method,
    }
    await uow.audit(action_key, "deal", deal.id, old=old, new=new, reason=reason, note=note)
    company = await company_name(s, deal)
    if prev_owner is None:
        await uow.emit(
            events.LEAD_ASSIGNED,
            {
                "deal_id": str(deal.id),
                "owner_id": str(target.id),
                "company": company,
                "method": method,
            },
            rt=deal_rt(deal, user_ids=[str(target.id)]),
        )
    else:
        await uow.emit(
            events.DEAL_OWNER_CHANGED,
            {
                "deal_id": str(deal.id),
                "from_id": str(prev_owner),
                "to_id": str(target.id),
                "company": company,
            },
            rt=deal_rt(deal, user_ids=[str(prev_owner), str(target.id)]),
        )
    await uow.emit(events.POOL_CHANGED, {"deal_id": str(deal.id)}, rt=pool_rt(target.team_id))


async def return_to_pool(
    uow: UnitOfWork,
    deal: Deal,
    team_id: uuid.UUID | None,
    *,
    source: str,
    hold: bool = False,
    reason: str | None = None,
) -> None:
    """Put a deal back in the Lead Pool (a departing rep, a transfer). `hold` keeps the engine
    away so the receiving Team Leader assigns it by hand."""
    s = uow.session
    nm = await names(s, {deal.owner_id})
    old = {"owner": nm.get(deal.owner_id) if deal.owner_id else None, "status": deal.status}
    prev_owner = deal.owner_id
    deal.owner_id, deal.team_id, deal.status = None, team_id, "pooled"
    deal.assignment_method = deal.assigned_by = deal.assignment_rule_id = None
    deal.version, deal.updated_at = deal.version + 1, utc_now()
    await s.execute(
        update(NextStep)
        .where(NextStep.deal_id == deal.id, NextStep.done_at.is_(None))
        .values(responsible_id=None)
    )
    waiting = await open_pool_entry(s, deal.id, lock=True)
    if waiting is not None:  # already waiting in the Pool: close that entry, open the new one
        waiting.left_at, waiting.left_reason = utc_now(), "moved"
        await s.flush()
    s.add(
        LeadPoolEntry(
            org_id=deal.org_id,
            deal_id=deal.id,
            entered_by=uow.actor.id,
            entered_source=source,
            routed_team_id=team_id,
            manual_hold=hold,
        )
    )
    await s.flush()
    await uow.audit(
        "lead.reassign",
        "deal",
        deal.id,
        old=old,
        new={"status": "pooled", "note": source},
        reason=reason,
    )
    await uow.emit(events.POOL_CHANGED, {"deal_id": str(deal.id)}, rt=pool_rt(team_id))
    if prev_owner:
        await uow.emit(
            events.DEAL_OWNER_CHANGED,
            {
                "deal_id": str(deal.id),
                "from_id": str(prev_owner),
                "to_id": None,
                "company": await company_name(s, deal),
            },
            rt=deal_rt(deal, user_ids=[str(prev_owner)]),
        )
    if not hold:
        await uow.emit(events.DEAL_POOLED, {"deal_id": str(deal.id)})


async def release_rep_hold(
    uow: UnitOfWork,
    deal: Deal,
    entry: LeadPoolEntry,
    *,
    action_key: str,
    note: str,
    new: dict[str, Any] | None = None,
    reason: str | None = None,
) -> None:
    """End the Team Leader hold on a rep-created lead so the engine may pick it (idempotent: the
    caller checks `entry.rep_hold`). One audit row; the `deal.pooled` event is the engine's
    safety net if the caller's own inline engine run fails. `tl_notified` is reset so a later
    'nobody is eligible' wait still tells the Team Leader once."""
    s = uow.session
    entry.manual_hold, entry.rep_hold, entry.tl_notified = False, False, False
    deal.version, deal.updated_at = deal.version + 1, utc_now()
    await s.flush()
    await uow.audit(
        action_key,
        "deal",
        deal.id,
        old={"hold": "team_leader"},
        new={"hold": "released", **(new or {})},
        reason=reason,
        note=note,
    )
    await uow.emit(events.POOL_CHANGED, {"deal_id": str(deal.id)}, rt=pool_rt(deal.team_id))
    await uow.emit(events.DEAL_POOLED, {"deal_id": str(deal.id)})
