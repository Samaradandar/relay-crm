# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Billing alerts (P4-09/P4-10): low balance, run-out, overage, payment due/overdue, awaiting
confirmation. Each fires once per (customer, alert type, pack-or-invoice, date), recorded in
`billing_alerts` before the notification is sent, so a retried job never sends it twice."""

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing.models import (
    BillingAlert,
    Invoice,
    MinutePack,
    OverageItem,
    Subscription,
)
from app.modules.billing.packs import _pack_balance
from app.modules.billing.schedule import ist_today
from app.modules.customers.models import Customer
from app.modules.leads.handlers import _leader
from app.modules.users.models import User
from app.platform.notifications import service as notifications
from app.platform.permissions.roles import Role

LOW_BALANCE = "billing.low_balance"
RUN_OUT = "billing.run_out"
OVERAGE = "billing.overage"
PAYMENT_DUE = "billing.payment_due"
PAYMENT_OVERDUE = "billing.payment_overdue"
AWAITING_CONFIRMATION = "billing.awaiting_confirmation"
CHURN_RISK = "churn.risk"
NO_REF = uuid.UUID(int=0)
LOW_BALANCE_BP = 20  # percent
DUE_WINDOW_DAYS = 14


async def _once(
    session: AsyncSession,
    org_id: uuid.UUID,
    customer_id: uuid.UUID,
    alert_type: str,
    ref_id: uuid.UUID,
    today: date,
) -> bool:
    """True the first time this (customer, type, ref, date) is seen; False on a repeat."""
    exists = (
        await session.execute(
            select(BillingAlert.id).where(
                BillingAlert.org_id == org_id,
                BillingAlert.customer_id == customer_id,
                BillingAlert.alert_type == alert_type,
                BillingAlert.ref_id == ref_id,
                BillingAlert.alert_date == today,
            )
        )
    ).first()
    if exists is not None:
        return False
    session.add(
        BillingAlert(
            org_id=org_id,
            customer_id=customer_id,
            alert_type=alert_type,
            ref_id=ref_id,
            alert_date=today,
        )
    )
    return True


async def _owner_and_tl(session: AsyncSession, customer: Customer) -> list[uuid.UUID]:
    owner = await session.get(User, customer.account_owner_id)
    ids = [customer.account_owner_id]
    if owner is not None and (leader := await _leader(session, owner.team_id)):
        ids.append(leader)
    return ids


async def _admins_and_sa(session: AsyncSession, org_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        (
            await session.execute(
                select(User.id).where(
                    User.org_id == org_id,
                    User.role.in_((Role.ADMIN, Role.SUPER_ADMIN)),
                    User.is_active.is_(True),
                )
            )
        ).scalars()
    )


async def check_balances(
    session: AsyncSession, org_id: uuid.UUID, today: date | None = None
) -> int:
    """Low balance (<=20% of the active pack total) and run-out (balance 0) per customer."""
    today = today or ist_today()
    sent = 0
    customers = (
        await session.execute(
            select(Customer).where(Customer.org_id == org_id, Customer.status == "active")
        )
    ).scalars()
    for customer in customers:
        packs = (
            (
                await session.execute(
                    select(MinutePack).where(
                        MinutePack.customer_id == customer.id,
                        MinutePack.deleted_at.is_(None),
                        (MinutePack.expires_on.is_(None)) | (MinutePack.expires_on >= today),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not packs:
            continue
        total_size = sum((p.size_minutes for p in packs), type(packs[0].size_minutes)(0))
        balance = sum(
            [await _pack_balance(session, p.id) for p in packs], type(packs[0].size_minutes)(0)
        )
        if total_size <= 0:
            continue
        pct = balance * 100 / total_size
        ref = packs[0].id
        if balance <= 0:
            if await _once(session, org_id, customer.id, RUN_OUT, ref, today):
                recipients = await _owner_and_tl(session, customer)
                await notifications.notify(
                    session, org_id, recipients, RUN_OUT, "Minutes ran out",
                    "The prepaid minute balance has reached zero.", f"/customers/{customer.id}",
                    dedupe_key=f"{RUN_OUT}:{customer.id}:{today.isoformat()}",
                )  # fmt: skip
                await notifications.notify(
                    session, org_id, await _admins_and_sa(session, org_id), CHURN_RISK,
                    "Churn risk: minutes ran out",
                    "A customer's prepaid minutes have run out.", f"/customers/{customer.id}",
                    dedupe_key=f"{CHURN_RISK}:{RUN_OUT}:{customer.id}:{today.isoformat()}",
                )  # fmt: skip
                sent += 1
        elif pct <= LOW_BALANCE_BP:
            if await _once(session, org_id, customer.id, LOW_BALANCE, ref, today):
                recipients = await _owner_and_tl(session, customer)
                await notifications.notify(
                    session, org_id, recipients, LOW_BALANCE, "Minute balance is low",
                    f"The prepaid balance has dropped to {pct:.0f}% of the pack.",
                    f"/customers/{customer.id}",
                    dedupe_key=f"{LOW_BALANCE}:{customer.id}:{today.isoformat()}",
                )  # fmt: skip
                sent += 1
    return sent


async def check_overage(session: AsyncSession, org_id: uuid.UUID, today: date | None = None) -> int:
    """A new unbilled overage item alerts Admins and Super admins once."""
    today = today or ist_today()
    sent = 0
    items = (
        await session.execute(
            select(OverageItem).where(
                OverageItem.org_id == org_id, OverageItem.status == "unbilled"
            )
        )
    ).scalars()
    admins = await _admins_and_sa(session, org_id)
    for item in items:
        if await _once(session, org_id, item.customer_id, OVERAGE, item.id, today):
            await notifications.notify(
                session, org_id, admins, OVERAGE, "Unbilled overage recorded",
                f"{item.minutes} minutes of overage ({item.reason}) need billing or a top-up.",
                f"/customers/{item.customer_id}",
                dedupe_key=f"{OVERAGE}:{item.id}:{today.isoformat()}",
            )  # fmt: skip
            sent += 1
    return sent


async def check_payment_due_and_overdue(
    session: AsyncSession, org_id: uuid.UUID, today: date | None = None
) -> int:
    """14 days before the due date, on the due date, and the day after if still unpaid."""
    today = today or ist_today()
    sent = 0
    rows = (
        await session.execute(
            select(Invoice, Customer)
            .join(Customer, Customer.id == Invoice.customer_id)
            .where(
                Invoice.org_id == org_id,
                Invoice.status.in_(("open", "part_paid")),
                Invoice.deleted_at.is_(None),
            )
        )
    ).all()
    for invoice, customer in rows:
        recipients = await _owner_and_tl(session, customer)
        if invoice.due_date == today or invoice.due_date - timedelta(days=DUE_WINDOW_DAYS) == today:
            if await _once(session, org_id, customer.id, PAYMENT_DUE, invoice.id, today):
                await notifications.notify(
                    session, org_id, recipients, PAYMENT_DUE, "Payment due",
                    f"Invoice {invoice.number} is due on {invoice.due_date.isoformat()}.",
                    f"/customers/{customer.id}",
                    dedupe_key=f"{PAYMENT_DUE}:{invoice.id}:{today.isoformat()}",
                )  # fmt: skip
                sent += 1
        elif invoice.due_date < today:
            if await _once(session, org_id, customer.id, PAYMENT_OVERDUE, invoice.id, today):
                await notifications.notify(
                    session, org_id, recipients, PAYMENT_OVERDUE, "Payment overdue",
                    f"Invoice {invoice.number} was due on {invoice.due_date.isoformat()}.",
                    f"/customers/{customer.id}",
                    dedupe_key=f"{PAYMENT_OVERDUE}:{invoice.id}:{today.isoformat()}",
                )  # fmt: skip
                await notifications.notify(
                    session, org_id, await _admins_and_sa(session, org_id), CHURN_RISK,
                    "Churn risk: payment overdue",
                    f"Invoice {invoice.number} is overdue.", f"/customers/{customer.id}",
                    dedupe_key=f"{CHURN_RISK}:{PAYMENT_OVERDUE}:{invoice.id}:{today.isoformat()}",
                )  # fmt: skip
                sent += 1
    return sent


async def check_awaiting_confirmation(
    session: AsyncSession, org_id: uuid.UUID, today: date | None = None
) -> int:
    """Daily until confirmed: the account owner's Team Leader, and Admins."""
    today = today or ist_today()
    sent = 0
    rows = (
        await session.execute(
            select(Subscription, Customer)
            .join(Customer, Customer.id == Subscription.customer_id)
            .where(
                Subscription.org_id == org_id,
                Subscription.status == "draft",
                Subscription.deleted_at.is_(None),
            )
        )
    ).all()
    for sub, customer in rows:
        if await _once(session, org_id, customer.id, AWAITING_CONFIRMATION, sub.id, today):
            owner = await session.get(User, customer.account_owner_id)
            leader = await _leader(session, owner.team_id) if owner else None
            admins = await _admins_and_sa(session, org_id)
            recipients = [i for i in [leader, *admins] if i is not None]
            await notifications.notify(
                session, org_id, recipients, AWAITING_CONFIRMATION,
                "A subscription is awaiting confirmation",
                "A draft subscription has not yet been confirmed.", f"/customers/{customer.id}",
                dedupe_key=f"{AWAITING_CONFIRMATION}:{sub.id}:{today.isoformat()}",
            )  # fmt: skip
            sent += 1
    return sent


async def run_all(
    session: AsyncSession, org_id: uuid.UUID, today: date | None = None
) -> dict[str, Any]:
    today = today or ist_today()
    return {
        "balances": await check_balances(session, org_id, today),
        "overage": await check_overage(session, org_id, today),
        "payments": await check_payment_due_and_overdue(session, org_id, today),
        "awaiting_confirmation": await check_awaiting_confirmation(session, org_id, today),
    }
