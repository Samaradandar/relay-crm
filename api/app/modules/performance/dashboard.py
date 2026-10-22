# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Section 12a: the frontend contract. Three screens already call these shapes on mock data;
Phase 6 fills the money fields (revenueMonth, target, wonMonth, lostMonth, weighted) from
rep_month_stats, the only read model (never raw deals, per PRD). The workload fields on
RepSummary (active leads, overdue, stale, pipeline by stage, cap, generated/assigned) are a
Phase 2/3 concern -- reused here from profiles.service and leads.ownership/assignment rather than
rebuilt, since nothing had assembled this particular response shape before Phase 6.
"""

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.core.time import IST, to_ist, utc_now
from app.modules.deals import today as today_mod
from app.modules.leads.assignment.service import caps_for
from app.modules.leads.models import Deal
from app.modules.leads.ownership import active_lead_counts
from app.modules.performance import forecast as forecast_mod
from app.modules.performance import stats
from app.modules.performance.models import RepMonthStats
from app.modules.pipeline.engine.service import published_version, stages_of
from app.modules.profiles.service import (
    _figures,  # noqa: SLF001 - cross-module reuse, see churn/deals handlers
)
from app.modules.users.models import Team, User
from app.platform.context import Actor
from app.platform.permissions.roles import Role
from app.platform.reads import authorize_read

PIPELINE = "new_business"


async def _meetings_this_week(
    session: AsyncSession, org_id: uuid.UUID, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not user_ids:
        return {}
    from app.modules.activities.models import Activity  # local import avoids a module cycle

    today_ist = to_ist(utc_now()).date()
    start_ist = today_ist - timedelta(days=today_ist.weekday())
    start = (
        to_ist(utc_now())
        .replace(
            year=start_ist.year,
            month=start_ist.month,
            day=start_ist.day,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        .astimezone(IST)
        .astimezone(tz=None)
    )
    start_utc = start.astimezone(tz=utc_now().tzinfo)
    rows = (
        await session.execute(
            select(Activity.attributed_to, Activity.id).where(
                Activity.org_id == org_id,
                Activity.attributed_to.in_(user_ids),
                Activity.type == "meeting",
                Activity.meeting_status == "held",
                Activity.deleted_at.is_(None),
                Activity.occurred_at >= start_utc,
            )
        )
    ).all()
    out = {u: 0 for u in user_ids}
    for uid, _id in rows:
        if uid is not None:
            out[uid] += 1
    return out


async def _stale_counts(
    session: AsyncSession, org_id: uuid.UUID, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    if not user_ids:
        return {}
    rows = (
        await session.execute(
            select(Deal.owner_id, Deal.id).where(
                Deal.org_id == org_id,
                Deal.owner_id.in_(user_ids),
                Deal.status == "open",
                Deal.health == "stale",
                Deal.deleted_at.is_(None),
            )
        )
    ).all()
    out = {u: 0 for u in user_ids}
    for uid, _id in rows:
        if uid is not None:
            out[uid] += 1
    return out


async def rep_summaries(
    session: AsyncSession, org_id: uuid.UUID, users: list[User], month: date
) -> list[dict[str, Any]]:
    """RepSummary rows for a batch of sales reps (team or company). Non-sales-rep users (TLs,
    Admins, SAs) get zeroed money fields -- they are never ranked and have no rep_month_stats
    row, but a TL's team roster still needs a row shape for the UI."""
    user_ids = [u.id for u in users]
    if not user_ids:
        return []
    start = to_ist(utc_now()) - timedelta(days=30)
    fig = await _figures(session, org_id, user_ids, start, utc_now())
    load = await active_lead_counts(session, org_id, user_ids)
    caps = await caps_for(session, org_id, user_ids)
    meetings = await _meetings_this_week(session, org_id, user_ids)
    stale = await _stale_counts(session, org_id, user_ids)
    money_rows = (
        (
            await session.execute(
                select(RepMonthStats).where(
                    RepMonthStats.org_id == org_id,
                    RepMonthStats.user_id.in_(user_ids),
                    RepMonthStats.month == month,
                )
            )
        )
        .scalars()
        .all()
    )
    money = {r.user_id: r for r in money_rows}

    version = await published_version(session, org_id, PIPELINE)
    stages = await stages_of(session, version.id) if version else {}
    by_user_stage: dict[uuid.UUID, dict[str, int]] = {}
    if user_ids:
        for uid, key, _n in (
            await session.execute(
                select(Deal.owner_id, Deal.stage_key, Deal.id).where(
                    Deal.org_id == org_id,
                    Deal.owner_id.in_(user_ids),
                    Deal.status == "open",
                    Deal.deleted_at.is_(None),
                )
            )
        ).all():
            if uid is None:
                continue
            by_user_stage.setdefault(uid, {}).setdefault(key, 0)
            by_user_stage[uid][key] += 1

    out = []
    for u in users:
        f = fig.get(u.id, {})
        m = money.get(u.id)
        out.append(
            {
                "id": u.id,
                "name": u.name,
                "email": u.email,
                "active": u.is_active,
                "cap": caps.get(u.id, 0),
                "active_leads": load.get(u.id, 0),
                "overdue": f.get("overdue", 0),
                "stale": stale.get(u.id, 0),
                "due_today": f.get("due_today", 0),
                "meetings_week": meetings.get(u.id, 0),
                "won_month": m.won_deals if m else 0,
                "lost_month": m.lost_deals if m else 0,
                "revenue_month": m.revenue_current_paise if m else 0,
                "target": m.target_paise if m else None,
                "last_activity_at": f.get("last_interaction"),
                "by_stage": [
                    {
                        "key": st.key,
                        "label": st.name,
                        "count": by_user_stage.get(u.id, {}).get(st.key, 0),
                    }
                    for st in stages.values()
                    if not st.won_on_entry
                ],
                "generated": f.get("generated", 0),
                "assigned": f.get("assigned", 0),
            }
        )
    return out


