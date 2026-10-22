# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Churn History (P5-07): the scoped, filterable/sortable/paginated list of churn events, plus
the summary/breakdown numbers for the page's top cards and charts.

Scoped exactly like `customers.risk.at_risk_customers`: the churned customer's `account_owner_id`
decides own/team/all, using `customer.view`'s scope (User own, Team Leader team, Admin/SA all).

`churn_rate` mirrors `billing.metrics.renewal_rate`'s style: a small, pure, unit-testable
function taking only what it needs, not the whole request.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailed
from app.modules.accounts.models import Account
from app.modules.billing.schedule import ist_today
from app.modules.churn.models import ChurnEvent
from app.modules.customers.models import Customer
from app.modules.customers.service import VIEW_KEY
from app.modules.users.models import User
from app.platform.context import Actor
from app.platform.permissions import loader
from app.platform.permissions.roles import Scope
from app.platform.permissions.scope import scope_conditions
from app.platform.reads import authorize_read

PAGE_SIZES = (10, 25, 50)
SORTS = ("churn_date", "value_lost", "time_as_customer")
TEAMS_COMPARE_KEY = "teams.compare"


async def customer_scope_where(session: AsyncSession, actor: Actor) -> tuple[list[Any], Scope]:
    decision = await authorize_read(session, actor, VIEW_KEY)
    where = scope_conditions(
        actor, Customer, decision.scope, owner_attr="account_owner_id", hide_sa_owned=False
    )
    return where, decision.scope


def _row_out(
    event: ChurnEvent,
    account_id: uuid.UUID,
    account_name: str,
    owner_name: str | None,
    recorded_by_name: str | None,
) -> dict[str, Any]:
    return {
        "id": event.id,
        "customer_id": event.customer_id,
        "account_id": account_id,
        "account_name": account_name,
        "account_owner_name": owner_name,
        "churn_date": event.churn_date,
        "reason": event.reason,
        "notes": event.notes,
        "monthly_value_lost_paise": event.monthly_value_lost_paise,
        "subscription_start": event.subscription_start,
        "time_as_customer_days": event.time_as_customer_days,
        "last_renewal_date": event.last_renewal_date,
        "recorded_by": event.recorded_by,
        "recorded_by_name": recorded_by_name,
        "recorded_at": event.recorded_at,
        "returned_deal_id": event.returned_deal_id,
        "reversed_at": event.reversed_at,
        "returned": event.returned_deal_id is not None and event.reversed_at is None,
    }


def history_filters(
    *,
    date_from: date | None,
    date_to: date | None,
    owner_id: uuid.UUID | None,
    reason: str | None,
) -> list[Any]:
    where: list[Any] = []
    if date_from is not None:
        where.append(ChurnEvent.churn_date >= date_from)
    if date_to is not None:
        where.append(ChurnEvent.churn_date <= date_to)
    if owner_id is not None:
        where.append(Customer.account_owner_id == owner_id)
    if reason is not None:
        where.append(ChurnEvent.reason == reason)
    return where


