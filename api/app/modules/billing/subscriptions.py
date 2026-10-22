# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Subscription read, confirm and contract-date edits (P4-02).

Confirming a draft, or editing a confirmed subscription's terms, both run through `_write`:
every change is checked for completeness, copied into `subscription_versions`, audited and
turned into an outbox event. Only a confirmed subscription has a billing schedule; a draft bills
nothing.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, ValidationFailed
from app.core.time import utc_now
from app.modules.billing import events
from app.modules.billing.draft import snapshot
from app.modules.billing.models import (
    BillingSchedule,
    MinutePack,
    Subscription,
    SubscriptionVersion,
)
from app.modules.billing.schedule import ist_today, periods
from app.modules.customers.models import Customer
from app.platform.context import Actor, RequestInfo
from app.platform.permissions.scope import scope_conditions
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork

CONFIRM_KEY = "contract_dates.set_and_subscription.confirm"
VIEW_KEY = "customer.view"
REQUIRED_TO_CONFIRM = (
    "pricing_model",
    "billing_frequency",
    "term_type",
    "contract_term",
    "start_date",
)


async def _scoped_sub(session: AsyncSession, actor: Actor, sub: Subscription) -> None:
    """Raise NotFound if `sub`'s customer is outside the actor's customer.view scope."""
    decision = await authorize_read(session, actor, VIEW_KEY)
    hit = (
        await session.execute(
            select(Customer.id).where(
                Customer.id == sub.customer_id,
                *scope_conditions(
                    actor,
                    Customer,
                    decision.scope,
                    owner_attr="account_owner_id",
                    hide_sa_owned=False,
                ),  # fmt: skip
            )
        )
    ).first()
    if hit is None:
        raise NotFound("Subscription not found")


def _out(sub: Subscription) -> dict[str, Any]:
    return {
        "id": sub.id,
        "customer_id": sub.customer_id,
        "source_deal_id": sub.source_deal_id,
        "status": sub.status,
        "pricing_model": sub.pricing_model,
        "pack_price_paise": sub.pack_price_paise,
        "pack_size_minutes": sub.pack_size_minutes,
        "fee_paise": sub.fee_paise,
        "billing_frequency": sub.billing_frequency,
        "term_type": sub.term_type,
        "contract_term": sub.contract_term,
        "start_date": sub.start_date,
        "renewal_date": sub.renewal_date,
        "contract_end_date": sub.contract_end_date,
        "paid_through_date": sub.paid_through_date,
        "billing_contact_id": sub.billing_contact_id,
        "one_time_fees": sub.one_time_fees,
        "confirmed_by": sub.confirmed_by,
        "confirmed_at": sub.confirmed_at,
        "version": sub.version,
        "renewal_status": sub.renewal_status,
        "cancellation_status": sub.cancellation_status,
        "cancellation_requested_at": sub.cancellation_requested_at,
        "ended_at": sub.ended_at,
        "end_reason": sub.end_reason,
    }


