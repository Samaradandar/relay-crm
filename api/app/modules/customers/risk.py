# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Churn-risk view (P5-05): customers matching any of cancellation requested, payment overdue,
minutes ran out, or contract ending within 14 days with renewal still upcoming. Read-only; scoped
exactly like `customers.service.list_customers` (User own, Team Leader team, Admin/SA all)."""

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounts.models import Account
from app.modules.billing.models import MinutePack, Subscription
from app.modules.billing.packs import _pack_balance
from app.modules.billing.payments import payment_status
from app.modules.billing.schedule import ist_today
from app.modules.customers.models import Customer
from app.modules.customers.service import VIEW_KEY
from app.modules.users.models import User
from app.platform.context import Actor
from app.platform.permissions.scope import scope_conditions
from app.platform.reads import authorize_read

CONTRACT_WINDOW_DAYS = 14


async def _minutes_ran_out(session: AsyncSession, customer_id: uuid.UUID, today: Any) -> bool:
    packs = (
        (
            await session.execute(
                select(MinutePack).where(
                    MinutePack.customer_id == customer_id,
                    MinutePack.deleted_at.is_(None),
                    (MinutePack.expires_on.is_(None)) | (MinutePack.expires_on >= today),
                )
            )
        )
        .scalars()
        .all()
    )
    if not packs:
        return False
    balance = sum([await _pack_balance(session, p.id) for p in packs], Decimal(0))
    return balance <= 0


async def at_risk_customers(session: AsyncSession, actor: Actor) -> list[dict[str, Any]]:
    decision = await authorize_read(session, actor, VIEW_KEY)
    today = ist_today()
    rows = (
        await session.execute(
            select(Customer, Account.name, User.name)
            .join(Account, Account.id == Customer.account_id)
            .outerjoin(User, User.id == Customer.account_owner_id)
            .where(
                *scope_conditions(
                    actor,
                    Customer,
                    decision.scope,
                    owner_attr="account_owner_id",
                    hide_sa_owned=False,
                ),
                Customer.deleted_at.is_(None),
                Customer.status == "active",
            )
            .order_by(Account.name, Customer.id)
        )
    ).all()
    out: list[dict[str, Any]] = []
    for customer, account_name, owner_name in rows:
        reasons: list[str] = []
        subs = (
            (
                await session.execute(
                    select(Subscription).where(
                        Subscription.customer_id == customer.id,
                        Subscription.deleted_at.is_(None),
                        Subscription.status == "confirmed",
                    )
                )
            )
            .scalars()
            .all()
        )
        if any(s.cancellation_status == "requested" for s in subs):
            reasons.append("cancellation_requested")
        if await payment_status(session, customer.id) == "overdue":
            reasons.append("payment_overdue")
        if await _minutes_ran_out(session, customer.id, today):
            reasons.append("minutes_ran_out")
        if any(
            s.renewal_status == "upcoming"
            and s.contract_end_date is not None
            and today <= s.contract_end_date <= today + timedelta(days=CONTRACT_WINDOW_DAYS)
            for s in subs
        ):
            reasons.append("contract_ending_soon")
        if reasons:
            out.append(
                {
                    "customer_id": customer.id,
                    "account_id": customer.account_id,
                    "account_name": account_name,
                    "account_owner_id": customer.account_owner_id,
                    "account_owner_name": owner_name,
                    "reasons": reasons,
                }
            )
    return out
