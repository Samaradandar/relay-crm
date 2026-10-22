# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""P6-01: the stats engine. `rep_month_stats` is the only table the leaderboard, personal
dashboard and reports read money/rank figures from -- this module is the only place that writes
it. Called from three places: live outbox handlers (handlers.py, one rep/month at a time),
the nightly job (jobs.py) and the month-close job (jobs.py), and the Admin/SA "recompute a closed
month" action (service.py).
"""

import logging
import uuid
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import IST, ist_month_bounds, utc_now
from app.modules.leads.models import Deal
from app.modules.performance.models import RepMonthStats, Target
from app.modules.pipeline.config.models import Stage
from app.modules.users.models import User
from app.platform.permissions.roles import Role

log = logging.getLogger("relay.performance.stats")


def current_ist_month(now: datetime | None = None) -> date:
    start, _ = ist_month_bounds(now or utc_now())
    return start.astimezone(IST).date()


def month_bounds_utc(month: date) -> tuple[datetime, datetime]:
    """[start, end) in UTC for the IST calendar month starting on `month` (the 1st)."""
    local_midnight = datetime(month.year, month.month, 1, tzinfo=IST)
    return ist_month_bounds(local_midnight)


def next_month(month: date) -> date:
    return date(month.year + 1, 1, 1) if month.month == 12 else date(month.year, month.month + 1, 1)


async def is_sales_rep(session: AsyncSession, user_id: uuid.UUID) -> bool:
    role = (await session.execute(select(User.role).where(User.id == user_id))).scalar_one_or_none()
    return role == Role.USER


async def _stage_probability(session: AsyncSession, deal: Deal) -> int:
    if deal.pipeline_version_id is None:
        return 0
    pct = (
        await session.execute(
            select(Stage.probability_pct).where(
                Stage.version_id == deal.pipeline_version_id, Stage.key == deal.stage_key
            )
        )
    ).scalar_one_or_none()
    return pct or 0


async def _active_deals(session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID) -> int:
    return (
        await session.execute(
            select(func.count(Deal.id)).where(
                Deal.org_id == org_id,
                Deal.owner_id == user_id,
                Deal.status == "open",
                Deal.deleted_at.is_(None),
            )
        )
    ).scalar_one()


async def _won_this_month(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, month: date
) -> tuple[int, int]:
    """(won_deals, revenue_current_paise) -- both pipelines, by won_at falling in the IST month."""
    start, end = month_bounds_utc(month)
    rows = (
        (
            await session.execute(
                select(Deal.won_value_paise).where(
                    Deal.org_id == org_id,
                    Deal.won_by_user_id == user_id,
                    Deal.won_at >= start,
                    Deal.won_at < end,
                    Deal.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    return len(rows), sum(v or 0 for v in rows)


async def _lost_this_month(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, month: date
) -> int:
    """Not part of the PRD's rep_month_stats definition, but the frontend contract's RepSummary
    (section 12a) needs a lostMonth figure served the same way as every other count here."""
    start, end = month_bounds_utc(month)
    return (
        await session.execute(
            select(func.count(Deal.id)).where(
                Deal.org_id == org_id,
                Deal.owner_id == user_id,
                Deal.status == "lost",
                Deal.lost_at >= start,
                Deal.lost_at < end,
                Deal.deleted_at.is_(None),
            )
        )
    ).scalar_one()


async def _projected_this_month(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, month: date
) -> int:
    """Sum(value_paise * stage probability) over the rep's OPEN deals whose expected_close_date
    falls in `month` (calendar comparison -- expected_close_date is a plain Date, no IST
    conversion needed). Deals with no expected close date are excluded (PRD)."""
    upper = next_month(month)
    deals = (
        (
            await session.execute(
                select(Deal).where(
                    Deal.org_id == org_id,
                    Deal.owner_id == user_id,
                    Deal.status == "open",
                    Deal.deleted_at.is_(None),
                    Deal.expected_close_date.is_not(None),
                    Deal.expected_close_date >= month,
                    Deal.expected_close_date < upper,
                )
            )
        )
        .scalars()
        .all()
    )
    total = 0
    for d in deals:
        total += (d.value_paise or 0) * (await _stage_probability(session, d)) // 100
    return total


async def _best_to_date(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, month: date, current_paise: int
) -> int:
    prior_max = (
        await session.execute(
            select(func.max(RepMonthStats.revenue_current_paise)).where(
                RepMonthStats.org_id == org_id,
                RepMonthStats.user_id == user_id,
                RepMonthStats.month != month,
            )
        )
    ).scalar_one_or_none() or 0
    return max(prior_max, current_paise)


async def recompute_rep_month(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID, month: date
) -> RepMonthStats | None:
    """Recompute one rep's one month from source tables. No-op (returns the existing row
    unchanged) for a month already closed -- closed months are frozen until an explicit
    Admin/SA "recompute month" action (PRD)."""
    row = (
        await session.execute(
            select(RepMonthStats).where(
                RepMonthStats.org_id == org_id,
                RepMonthStats.user_id == user_id,
                RepMonthStats.month == month,
            )
        )
    ).scalar_one_or_none()
    if row is not None and row.is_closed:
        return row

    user = await session.get(User, user_id)
    if user is None or user.role != Role.USER:
        return row  # TLs/Admins/SAs are never ranked and have no stats row

    active_deals = await _active_deals(session, org_id, user_id)
    won_deals, revenue_current = await _won_this_month(session, org_id, user_id, month)
    lost_deals = await _lost_this_month(session, org_id, user_id, month)
    revenue_projected = revenue_current + await _projected_this_month(
        session, org_id, user_id, month
    )
    best = await _best_to_date(session, org_id, user_id, month, revenue_current)
    target_row = (
        await session.execute(
            select(Target.amount_paise).where(
                Target.org_id == org_id, Target.user_id == user_id, Target.month == month
            )
        )
    ).scalar_one_or_none()

    if row is None:
        row = RepMonthStats(org_id=org_id, user_id=user_id, month=month, computed_at=utc_now())
        session.add(row)
    else:
        changed = (
            row.active_deals != active_deals
            or row.won_deals != won_deals
            or row.revenue_current_paise != revenue_current
            or row.revenue_projected_paise != revenue_projected
        )
        if changed:
            log.warning(
                "rep_month_stats drift org=%s user=%s month=%s: "
                "active %s->%s won %s->%s current %s->%s projected %s->%s",
                org_id, user_id, month, row.active_deals, active_deals, row.won_deals, won_deals,
                row.revenue_current_paise, revenue_current, row.revenue_projected_paise,
                revenue_projected,
            )  # fmt: skip

    row.team_id = user.team_id
    row.active_deals = active_deals
    row.won_deals = won_deals
    row.lost_deals = lost_deals
    row.revenue_current_paise = revenue_current
    row.revenue_projected_paise = revenue_projected
    row.best_to_date_paise = best
    row.target_paise = target_row
    row.computed_at = utc_now()
    await session.flush()
    return row


async def recompute_ranks_for_month(session: AsyncSession, org_id: uuid.UUID, month: date) -> None:
    """Rank: Revenue Current desc, then won deals desc, then active deals desc; ties share a
    rank (1,2,2,4) and are ordered alphabetically among themselves. A deactivated rep's row keeps
    its values but gets no rank (removed from the current month's live ranking; past months are
    frozen and keep whatever rank they had at close, so this function is only ever called for the
    live, not-yet-closed month)."""
    rows = (
        await session.execute(
            select(RepMonthStats, User.name, User.is_active)
            .join(User, User.id == RepMonthStats.user_id)
            .where(
                RepMonthStats.org_id == org_id,
                RepMonthStats.month == month,
                RepMonthStats.is_closed.is_(False),
            )
        )
    ).all()
    ranked = sorted(
        (r for r in rows if r[2]),
        key=lambda r: (-r[0].revenue_current_paise, -r[0].won_deals, -r[0].active_deals, r[1]),
    )
    prev_key: tuple[int, int, int] | None = None
    rank = 0
    for i, (row, _name, _active) in enumerate(ranked, start=1):
        key = (row.revenue_current_paise, row.won_deals, row.active_deals)
        if key != prev_key:
            rank = i
        row.rank = rank
        prev_key = key
    for row, _name, active in rows:
        if not active:
            row.rank = None
    await session.flush()


async def recompute_org_month(session: AsyncSession, org_id: uuid.UUID, month: date) -> int:
    """Every active sales rep in the org, for one month. Used by the nightly job and by the
    Admin/SA recompute action. Returns the number of reps recomputed."""
    user_ids = (
        (
            await session.execute(
                select(User.id).where(
                    User.org_id == org_id, User.role == Role.USER, User.is_active.is_(True)
                )
            )
        )
        .scalars()
        .all()
    )
    for user_id in user_ids:
        await recompute_rep_month(session, org_id, user_id, month)
    await recompute_ranks_for_month(session, org_id, month)
    return len(user_ids)


async def close_month_if_due(session: AsyncSession, org_id: uuid.UUID, today_ist: date) -> bool:
    """Idempotent: a no-op unless `today_ist` is the 1st. On the 1st, freezes every open row for
    the month that just ended and makes sure a (zero) row exists this month for every active
    sales rep, with Best carried forward."""
    if today_ist.day != 1:
        return False
    prev_month = (
        date(today_ist.year, today_ist.month - 1, 1)
        if today_ist.month > 1
        else date(today_ist.year - 1, 12, 1)
    )
    rows = (
        (
            await session.execute(
                select(RepMonthStats).where(
                    RepMonthStats.org_id == org_id,
                    RepMonthStats.month == prev_month,
                    RepMonthStats.is_closed.is_(False),
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.is_closed = True
    await session.flush()
    this_month = today_ist.replace(day=1)
    await recompute_org_month(session, org_id, this_month)
    return True