async def list_churn_history(
    session: AsyncSession,
    actor: Actor,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    owner_id: uuid.UUID | None = None,
    reason: str | None = None,
    sort: str = "churn_date",
    desc: bool = True,
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    if page_size not in PAGE_SIZES:
        raise ValidationFailed("Page size must be 10, 25 or 50", {"field": "page_size"})
    if sort not in SORTS:
        raise ValidationFailed("Unknown sort", {"field": "sort"})
    scope_where, _ = await customer_scope_where(session, actor)
    where = [
        *scope_where,
        *history_filters(date_from=date_from, date_to=date_to, owner_id=owner_id, reason=reason),
    ]
    base = (
        select(ChurnEvent, Account.id, Account.name, User.name)
        .join(Customer, Customer.id == ChurnEvent.customer_id)
        .join(Account, Account.id == Customer.account_id)
        .outerjoin(User, User.id == Customer.account_owner_id)
        .where(*where)
    )
    total = (
        await session.execute(
            select(func.count())
            .select_from(ChurnEvent)
            .join(Customer, Customer.id == ChurnEvent.customer_id)
            .where(*where)
        )
    ).scalar_one()
    order_col = {
        "churn_date": ChurnEvent.churn_date,
        "value_lost": ChurnEvent.monthly_value_lost_paise,
        "time_as_customer": ChurnEvent.time_as_customer_days,
    }[sort]
    rows = (
        await session.execute(
            base.order_by(order_col.desc() if desc else order_col.asc(), ChurnEvent.id)
            .offset((max(page, 1) - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    recorded_by_ids = {e.recorded_by for e, _, _, _ in rows}
    recorded_names = (
        {
            u.id: u.name
            for u in (
                await session.execute(select(User).where(User.id.in_(recorded_by_ids)))
            ).scalars()
        }
        if recorded_by_ids
        else {}
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "rows": [
            _row_out(e, account_id, account_name, owner_name, recorded_names.get(e.recorded_by))
            for e, account_id, account_name, owner_name in rows
        ],
    }


async def churn_rate(
    session: AsyncSession, org_id: uuid.UUID, period_start: date, period_end: date
) -> Decimal | None:
    """Churned in [period_start, period_end] / active customers at the START of the period.

    'Active at the start' = a customer whose record already existed before `period_start` and
    was not churned before `period_start` (it may have churned later, inside the window, and
    still counts in the denominator). None when there were no such customers (undefined, not
    zero), mirroring `renewal_rate`'s None-for-undefined convention."""
    active_at_start = (
        await session.execute(
            select(func.count())
            .select_from(Customer)
            .where(
                Customer.org_id == org_id,
                Customer.deleted_at.is_(None),
                Customer.created_at < period_start,
                ~Customer.id.in_(
                    select(ChurnEvent.customer_id).where(
                        ChurnEvent.org_id == org_id,
                        ChurnEvent.churn_date < period_start,
                        ChurnEvent.reversed_at.is_(None),
                    )
                ),
            )
        )
    ).scalar_one()
    if active_at_start == 0:
        return None
    churned_in_period = (
        await session.execute(
            select(func.count())
            .select_from(ChurnEvent)
            .where(
                ChurnEvent.org_id == org_id,
                ChurnEvent.churn_date >= period_start,
                ChurnEvent.churn_date <= period_end,
                ChurnEvent.reversed_at.is_(None),
            )
        )
    ).scalar_one()
    return Decimal(churned_in_period) / Decimal(active_at_start)


async def _period_bounds(session: AsyncSession, actor: Actor) -> dict[str, tuple[date, date]]:
    today = ist_today()
    month_start = today.replace(day=1)
    q_start_month = ((today.month - 1) // 3) * 3 + 1
    quarter_start = today.replace(month=q_start_month, day=1)
    return {
        "month": (month_start, today),
        "quarter": (quarter_start, today),
    }


async def _rep_breakdown(
    session: AsyncSession, actor: Actor, where: list[Any]
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(
                Customer.account_owner_id,
                User.name,
                func.count(),
                func.coalesce(func.sum(ChurnEvent.monthly_value_lost_paise), 0),
            )
            .select_from(ChurnEvent)
            .join(Customer, Customer.id == ChurnEvent.customer_id)
            .outerjoin(User, User.id == Customer.account_owner_id)
            .where(*where)
            .group_by(Customer.account_owner_id, User.name)
        )
    ).all()
    return [
        {
            "owner_id": owner_id,
            "owner_name": owner_name,
            "count": count,
            "value_lost_paise": value_lost,
        }
        for owner_id, owner_name, count, value_lost in rows
    ]


async def summary(
    session: AsyncSession,
    actor: Actor,
    *,
    breakdown: Literal["reason", "rep", "month", None] = None,
    top_reasons: int = 5,
) -> dict[str, Any]:
    scope_where, scope = await customer_scope_where(session, actor)
    where = [
        *scope_where,
        ChurnEvent.reversed_at.is_(None),
    ]

    async def _count_and_value(extra: list[Any]) -> tuple[int, int]:
        row = (
            await session.execute(
                select(
                    func.count(), func.coalesce(func.sum(ChurnEvent.monthly_value_lost_paise), 0)
                )
                .select_from(ChurnEvent)
                .join(Customer, Customer.id == ChurnEvent.customer_id)
                .where(*where, *extra)
            )
        ).one()
        return row[0], row[1]

    periods = await _period_bounds(session, actor)
    this_month_count, _ = await _count_and_value(
        [ChurnEvent.churn_date >= periods["month"][0], ChurnEvent.churn_date <= periods["month"][1]]
    )
    this_quarter_count, _ = await _count_and_value(
        [
            ChurnEvent.churn_date >= periods["quarter"][0],
            ChurnEvent.churn_date <= periods["quarter"][1],
        ]
    )
    all_time_count, total_value_lost = await _count_and_value([])

    rate = await churn_rate(session, actor.org_id, periods["month"][0], periods["month"][1])

    top_reason_rows = (
        await session.execute(
            select(ChurnEvent.reason, func.count())
            .select_from(ChurnEvent)
            .join(Customer, Customer.id == ChurnEvent.customer_id)
            .where(*where)
            .group_by(ChurnEvent.reason)
            .order_by(func.count().desc())
            .limit(top_reasons)
        )
    ).all()

    out: dict[str, Any] = {
        "churned_this_month": this_month_count,
        "churned_this_quarter": this_quarter_count,
        "churned_all_time": all_time_count,
        "churn_rate_this_month": rate,
        "total_monthly_value_lost_paise": total_value_lost,
        "top_reasons": [{"reason": r, "count": c} for r, c in top_reason_rows],
    }

    can_see_rep_breakdown = scope in (Scope.TEAM, Scope.ALL)
    if breakdown == "rep":
        out["by_rep"] = await _rep_breakdown(session, actor, where) if can_see_rep_breakdown else []
    elif breakdown == "reason":
        out["by_reason"] = [{"reason": r, "count": c} for r, c in top_reason_rows]
    elif breakdown == "month":
        month_rows = (
            await session.execute(
                select(
                    func.date_trunc("month", ChurnEvent.churn_date),
                    func.count(),
                    func.coalesce(func.sum(ChurnEvent.monthly_value_lost_paise), 0),
                )
                .select_from(ChurnEvent)
                .join(Customer, Customer.id == ChurnEvent.customer_id)
                .where(*where)
                .group_by(func.date_trunc("month", ChurnEvent.churn_date))
                .order_by(func.date_trunc("month", ChurnEvent.churn_date))
            )
        ).all()
        out["by_month"] = [
            {"month": m.date(), "count": c, "value_lost_paise": v} for m, c, v in month_rows
        ]
    elif can_see_rep_breakdown:
        # Not explicitly requested, but TL/Admin/SA still get it by default so the page can
        # render the breakdown panel without a second round trip; Users simply never get it.
        out["by_rep"] = await _rep_breakdown(session, actor, where)

    # teams.compare: an extra cross-team comparison signal, Admin/SA only (TL is denied this key
    # even though they see their own team's rep breakdown above via customer.view's TEAM scope).
    table = await loader.get_table(session)
    rule = table.get((actor.role_enum, TEAMS_COMPARE_KEY))
    out["teams_compare_available"] = bool(rule and not rule.denied)
    return out