async def my_numbers(session: AsyncSession, actor: Actor) -> dict[str, Any] | None:
    """Sales reps only -- a Team Leader who owns deals still gets 404 here (PRD; the frontend
    then hides the strip) unless a future decision gives TLs a dashboard."""
    if actor.role != Role.USER:
        return None
    month = stats.current_ist_month()
    row = (
        await session.execute(
            select(RepMonthStats).where(
                RepMonthStats.org_id == actor.org_id,
                RepMonthStats.user_id == actor.id,
                RepMonthStats.month == month,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return {
            "revenue_month": 0, "target": 0, "best": 0, "projected": 0, "rank": 0, "reps": 0,
            "behind_next": None, "won_month": 0,
        }  # fmt: skip
    reps_count = (
        await session.execute(
            select(RepMonthStats.id).where(
                RepMonthStats.org_id == actor.org_id,
                RepMonthStats.month == month,
                RepMonthStats.rank.is_not(None),
            )
        )
    ).all()
    behind_next = None
    if row.rank is not None and row.rank > 1:
        ahead = (
            await session.execute(
                select(RepMonthStats.revenue_current_paise)
                .where(
                    RepMonthStats.org_id == actor.org_id,
                    RepMonthStats.month == month,
                    RepMonthStats.rank == row.rank - 1,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if ahead is not None:
            behind_next = max(0, ahead - row.revenue_current_paise)
    return {
        "revenue_month": row.revenue_current_paise,
        "target": row.target_paise or 0,
        "best": row.best_to_date_paise,
        "projected": row.revenue_projected_paise,
        "rank": row.rank or 0,
        "reps": len(reps_count),
        "behind_next": behind_next,
        "won_month": row.won_deals,
    }


async def _team_users(session: AsyncSession, org_id: uuid.UUID, team_id: uuid.UUID) -> list[User]:
    rows = (
        (
            await session.execute(
                select(User).where(
                    User.org_id == org_id,
                    User.team_id == team_id,
                    User.role == Role.USER,
                    User.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def team_overview(session: AsyncSession, actor: Actor) -> dict[str, Any]:
    # Team Leader (own team) / Admin / Super admin only -- a User must never see teammates'
    # individual revenue/overdue/stage figures (PRD: supervising a team is TL+, not a peer).
    # `today.view` (own/team/all/all) was used here before; a User's "own" scope made
    # `authorize_read` pass them straight through to the whole team's data below. BUG-P6-xx.
    await authorize_read(session, actor, "profile.view_others")
    team_id = actor.team_id
    if team_id is None:
        raise NotFound("Not in a team")
    users = await _team_users(session, actor.org_id, team_id)
    month = stats.current_ist_month()
    reps = await rep_summaries(session, actor.org_id, users, month)
    open_deals = (
        (
            await session.execute(
                select(Deal).where(
                    Deal.org_id == actor.org_id,
                    Deal.team_id == team_id,
                    Deal.status == "open",
                    Deal.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    weighted = await forecast_mod.weighted_total(session, actor.org_id, team_id=team_id)
    return {
        "reps": reps,
        "revenue_month": sum(r["revenue_month"] for r in reps),
        "target": sum(r["target"] or 0 for r in reps),
        "open_value": sum(d.value_paise or 0 for d in open_deals),
        "weighted": weighted,
        "overdue": sum(r["overdue"] for r in reps),
        "stale": sum(r["stale"] for r in reps),
        "open_deals": len(open_deals),
    }


async def rep_profile_for(
    session: AsyncSession, actor: Actor, user_id: uuid.UUID
) -> dict[str, Any]:
    user = (
        await session.execute(select(User).where(User.id == user_id, User.org_id == actor.org_id))
    ).scalar_one_or_none()
    if user is None:
        raise NotFound("Not found")
    today = await today_mod.today_view(session, actor, user_id)  # does its own TL/Admin/SA check
    month = stats.current_ist_month()
    summary = (await rep_summaries(session, actor.org_id, [user], month))[0]
    lost_rows = (
        await session.execute(
            select(Deal.lost_reason, Deal.lost_at, Deal.account_id)
            .where(
                Deal.org_id == actor.org_id,
                Deal.owner_id == user_id,
                Deal.status == "lost",
                Deal.deleted_at.is_(None),
            )
            .order_by(Deal.lost_at.desc())
            .limit(20)
        )
    ).all()
    from app.modules.accounts.models import Account

    account_ids = {a for _, _, a in lost_rows if a}
    names = {}
    if account_ids:
        names = dict(
            (
                await session.execute(
                    select(Account.id, Account.name).where(Account.id.in_(account_ids))
                )
            ).all()
        )
    lost = [
        {"company": names.get(acc, "Unknown"), "reason": reason or "Unspecified", "at": at}
        for reason, at, acc in lost_rows
    ]
    from app.modules.activities.models import Activity

    recent = (
        (
            await session.execute(
                select(Activity)
                .where(
                    Activity.org_id == actor.org_id,
                    Activity.attributed_to == user_id,
                    Activity.deleted_at.is_(None),
                )
                .order_by(Activity.occurred_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    from app.modules.accounts.models import Account as AccountModel

    acc_ids2 = {a.account_id for a in recent if a.account_id}
    names2 = {}
    if acc_ids2:
        names2 = dict(
            (
                await session.execute(
                    select(AccountModel.id, AccountModel.name).where(AccountModel.id.in_(acc_ids2))
                )
            ).all()
        )
    return {
        "summary": summary,
        "today": today,
        "lost": lost,
        "recent": [
            {
                "id": a.id,
                "deal_id": a.deal_id,
                "company": names2.get(a.account_id, "Unknown"),
                "type": a.type,
                "occurred_at": a.occurred_at,
                "discussed": a.discussed,
            }  # fmt: skip
            for a in recent
        ],
    }


async def company_overview(session: AsyncSession, actor: Actor) -> dict[str, Any]:
    await authorize_read(session, actor, "teams.compare")
    month = stats.current_ist_month()
    teams = (
        (
            await session.execute(
                select(Team).where(Team.org_id == actor.org_id, Team.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    all_users = list(
        (
            await session.execute(
                select(User).where(
                    User.org_id == actor.org_id, User.role == Role.USER, User.deleted_at.is_(None)
                )
            )
        )
        .scalars()
        .all()
    )
    reps_out = await rep_summaries(session, actor.org_id, all_users, month)
    reps_by_team: dict[uuid.UUID | None, list[dict[str, Any]]] = {}
    for u, r in zip(all_users, reps_out, strict=True):
        reps_by_team.setdefault(u.team_id, []).append(r)

    teams_out = []
    for t in teams:
        team_reps = reps_by_team.get(t.id, [])
        open_deals = (
            (
                await session.execute(
                    select(Deal).where(
                        Deal.org_id == actor.org_id,
                        Deal.team_id == t.id,
                        Deal.status == "open",
                        Deal.deleted_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        leader_name = None
        if t.leader_id:
            leader_name = (
                await session.execute(select(User.name).where(User.id == t.leader_id))
            ).scalar_one_or_none()
        weighted = await forecast_mod.weighted_total(session, actor.org_id, team_id=t.id)
        caps = await caps_for(session, actor.org_id, [r["id"] for r in team_reps])
        teams_out.append(
            {
                "id": t.id,
                "name": t.name,
                "color": t.colour,
                "leader": leader_name,
                "reps": len(team_reps),
                "active_reps": sum(1 for r in team_reps if r["active"]),
                "revenue_month": sum(r["revenue_month"] for r in team_reps),
                "target": sum(r["target"] or 0 for r in team_reps),
                "open_deals": len(open_deals),
                "open_value": sum(d.value_paise or 0 for d in open_deals),
                "weighted": weighted,
                "overdue": sum(r["overdue"] for r in team_reps),
                "stale": sum(r["stale"] for r in team_reps),
                "at_cap": sum(
                    1
                    for r in team_reps
                    if caps.get(r["id"], 0) and r["active_leads"] >= caps.get(r["id"], 0)
                ),
                "pool_waiting": 0,  # Lead Pool count is a Phase 2 concern; not surfaced here
                "won_month": sum(r["won_month"] for r in team_reps),
            }
        )
    return {
        "teams": teams_out,
        "reps": [{**r, "team_id": u.team_id} for u, r in zip(all_users, reps_out, strict=True)],
        "failed_jobs": 0,
        "pending_approvals": 0,
    }