async def get_subscription(
    session: AsyncSession, actor: Actor, customer_id: uuid.UUID
) -> dict[str, Any]:
    await authorize_read(session, actor, VIEW_KEY)
    sub = (
        (
            await session.execute(
                select(Subscription)
                .where(Subscription.customer_id == customer_id, Subscription.deleted_at.is_(None))
                .order_by(Subscription.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if sub is None:
        raise NotFound("No subscription for this customer")
    await _scoped_sub(session, actor, sub)
    return _out(sub)


async def pending_confirmations(session: AsyncSession, actor: Actor) -> list[dict[str, Any]]:
    decision = await authorize_read(session, actor, VIEW_KEY)
    rows = (
        await session.execute(
            select(Subscription)
            .join(Customer, Customer.id == Subscription.customer_id)
            .where(
                Subscription.status == "draft",
                Subscription.deleted_at.is_(None),
                *scope_conditions(
                    actor,
                    Customer,
                    decision.scope,
                    owner_attr="account_owner_id",
                    hide_sa_owned=False,
                ),  # fmt: skip
            )
            .order_by(Subscription.created_at)
        )
    ).scalars()
    return [_out(s) for s in rows]


def _apply_fields(sub: Subscription, body: dict[str, Any]) -> None:
    for field, value in body.items():
        if field != "reason" and value is not None:
            setattr(sub, field, value)


def _check_shape(sub: Subscription) -> None:
    for field in REQUIRED_TO_CONFIRM:
        if getattr(sub, field) is None:
            raise ValidationFailed(f"'{field}' is required to confirm the subscription")
    if sub.pricing_model == "per_minute":
        if sub.pack_price_paise is None or sub.pack_size_minutes is None:
            raise ValidationFailed("a per-minute subscription needs a pack price and size")
    elif sub.fee_paise is None:
        raise ValidationFailed("a SaaS subscription needs a fee")


async def _write(
    uow: UnitOfWork, sub: Subscription, body: dict[str, Any], *, confirming: bool
) -> Subscription:
    s = uow.session
    await uow.authorize(CONFIRM_KEY, entity=("customer", sub.customer_id))
    cleaned = uow.reason(CONFIRM_KEY, body.get("reason"))
    old = snapshot(sub)
    _apply_fields(sub, {k: v for k, v in body.items() if k != "reason"})
    if confirming:
        _check_shape(sub)
        sub.status = "confirmed"
        sub.confirmed_by, sub.confirmed_at = uow.actor.id, utc_now()
    sub.version += 1
    sub.updated_at = utc_now()
    await s.flush()
    new = snapshot(sub)
    s.add(
        SubscriptionVersion(
            org_id=sub.org_id,
            subscription_id=sub.id,
            version_no=sub.version,
            snapshot=new,
            reason=cleaned,
            changed_by=uow.actor.id,
        )
    )
    await uow.audit(CONFIRM_KEY, "subscription", sub.id, old=old, new=new, reason=cleaned)
    if confirming:
        await _generate_schedule(s, sub)
        await _create_first_pack(s, sub)
        await uow.emit(
            events.SUBSCRIPTION_CONFIRMED,
            {"subscription_id": str(sub.id), "customer_id": str(sub.customer_id)},
        )
    else:
        await uow.emit(
            events.SUBSCRIPTION_CHANGED,
            {"subscription_id": str(sub.id), "customer_id": str(sub.customer_id)},
        )
    return sub


async def _generate_schedule(session: AsyncSession, sub: Subscription) -> None:
    """One billing_schedule row per cycle for the whole contract term, billed in advance.
    SaaS only: a per-minute subscription has no schedule, only packs."""
    if sub.pricing_model != "saas" or sub.start_date is None:
        return
    existing = (
        await session.execute(
            select(BillingSchedule.id).where(BillingSchedule.subscription_id == sub.id).limit(1)
        )
    ).first()
    if existing is not None:
        return
    assert sub.billing_frequency and sub.contract_term and sub.fee_paise is not None
    for p in periods(sub.start_date, sub.billing_frequency, sub.contract_term):
        session.add(
            BillingSchedule(
                org_id=sub.org_id,
                subscription_id=sub.id,
                period_start=p.start,
                period_end=p.end,
                due_date=p.due,
                expected_amount_paise=sub.fee_paise,
            )
        )
    if sub.contract_end_date is None:
        last = periods(sub.start_date, sub.billing_frequency, sub.contract_term)[-1]
        sub.contract_end_date = last.end
    if sub.renewal_date is None:
        sub.renewal_date = sub.contract_end_date


async def _create_first_pack(session: AsyncSession, sub: Subscription) -> None:
    """On confirmation, a per-minute subscription gets its first minute pack from the deal's
    pack price and size (PRD P4-02); later top-ups are Phase 5 deals or a manual Admin add."""
    if sub.pricing_model != "per_minute":
        return
    if sub.pack_price_paise is None or sub.pack_size_minutes is None:
        return
    existing = (
        await session.execute(
            select(MinutePack.id).where(MinutePack.subscription_id == sub.id).limit(1)
        )
    ).first()
    if existing is not None:
        return
    session.add(
        MinutePack(
            org_id=sub.org_id,
            customer_id=sub.customer_id,
            subscription_id=sub.id,
            size_minutes=sub.pack_size_minutes,
            price_paise=sub.pack_price_paise,
            purchased_on=sub.start_date or ist_today(),
            source_deal_id=sub.source_deal_id,
        )
    )


async def confirm_by_id(
    actor: Actor, request: RequestInfo, subscription_id: uuid.UUID, body: dict[str, Any]
) -> dict[str, Any]:
    async with UnitOfWork(actor, request) as uow:
        sub = await uow.session.get(Subscription, subscription_id)
        if sub is None or sub.deleted_at is not None:
            raise NotFound("Subscription not found")
        if sub.status != "draft":
            raise ValidationFailed("This subscription is not awaiting confirmation")
        sub = await _write(uow, sub, body, confirming=True)
        out = _out(sub)
    return out


async def update_contract(
    actor: Actor, request: RequestInfo, customer_id: uuid.UUID, body: dict[str, Any]
) -> dict[str, Any]:
    """Change a confirmed subscription's terms (contract dates, fee, etc)."""
    async with UnitOfWork(actor, request) as uow:
        sub = (
            (
                await uow.session.execute(
                    select(Subscription).where(
                        Subscription.customer_id == customer_id, Subscription.status != "draft"
                    )
                )
            )
            .scalars()
            .first()
        )
        if sub is None:
            raise NotFound("No confirmed subscription for this customer")
        sub = await _write(uow, sub, body, confirming=False)
        out = _out(sub)
    return out


async def get_schedule(
    session: AsyncSession, actor: Actor, customer_id: uuid.UUID
) -> list[dict[str, Any]]:
    await authorize_read(session, actor, VIEW_KEY)
    sub = (
        (
            await session.execute(
                select(Subscription.id).where(Subscription.customer_id == customer_id)
            )
        )
        .scalars()
        .first()
    )
    if sub is None:
        raise NotFound("No subscription for this customer")
    rows = (
        await session.execute(
            select(BillingSchedule)
            .where(BillingSchedule.subscription_id == sub)
            .order_by(BillingSchedule.period_start)
        )
    ).scalars()
    return [
        {
            "id": r.id,
            "period_start": r.period_start,
            "period_end": r.period_end,
            "due_date": r.due_date,
            "expected_amount_paise": r.expected_amount_paise,
            "invoice_id": r.invoice_id,
        }
        for r in rows
    ]
