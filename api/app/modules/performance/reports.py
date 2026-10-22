# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P6-08: reports and analytics (PRD Feature 12). Each report is a scoped, read-only query --
built directly on indexed source tables rather than materialised views for this first cut (the
PRD's "refreshed every 15 minutes" materialised-view infrastructure is a follow-up; flagged, not
silently skipped). `renewals_churn` and the churn half of `measures` reuse Phase 5's existing
functions (churn/history.py, billing/metrics.py) rather than recomputing them.
"""

import uuid
from datetime import date, timedelta
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailed
from app.core.time import to_ist, utc_now
from app.modules.activities.models import Activity
from app.modules.billing.metrics import renewal_rate
from app.modules.churn import history as churn_history
from app.modules.leads.models import Deal, NextStep
from app.modules.performance.models import UserActivityDay
from app.modules.users.models import User
from app.platform.context import Actor
from app.platform.permissions.roles import Scope
from app.platform.permissions.scope import scope_conditions
from app.platform.reads import authorize_read

REPORT_NAMES = (
    "leads", "conversion", "won_lost", "revenue_billing", "renewals_churn", "rep_performance",
    "measures",
)  # fmt: skip


async def record_active_day(session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """First authenticated request of the IST day (PRD 12 'daily active use'). Call-site is a
    FastAPI dependency so every endpoint counts, not just report views."""
    today = to_ist(utc_now()).date()
    exists = (
        await session.execute(
            select(UserActivityDay.id).where(
                UserActivityDay.org_id == org_id, UserActivityDay.user_id == user_id,
                UserActivityDay.day == today,
            )
        )
    ).first()  # fmt: skip
    if exists:
        return
    session.add(UserActivityDay(org_id=org_id, user_id=user_id, day=today, first_seen_at=utc_now()))
    await session.flush()


async def _scope_and_deals(
    session: AsyncSession, actor: Actor, date_from: date | None, date_to: date | None
) -> tuple[Scope, list[Deal]]:
    decision = await authorize_read(session, actor, "reports.view")
    conds = scope_conditions(actor, Deal, decision.scope, team_attr="team_id")
    conds.append(Deal.deleted_at.is_(None))
    if date_from:
        conds.append(Deal.created_at >= date_from)
    if date_to:
        conds.append(Deal.created_at <= date_to)
    deals = (await session.execute(select(Deal).where(*conds))).scalars().all()
    return decision.scope, list(deals)


async def _leads_report(
    session: AsyncSession, actor: Actor, date_from: date | None, date_to: date | None
) -> dict[str, Any]:
    _scope, deals = await _scope_and_deals(session, actor, date_from, date_to)
    by_source: dict[str, int] = {}
    by_rep: dict[str, int] = {}
    response_days: list[float] = []
    for d in deals:
        by_source[d.lead_source] = by_source.get(d.lead_source, 0) + 1
        if d.owner_id:
            by_rep[str(d.owner_id)] = by_rep.get(str(d.owner_id), 0) + 1
        if d.assigned_at:
            first_activity = (
                await session.execute(
                    select(func.min(Activity.occurred_at)).where(Activity.deal_id == d.id)
                )
            ).scalar_one_or_none()
            if first_activity and first_activity >= d.assigned_at:
                response_days.append((first_activity - d.assigned_at).total_seconds() / 86400)
    avg_response = round(sum(response_days) / len(response_days), 2) if response_days else None
    return {
        "generated": len(deals), "by_source": by_source, "by_rep": by_rep,
        "avg_lead_response_days": avg_response,
    }  # fmt: skip


async def _conversion_report(
    session: AsyncSession, actor: Actor, date_from: date | None, date_to: date | None
) -> dict[str, Any]:
    decision = await authorize_read(session, actor, "reports.view")
    conds = scope_conditions(actor, Deal, decision.scope, team_attr="team_id")
    conds.append(Deal.deleted_at.is_(None))
    deals = (await session.execute(select(Deal).where(*conds))).scalars().all()
    by_stage: dict[str, int] = {}
    won = lost = 0
    by_rep_won: dict[str, int] = {}
    by_rep_lost: dict[str, int] = {}
    for d in deals:
        if d.status == "open":
            by_stage[d.stage_key] = by_stage.get(d.stage_key, 0) + 1
        elif d.status == "won":
            won += 1
            if d.owner_id:
                by_rep_won[str(d.owner_id)] = by_rep_won.get(str(d.owner_id), 0) + 1
        elif d.status == "lost":
            lost += 1
            if d.owner_id:
                by_rep_lost[str(d.owner_id)] = by_rep_lost.get(str(d.owner_id), 0) + 1
    win_rate_by_rep = {
        rep: round(100 * w / (w + by_rep_lost.get(rep, 0)), 1)
        for rep, w in by_rep_won.items()
        if (w + by_rep_lost.get(rep, 0)) > 0
    }
    return {
        "pipeline_by_stage": by_stage, "won": won, "lost": lost,
        "win_rate_pct": round(100 * won / (won + lost), 1) if (won + lost) else None,
        "win_rate_by_rep": win_rate_by_rep,
    }  # fmt: skip


async def _won_lost_report(
    session: AsyncSession, actor: Actor, date_from: date | None, date_to: date | None
) -> dict[str, Any]:
    _scope, deals = await _scope_and_deals(session, actor, date_from, date_to)
    won = [d for d in deals if d.status == "won"]
    lost = [d for d in deals if d.status == "lost"]
    lost_reasons: dict[str, int] = {}
    lost_by_stage: dict[str, int] = {}
    for d in lost:
        lost_reasons[d.lost_reason or "Unspecified"] = (
            lost_reasons.get(d.lost_reason or "Unspecified", 0) + 1
        )
        key = d.lost_from_stage_key or d.stage_key
        lost_by_stage[key] = lost_by_stage.get(key, 0) + 1
    return {
        "won_count": len(won), "won_value_paise": sum(d.won_value_paise or 0 for d in won),
        "lost_count": len(lost), "lost_reasons": lost_reasons, "lost_by_stage": lost_by_stage,
    }  # fmt: skip


async def _revenue_billing_report(
    session: AsyncSession, actor: Actor, date_from: date | None, date_to: date | None
) -> dict[str, Any]:
    decision = await authorize_read(session, actor, "reports.view")
    conds = scope_conditions(actor, Deal, decision.scope, team_attr="team_id")
    conds.extend([Deal.status == "won", Deal.deleted_at.is_(None)])
    if date_from:
        conds.append(Deal.won_at >= date_from)
    if date_to:
        conds.append(Deal.won_at <= date_to)
    # One expression object reused in SELECT/GROUP BY/ORDER BY: each `func.date_trunc(...)` call
    # otherwise binds "month" as its own separate parameter, and Postgres only matches a GROUP BY
    # clause against the exact same expression -- two calls with an equal-but-distinct bind
    # parameter don't count, raising "column deals.won_at must appear in the GROUP BY clause".
    month_trunc = func.date_trunc("month", Deal.won_at)
    rows = (
        await session.execute(
            select(
                month_trunc,
                func.coalesce(func.sum(Deal.won_value_paise), 0),
                func.count(),
            )
            .where(*conds)
            .group_by(month_trunc)
            .order_by(month_trunc)
        )
    ).all()
    return {
        "by_month": [{"month": m.date(), "revenue_paise": v, "deals": c} for m, v, c in rows],
        "total_revenue_paise": sum(v for _, v, _ in rows),
        "note": "Invoiced-vs-received and overdue amounts need a dedicated billing-table join; "
                "not included in this first cut (see module docstring).",
    }  # fmt: skip


async def _rep_performance_report(
    session: AsyncSession, actor: Actor, date_from: date | None, date_to: date | None
) -> dict[str, Any]:
    decision = await authorize_read(session, actor, "reports.view")
    conds = [User.org_id == actor.org_id, User.role == "user", User.deleted_at.is_(None)]
    if decision.scope == Scope.TEAM:
        conds.append(User.team_id == actor.team_id)
    elif decision.scope == Scope.OWN:
        conds.append(User.id == actor.id)
    elif decision.scope != Scope.ALL:
        return {"reps": []}
    users = (await session.execute(select(User).where(*conds))).scalars().all()
    out = []
    for u in users:
        _scope, deals = await _scope_and_deals(session, actor, date_from, date_to)
        mine = [d for d in deals if d.owner_id == u.id]
        won = sum(1 for d in mine if d.status == "won")
        lost = sum(1 for d in mine if d.status == "lost")
        acts = (
            await session.execute(
                select(func.count()).where(
                    Activity.org_id == actor.org_id, Activity.attributed_to == u.id,
                    Activity.deleted_at.is_(None),
                )
            )
        ).scalar_one()  # fmt: skip
        out.append(
            {
                "user_id": u.id,
                "name": u.name,
                "activities": acts,
                "deals": len(mine),
                "won": won,
                "lost": lost,
                "win_rate_pct": round(100 * won / (won + lost), 1) if (won + lost) else None,
                "revenue_paise": sum(d.won_value_paise or 0 for d in mine if d.status == "won"),
            }
        )
    return {"reps": out}


async def _measures_report(session: AsyncSession, actor: Actor) -> dict[str, Any]:
    """PRD section 12's tracked-measures snapshot."""
    decision = await authorize_read(session, actor, "reports.view")
    conds = scope_conditions(actor, Deal, decision.scope, team_attr="team_id")
    open_conds = [*conds, Deal.status == "open", Deal.deleted_at.is_(None)]
    open_deals = (await session.execute(select(Deal).where(*open_conds))).scalars().all()
    no_next_step = 0
    for d in open_deals:
        has_open_step = (
            await session.execute(
                select(NextStep.id)
                .where(NextStep.deal_id == d.id, NextStep.done_at.is_(None))
                .limit(1)
            )
        ).first()
        if not has_open_step:
            no_next_step += 1
    stale = sum(1 for d in open_deals if d.health == "stale")
    today = to_ist(utc_now()).date()
    window_start = today - timedelta(days=30)
    dau_rows = (
        await session.execute(
            select(func.count(func.distinct(UserActivityDay.user_id))).where(
                UserActivityDay.org_id == actor.org_id, UserActivityDay.day >= window_start,
            )
        )
    ).scalar_one()  # fmt: skip
    rate = await renewal_rate(session, actor.org_id, window_start, today)
    churn = await churn_history.churn_rate(session, actor.org_id, window_start, today)
    return {
        "open_deals_without_next_step": no_next_step,
        "open_deals_total": len(open_deals),
        "stale_share_pct": round(100 * stale / len(open_deals), 1) if open_deals else None,
        "daily_active_users_30d": dau_rows,
        "renewal_rate_30d": float(rate) if rate is not None else None,
        "churn_rate_30d": float(churn) if churn is not None else None,
    }


async def get_report(
    session: AsyncSession,
    actor: Actor,
    name: Literal[
        "leads",
        "conversion",
        "won_lost",
        "revenue_billing",
        "renewals_churn",
        "rep_performance",
        "measures",
    ],  # fmt: skip
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    if name == "leads":
        return await _leads_report(session, actor, date_from, date_to)
    if name == "conversion":
        return await _conversion_report(session, actor, date_from, date_to)
    if name == "won_lost":
        return await _won_lost_report(session, actor, date_from, date_to)
    if name == "revenue_billing":
        return await _revenue_billing_report(session, actor, date_from, date_to)
    if name == "renewals_churn":
        return await churn_history.summary(session, actor)
    if name == "rep_performance":
        return await _rep_performance_report(session, actor, date_from, date_to)
    if name == "measures":
        return await _measures_report(session, actor)
    raise ValidationFailed("Unknown report", {"field": "name"})
