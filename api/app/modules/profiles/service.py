# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Rep profiles, team monitoring and team comparison (PRD 7, 9). Every figure is calculated from
deals, activities and stage history, never typed in. Scope: a User sees their own profile, a Team
Leader their team's, Admin and Super admin everyone's."""

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.core.time import IST, to_ist, utc_now
from app.modules.accounts.models import Account
from app.modules.activities.models import Activity
from app.modules.deals import health
from app.modules.leads.assignment.service import caps_for
from app.modules.leads.models import Deal, NextStep
from app.modules.leads.ownership import active_lead_counts, team_names
from app.modules.performance import forecast as forecast_mod
from app.modules.performance import stats as perf_stats
from app.modules.performance.models import RepMonthStats
from app.modules.pipeline.config.models import Stage
from app.modules.pipeline.engine.models import DealStageEntry
from app.modules.pipeline.engine.service import published_version, stages_of
from app.modules.users.models import Team, User
from app.platform.context import Actor
from app.platform.permissions import loader
from app.platform.permissions.roles import Role, Scope
from app.platform.reads import authorize_read

PIPELINE = "new_business"


def _period(start: date | None, end: date | None) -> tuple[datetime, datetime]:
    """[from, to) in UTC; the bounds are IST midnights because the product reports IST days."""
    now = utc_now()

    def ist_midnight(d: date) -> datetime:
        try:
            return datetime.combine(d, datetime.min.time(), tzinfo=IST).astimezone(now.tzinfo)
        except OverflowError:  # the very first or last day of the calendar
            edge = datetime.min if d.year < 2000 else datetime.max
            return edge.replace(tzinfo=now.tzinfo)

    last = end if end else to_ist(now).date()
    e = (
        ist_midnight(last + timedelta(days=1))
        if last < date.max
        else datetime.max.replace(tzinfo=now.tzinfo)
    )
    s = ist_midnight(start) if start else e - timedelta(days=30)
    return s, e


async def _target(session: AsyncSession, actor: Actor, user_id: uuid.UUID) -> User:
    table = await loader.get_table(session)
    own = user_id == actor.id
    key = "profile.view_own" if own else "profile.view_others"
    rule = table[(actor.role_enum, key)]
    if rule.denied:
        raise NotFound("Not found")
    user = (
        await session.execute(select(User).where(User.id == user_id, User.org_id == actor.org_id))
    ).scalar_one_or_none()
    if user is None:
        raise NotFound("Not found")
    if not own:
        if rule.scope == Scope.TEAM and user.team_id != actor.team_id:
            raise NotFound("Not found")
        if user.role == Role.SUPER_ADMIN and not actor.is_privileged:
            raise NotFound("Not found")
    return user


async def _figures(
    session: AsyncSession,
    org_id: uuid.UUID,
    user_ids: list[uuid.UUID],
    start: datetime,
    end: datetime,
) -> dict[Any, dict[str, Any]]:
    """Counts per rep for the period, in a handful of grouped queries."""
    out: dict[Any, dict[str, Any]] = {
        u: {
            "assigned": 0,
            "generated": 0,
            "won": 0,
            "lost": 0,
            "lost_reasons": {},
            "activities": 0,
            "last_interaction": None,
            "next_action": None,
            "overdue": 0,
            "due_today": 0,
        }
        for u in user_ids
    }
    if not user_ids:
        return out
    base = [Deal.org_id == org_id, Deal.deleted_at.is_(None), Deal.pipeline == PIPELINE]
    for uid, n in (
        await session.execute(
            select(Deal.owner_id, func.count())
            .where(
                *base,
                Deal.owner_id.in_(user_ids),
                Deal.assigned_at >= start,
                Deal.assigned_at < end,
            )
            .group_by(Deal.owner_id)
        )
    ).all():
        out[uid]["assigned"] = n
    for uid, n in (
        await session.execute(
            select(Deal.generated_by, func.count())
            .where(
                *base,
                Deal.generated_by.in_(user_ids),
                Deal.created_at >= start,
                Deal.created_at < end,
            )
            .group_by(Deal.generated_by)
        )
    ).all():
        out[uid]["generated"] = n
    for uid, n in (
        await session.execute(
            select(Deal.owner_id, func.count())
            .where(
                *base,
                Deal.owner_id.in_(user_ids),
                Deal.status == "won",
                Deal.won_at >= start,
                Deal.won_at < end,
            )
            .group_by(Deal.owner_id)
        )
    ).all():
        out[uid]["won"] = n
    for uid, reason, n in (
        await session.execute(
            select(Deal.owner_id, Deal.lost_reason, func.count())
            .where(
                *base,
                Deal.owner_id.in_(user_ids),
                Deal.status == "lost",
                Deal.lost_at >= start,
                Deal.lost_at < end,
            )
            .group_by(Deal.owner_id, Deal.lost_reason)
        )
    ).all():
        out[uid]["lost"] += n
        out[uid]["lost_reasons"][reason or "Unspecified"] = n
    for uid, n, last in (
        await session.execute(
            select(Activity.attributed_to, func.count(), func.max(Activity.occurred_at))
            .where(
                Activity.org_id == org_id,
                Activity.attributed_to.in_(user_ids),
                Activity.deleted_at.is_(None),
                Activity.occurred_at >= start,
                Activity.occurred_at < end,
            )
            .group_by(Activity.attributed_to)
        )
    ).all():
        out[uid]["activities"], out[uid]["last_interaction"] = n, last
    today = health.ist_date(utc_now())
    for uid, due, _n in (
        await session.execute(
            select(NextStep.responsible_id, func.min(NextStep.due_date), func.count())
            .join(Deal, Deal.id == NextStep.deal_id)
            .where(
                *base,
                NextStep.responsible_id.in_(user_ids),
                NextStep.done_at.is_(None),
                Deal.status == "open",
            )
            .group_by(NextStep.responsible_id)
        )
    ).all():
        out[uid]["next_action"] = due
    for (
        uid,
        due,
    ) in (
        await session.execute(
            select(NextStep.responsible_id, NextStep.due_date)
            .join(Deal, Deal.id == NextStep.deal_id)
            .where(
                *base,
                NextStep.responsible_id.in_(user_ids),
                NextStep.done_at.is_(None),
                Deal.status == "open",
                NextStep.due_date <= today,
            )
        )
    ).all():
        if due < today:
            out[uid]["overdue"] += 1
        else:
            out[uid]["due_today"] += 1
    return out


def _rate(won: int, lost: int) -> float | None:
    return round(100 * won / (won + lost), 1) if won + lost else None


async def _money(
    session: AsyncSession, org_id: uuid.UUID, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[int, int | None]]:
    """This month's Revenue: Current and target per rep, from rep_month_stats (Phase 6) --
    never computed from raw deals here."""
    out: dict[uuid.UUID, tuple[int, int | None]] = dict.fromkeys(user_ids, (0, None))
    if not user_ids:
        return out
    month = perf_stats.current_ist_month()
    rows = await session.execute(
        select(
            RepMonthStats.user_id, RepMonthStats.revenue_current_paise, RepMonthStats.target_paise
        ).where(
            RepMonthStats.org_id == org_id,
            RepMonthStats.user_id.in_(user_ids),
            RepMonthStats.month == month,
        )
    )
    for uid, revenue, target in rows:
        out[uid] = (revenue, target)
    return out


async def rep_profile(
    session: AsyncSession, actor: Actor, user_id: uuid.UUID, start: date | None, end: date | None
) -> dict[str, Any]:
    user = await _target(session, actor, user_id)
    s_, e_ = _period(start, end)
    fig = (await _figures(session, actor.org_id, [user.id], s_, e_))[user.id]
    load = (await active_lead_counts(session, actor.org_id, [user.id]))[user.id]
    cap = (await caps_for(session, actor.org_id, [user.id]))[user.id]
    revenue_month, target = (await _money(session, actor.org_id, [user.id]))[user.id]
    version = await published_version(session, actor.org_id, PIPELINE)
    stages = await stages_of(session, version.id)
    by_stage = dict(
        (
            await session.execute(
                select(Deal.stage_key, func.count())
                .where(
                    Deal.org_id == actor.org_id,
                    Deal.owner_id == user.id,
                    Deal.status == "open",
                    Deal.deleted_at.is_(None),
                )
                .group_by(Deal.stage_key)
            )
        ).all()
    )
    pipeline = [
        {"key": st.key, "name": st.name, "open": by_stage.get(st.key, 0)}
        for st in stages.values()
        if not st.won_on_entry
    ]
    # conversion by stage: of the deals this rep owns that entered a stage, how many went further
    owned = select(Deal.id).where(
        Deal.org_id == actor.org_id, Deal.owner_id == user.id, Deal.deleted_at.is_(None)
    )
    visited: dict[uuid.UUID, set[str]] = {}
    for did, key in (
        await session.execute(
            select(DealStageEntry.deal_id, DealStageEntry.stage_key).where(
                DealStageEntry.deal_id.in_(owned),
                DealStageEntry.entered_at >= s_,
                DealStageEntry.entered_at < e_,
            )
        )
    ).all():
        visited.setdefault(did, set()).add(key)
    pos = {st.key: st.position for st in stages.values()}
    conv = []
    for st in stages.values():
        if st.won_on_entry:
            continue
        entered = [k for k in visited.values() if st.key in k]
        advanced = [k for k in entered if any(pos.get(x, 0) > st.position for x in k)]
        conv.append(
            {
                "key": st.key,
                "name": st.name,
                "entered": len(entered),
                "advanced": len(advanced),
                "pct": round(100 * len(advanced) / len(entered), 1) if entered else None,
            }
        )
    acts = list(
        (
            await session.execute(
                select(Activity, Account.name)
                .join(Account, Account.id == Activity.account_id)
                .where(
                    Activity.org_id == actor.org_id,
                    Activity.attributed_to == user.id,
                    Activity.deleted_at.is_(None),
                    Activity.occurred_at >= s_,
                    Activity.occurred_at < e_,
                )
                .order_by(Activity.occurred_at.desc())
                .limit(100)
            )
        ).all()
    )
    team = (await team_names(session, {user.team_id})).get(user.team_id) if user.team_id else None
    return {
        "user": {
            "id": user.id,
            "name": user.name,
            "email": user.email,
            "role": user.role,
            "team_name": team,
            "is_active": user.is_active,
            "cap": cap,
        },
        "period": {"from": s_, "to": e_ - timedelta(days=1)},
        "workload": {
            "leads_assigned": fig["assigned"],
            "leads_generated": fig["generated"],
            "active_leads": load,
            "cap": cap,
            "pipeline_by_stage": pipeline,
            "follow_ups_due_today": fig["due_today"],
            "follow_ups_overdue": fig["overdue"],
            "revenue_month_paise": revenue_month,
            "target_paise": target,
        },
        "outcomes": {
            "won": fig["won"],
            "lost": fig["lost"],
            "lost_reasons": fig["lost_reasons"],
            "win_rate_pct": _rate(fig["won"], fig["lost"]),
            "conversion_by_stage": conv,
        },
        "activities": {
            "total": fig["activities"],
            "recent": [
                {
                    "id": a.id,
                    "deal_id": a.deal_id,
                    "company": c,
                    "type": a.type,
                    "occurred_at": a.occurred_at,
                    "discussed": a.discussed,
                }
                for a, c in acts
            ],
        },
    }


async def monitoring(
    session: AsyncSession,
    actor: Actor,
    team_id: uuid.UUID | None,
    start: date | None,
    end: date | None,
) -> dict[str, Any]:
    decision = await authorize_read(session, actor, "profile.view_others")
    q = select(User).where(
        User.org_id == actor.org_id, User.role == Role.USER, User.deleted_at.is_(None)
    )
    if decision.scope == Scope.TEAM:
        q = q.where(User.team_id == actor.team_id)
    elif team_id:
        q = q.where(User.team_id == team_id)
    reps = list((await session.execute(q.order_by(User.name))).scalars())
    s_, e_ = _period(start, end)
    ids = [r.id for r in reps]
    fig = await _figures(session, actor.org_id, ids, s_, e_)
    load = await active_lead_counts(session, actor.org_id, ids)
    caps = await caps_for(session, actor.org_id, ids)
    money = await _money(session, actor.org_id, ids)
    stage_of: dict[Any, dict[str, int]] = {i: {} for i in ids}
    for uid, key, n in (
        (
            await session.execute(
                select(Deal.owner_id, Deal.stage_key, func.count())
                .where(
                    Deal.org_id == actor.org_id,
                    Deal.owner_id.in_(ids),
                    Deal.status == "open",
                    Deal.deleted_at.is_(None),
                )
                .group_by(Deal.owner_id, Deal.stage_key)
            )
        ).all()
        if ids
        else []
    ):
        stage_of[uid][key] = n
    teams = await team_names(session, {r.team_id for r in reps})
    return {
        "period": {"from": s_, "to": e_ - timedelta(days=1)},
        "reps": [
            {
                "user_id": r.id,
                "name": r.name,
                "team_id": r.team_id,
                "team_name": teams.get(r.team_id) if r.team_id else None,
                "is_active": r.is_active,
                "assigned": fig[r.id]["assigned"],
                "active_leads": load[r.id],
                "cap": caps[r.id],
                "by_stage": stage_of[r.id],
                "last_interaction": fig[r.id]["last_interaction"],
                "next_action": fig[r.id]["next_action"],
                "won": fig[r.id]["won"],
                "lost": fig[r.id]["lost"],
                "lost_reasons": fig[r.id]["lost_reasons"],
                "conversion_pct": _rate(fig[r.id]["won"], fig[r.id]["lost"]),
                "activities": fig[r.id]["activities"],
                "revenue_month_paise": money[r.id][0],
                "target_paise": money[r.id][1],
            }
            for r in reps
        ],
    }


async def compare_teams(
    session: AsyncSession, actor: Actor, start: date | None, end: date | None
) -> list[dict[str, Any]]:
    await authorize_read(session, actor, "teams.compare")
    s_, e_ = _period(start, end)
    teams = list(
        (
            await session.execute(
                select(Team)
                .where(Team.org_id == actor.org_id, Team.deleted_at.is_(None))
                .order_by(Team.name)
            )
        ).scalars()
    )
    out = []
    for t in teams:
        reps = list(
            (
                await session.execute(
                    select(User.id).where(
                        User.org_id == actor.org_id,
                        User.team_id == t.id,
                        User.role == Role.USER,
                        User.is_active.is_(True),
                    )
                )
            ).scalars()
        )
        fig = await _figures(session, actor.org_id, reps, s_, e_)
        won = sum(f["won"] for f in fig.values())
        lost = sum(f["lost"] for f in fig.values())
        loads = await active_lead_counts(session, actor.org_id, reps)
        value = (
            await session.execute(
                select(func.coalesce(func.sum(Deal.value_paise), 0)).where(
                    Deal.org_id == actor.org_id,
                    Deal.team_id == t.id,
                    Deal.owner_id.in_(reps),
                    Deal.status == "won",
                    Deal.won_at >= s_,
                    Deal.won_at < e_,
                    Deal.deleted_at.is_(None),
                )
            )
        ).scalar_one()
        money = await _money(session, actor.org_id, reps)
        weighted = await forecast_mod.weighted_total(session, actor.org_id, team_id=t.id)
        out.append(
            {
                "team_id": t.id,
                "name": t.name,
                "reps": len(reps),
                "active_leads": sum(loads.values()),
                "assigned": sum(f["assigned"] for f in fig.values()),
                "won": won,
                "lost": lost,
                "win_rate_pct": _rate(won, lost),
                "won_value_paise": int(value),
                "activities": sum(f["activities"] for f in fig.values()),
                "revenue_month_paise": sum(m[0] for m in money.values()),
                "target_paise": sum(m[1] or 0 for m in money.values()),
                "weighted_paise": weighted,
            }
        )
    return out


_ = Stage
