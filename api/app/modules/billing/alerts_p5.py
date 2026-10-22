# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Phase 5 contract / cancellation alerts (P5-04): contract approaching end, potential
cancellation (churn risk), contract ended without renewal. Mirrors billing/alerts.py's pattern
exactly: each polling check uses `_once()` to dedup against `billing_alerts` by
(customer, alert type, ref, date); the cancellation-requested alert is event-driven instead, hooked
on the outbox so it fires immediately rather than waiting for the daily job, and is made idempotent
by `notify()`'s own dedupe_key (at-least-once outbox delivery)."""

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.billing import events
from app.modules.billing.alerts import CHURN_RISK, _admins_and_sa, _once, _owner_and_tl
from app.modules.billing.models import MinutePack, Subscription
from app.modules.billing.packs import _pack_balance
from app.modules.billing.payments import payment_status
from app.modules.billing.schedule import ist_today
from app.modules.customers.models import Customer
from app.platform.notifications import service as notifications
from app.platform.outbox.models import OutboxEvent
from app.platform.outbox.service import on_event

CONTRACT_APPROACHING_END = "billing.contract_approaching_end"
CONTRACT_ENDED_NO_RENEWAL = "billing.contract_ended_no_renewal"
CONTRACT_WINDOW_DAYS = 14


async def check_contract_approaching_end(
    session: AsyncSession, org_id: uuid.UUID, today: Any = None
) -> int:
    """14 days before `contract_end_date`, and on the day itself, for every confirmed
    subscription not already cancelled."""
    today = today or ist_today()
    sent = 0
    warn_date = today + timedelta(days=CONTRACT_WINDOW_DAYS)
    rows = (
        await session.execute(
            select(Subscription, Customer)
            .join(Customer, Customer.id == Subscription.customer_id)
            .where(
                Subscription.org_id == org_id,
                Subscription.status == "confirmed",
                Subscription.deleted_at.is_(None),
                Subscription.cancellation_status != "cancelled",
                Subscription.contract_end_date.in_((today, warn_date)),
            )
        )
    ).all()
    for sub, customer in rows:
        if await _once(session, org_id, customer.id, CONTRACT_APPROACHING_END, sub.id, today):
            recipients = await _owner_and_tl(session, customer)
            await notifications.notify(
                session, org_id, recipients, CONTRACT_APPROACHING_END, "Contract approaching end",
                f"The contract ends on {sub.contract_end_date.isoformat()}.",
                f"/customers/{customer.id}",
                dedupe_key=f"{CONTRACT_APPROACHING_END}:{sub.id}:{today.isoformat()}",
            )  # fmt: skip
            sent += 1
    return sent


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


async def check_payment_overdue_and_minutes_out(
    session: AsyncSession, org_id: uuid.UUID, today: Any = None
) -> int:
    """Churn risk: a customer with an overdue invoice AND no prepaid minutes left. Reuses
    payments.payment_status() and packs' balance logic; dedup is separate from the single-signal
    churn-risk notes billing/alerts.py already sends (different ref: the customer, not a pack or
    invoice)."""
    today = today or ist_today()
    sent = 0
    customers = (
        await session.execute(
            select(Customer).where(Customer.org_id == org_id, Customer.status == "active")
        )
    ).scalars()
    admins = await _admins_and_sa(session, org_id)
    for customer in customers:
        if await payment_status(session, customer.id) != "overdue":
            continue
        if not await _minutes_ran_out(session, customer.id, today):
            continue
        if await _once(session, org_id, customer.id, CHURN_RISK, customer.id, today):
            await notifications.notify(
                session, org_id, admins, CHURN_RISK,
                "Churn risk: payment overdue and minutes ran out",
                "This customer has an overdue invoice and no prepaid minutes remaining.",
                f"/customers/{customer.id}",
                dedupe_key=f"{CHURN_RISK}:overdue_and_out:{customer.id}:{today.isoformat()}",
            )  # fmt: skip
            sent += 1
    return sent


async def check_contract_ended_no_renewal(
    session: AsyncSession, org_id: uuid.UUID, today: Any = None
) -> int:
    """The day after `contract_end_date`, for every confirmed subscription whose renewal_status
    is still 'upcoming' (nothing was recorded)."""
    today = today or ist_today()
    sent = 0
    yesterday = today - timedelta(days=1)
    rows = (
        await session.execute(
            select(Subscription, Customer)
            .join(Customer, Customer.id == Subscription.customer_id)
            .where(
                Subscription.org_id == org_id,
                Subscription.status == "confirmed",
                Subscription.deleted_at.is_(None),
                Subscription.contract_end_date == yesterday,
                Subscription.renewal_status == "upcoming",
            )
        )
    ).all()
    admins = await _admins_and_sa(session, org_id)
    for sub, customer in rows:
        if await _once(session, org_id, customer.id, CONTRACT_ENDED_NO_RENEWAL, sub.id, today):
            recipients = [*await _owner_and_tl(session, customer), *admins]
            await notifications.notify(
                session, org_id, recipients, CONTRACT_ENDED_NO_RENEWAL,
                "Contract ended without renewal",
                f"The contract ended on {sub.contract_end_date.isoformat()} with no renewal "
                "recorded.",
                f"/customers/{customer.id}",
                dedupe_key=f"{CONTRACT_ENDED_NO_RENEWAL}:{sub.id}:{today.isoformat()}",
            )  # fmt: skip
            sent += 1
    return sent


async def run_all_p5(session: AsyncSession, org_id: uuid.UUID, today: Any = None) -> dict[str, Any]:
    today = today or ist_today()
    return {
        "contract_approaching_end": await check_contract_approaching_end(session, org_id, today),
        "churn_risk_overdue_and_out": await check_payment_overdue_and_minutes_out(
            session, org_id, today
        ),
        "contract_ended_no_renewal": await check_contract_ended_no_renewal(session, org_id, today),
    }


@on_event(events.SUBSCRIPTION_CANCELLATION_REQUESTED)
async def churn_risk_on_cancellation_requested(session: AsyncSession, event: OutboxEvent) -> None:
    """Event-driven (not polling): the moment lifecycle.record_cancellation(action="request")
    fires its outbox event, notify Admins and Super admins. Idempotent via notify()'s dedupe_key,
    since the outbox delivers at least once."""
    sub = await session.get(Subscription, uuid.UUID(event.payload["subscription_id"]))
    if sub is None:
        return
    admins = await _admins_and_sa(session, sub.org_id)
    await notifications.notify(
        session, sub.org_id, admins, CHURN_RISK, "Churn risk: cancellation requested",
        "A subscription cancellation has been requested.", f"/customers/{sub.customer_id}",
        dedupe_key=f"{CHURN_RISK}:cancellation_requested:{sub.id}",
    )  # fmt: skip
