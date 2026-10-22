# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Subscription changes raised from the existing_customer pipeline (Phase 5, P5-02).

Entering the Updated stage (deal.won, pipeline=existing_customer) raises a *pending*
SubscriptionChange from the deal's `closure_change` -- it never mutates the subscription itself.
A Team Leader, Admin or Super admin then calls POST /subscription-changes/{id}/confirm (same
permission key as subscriptions.py's draft confirmation, `contract_dates.set_and_subscription
.confirm`) to actually apply the billing effect; nothing bills before that. A reject just closes
the request with no mutation.

Mirrors billing/draft.py's draft_on_won hook (registered the same way in platform/registry.py)
and billing/subscriptions.py's `_write` snapshot-and-audit pattern, but is a new function rather
than a reuse of `update_contract`: the mutation shape (new version + schedule regen from an
effective date, or a pack, or a renewal) is different enough per change_type that folding it
into the generic contract-edit path would make both harder to read.
"""

import uuid
from datetime import date
from decimal import Decimal
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
    Renewal,
    Subscription,
    SubscriptionChange,
    SubscriptionVersion,
)
from app.modules.billing.schedule import add_months, ist_today, periods
from app.modules.catalog.models import Product
from app.modules.customers.models import Customer
from app.modules.leads.models import Deal
from app.modules.pipeline.engine import events as pipeline_events
from app.platform.context import Actor, RequestInfo
from app.platform.outbox.models import OutboxEvent
from app.platform.outbox.service import on_event
from app.platform.permissions.scope import scope_conditions
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork

CONFIRM_KEY = "contract_dates.set_and_subscription.confirm"
VIEW_KEY = "customer.view"
CHANGE_TYPES = (
    "top_up",
    "plan_change",
    "model_switch",
    "other_product",
    "renewal",
    "increase",
    "decrease",
)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


async def _subscription_for_deal(session: AsyncSession, deal: Deal) -> Subscription | None:
    """The existing_customer deal's own subscription: via its account's customer."""
    customer_id = (
        await session.execute(select(Customer.id).where(Customer.account_id == deal.account_id))
    ).scalar_one_or_none()
    if customer_id is None:
        return None
    return (
        (
            await session.execute(
                select(Subscription)
                .where(Subscription.customer_id == customer_id, Subscription.status != "draft")
                .order_by(Subscription.created_at.desc())
            )
        )
        .scalars()
        .first()
    )


@on_event(pipeline_events.DEAL_WON)
async def raise_change_on_won(session: AsyncSession, event: OutboxEvent) -> None:
    """Entering Updated (existing_customer pipeline): raise one pending SubscriptionChange from
    the deal's closure_change. Does NOT mutate the subscription -- see module docstring."""
    deal = await session.get(Deal, uuid.UUID(event.payload["deal_id"]))
    if deal is None or deal.pipeline != "existing_customer":
        return
    change = deal.closure_change or {}
    change_type = change.get("change_type")
    if change_type not in CHANGE_TYPES:
        return
    sub = await _subscription_for_deal(session, deal)
    if sub is None:
        return
    existing = (
        await session.execute(
            select(SubscriptionChange.id).where(SubscriptionChange.source_deal_id == deal.id)
        )
    ).first()
    if existing is not None:  # at-least-once delivery: never raise a second change for this deal
        return
    session.add(
        SubscriptionChange(
            org_id=deal.org_id,
            subscription_id=sub.id,
            source_deal_id=deal.id,
            change_type=change_type,
            payload=change.get("new_plan_values") or {},
            effective_date=_parse_date(change.get("effective_date")),
        )
    )


def _out(c: SubscriptionChange) -> dict[str, Any]:
    return {
        "id": c.id,
        "subscription_id": c.subscription_id,
        "source_deal_id": c.source_deal_id,
        "change_type": c.change_type,
        "payload": c.payload,
        "effective_date": c.effective_date,
        "status": c.status,
        "confirmed_by": c.confirmed_by,
        "confirmed_at": c.confirmed_at,
        "created_at": c.created_at,
    }


async def _scoped_sub(session: AsyncSession, actor: Actor, sub: Subscription) -> None:
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


async def list_changes(
    session: AsyncSession, actor: Actor, subscription_id: uuid.UUID
) -> list[dict[str, Any]]:
    sub = await session.get(Subscription, subscription_id)
    if sub is None:
        raise NotFound("Subscription not found")
    await _scoped_sub(session, actor, sub)
    rows = (
        await session.execute(
            select(SubscriptionChange)
            .where(SubscriptionChange.subscription_id == subscription_id)
            .order_by(SubscriptionChange.created_at.desc())
        )
    ).scalars()
    return [_out(c) for c in rows]


async def _regen_schedule(
    session: AsyncSession, sub: Subscription, effective: date, fee_paise: int
) -> None:
    """Re-price not-yet-invoiced schedule rows dated on/after `effective` to the new fee, and
    add fresh rows for any gap out to the contract end. Rows before `effective`, and any row
    already linked to an invoice, are never touched -- and never deleted: `billing_schedule` is
    append-only by grant (relay_app has no DELETE on it, same as subscription_versions), so this
    re-prices in place instead of dropping and re-inserting."""
    stale = (
        (
            await session.execute(
                select(BillingSchedule).where(
                    BillingSchedule.subscription_id == sub.id,
                    BillingSchedule.period_start >= effective,
                    BillingSchedule.invoice_id.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    covered = set()
    for row in stale:
        row.expected_amount_paise = fee_paise
        covered.add(row.period_start)
    end = sub.contract_end_date or effective
    for p in periods(effective, sub.billing_frequency or "monthly", "monthly", end=end):
        if p.start in covered:
            continue
        session.add(
            BillingSchedule(
                org_id=sub.org_id,
                subscription_id=sub.id,
                period_start=p.start,
                period_end=p.end,
                due_date=p.due,
                expected_amount_paise=fee_paise,
            )
        )


async def _apply_top_up(
    session: AsyncSession, sub: Subscription, change: SubscriptionChange
) -> None:
    p = change.payload
    session.add(
        MinutePack(
            org_id=sub.org_id,
            customer_id=sub.customer_id,
            subscription_id=sub.id,
            size_minutes=Decimal(str(p["size_minutes"])),
            price_paise=int(p["price_paise"]),
            purchased_on=change.effective_date or ist_today(),
            source_deal_id=change.source_deal_id,
            reason="Phase 5 top-up, confirmed",
        )
    )


async def _apply_plan_change(
    uow: UnitOfWork, sub: Subscription, change: SubscriptionChange, *, model_switch: bool
) -> None:
    p = change.payload
    effective = change.effective_date or ist_today()
    if p.get("pricing_model"):
        sub.pricing_model = p["pricing_model"]
    if p.get("fee_paise") is not None:
        sub.fee_paise = int(p["fee_paise"])
    if p.get("pack_price_paise") is not None:
        sub.pack_price_paise = int(p["pack_price_paise"])
    if p.get("pack_size_minutes") is not None:
        sub.pack_size_minutes = Decimal(str(p["pack_size_minutes"]))
    sub.version += 1
    await uow.session.flush()
    uow.session.add(
        SubscriptionVersion(
            org_id=sub.org_id,
            subscription_id=sub.id,
            version_no=sub.version,
            snapshot=snapshot(sub),
            reason=f"{'Model switch' if model_switch else 'Plan change'} confirmed from deal "
            f"{change.source_deal_id}, effective {effective.isoformat()}",
            changed_by=uow.actor.id,
        )
    )
    if sub.pricing_model == "saas" and sub.fee_paise is not None:
        await _regen_schedule(uow.session, sub, effective, sub.fee_paise)
    # model_switch, per_minute side: the remaining balance of any existing pack stays usable
    # until it is drawn down, never force-expired at `effective` -- the more customer-friendly
    # of the two options raised in the PRD; no answer came back on which to pick (noted in the
    # PR report as an assumption).


async def _apply_other_product(
    session: AsyncSession, sub: Subscription, change: SubscriptionChange
) -> None:
    p = change.payload
    added: dict[str, Any] = {"product_id": str(p.get("product_id")), "quantity": p.get("quantity")}
    if p.get("product_id"):
        product = await session.get(Product, uuid.UUID(str(p["product_id"])))
        if product is not None:
            added["name"] = product.name
            added["unit_price_paise"] = product.list_price_paise
    sub.version += 1
    await session.flush()
    snap = snapshot(sub)
    snap["added_products"] = [added]
    session.add(
        SubscriptionVersion(
            org_id=sub.org_id,
            subscription_id=sub.id,
            version_no=sub.version,
            snapshot=snap,
            reason=f"Other product added, confirmed from deal {change.source_deal_id}",
        )
    )


async def _apply_renewal(uow: UnitOfWork, sub: Subscription, change: SubscriptionChange) -> None:
    term = change.payload.get("contract_term") or sub.contract_term or "yearly"
    months = {"monthly": 1, "quarterly": 3, "yearly": 12}.get(term, 12)
    start = sub.contract_end_date or sub.renewal_date or ist_today()
    new_end = add_months(start, months)
    sub.contract_end_date = new_end
    sub.renewal_date = new_end
    sub.renewal_status = "renewed"
    sub.version += 1
    await uow.session.flush()
    uow.session.add(
        SubscriptionVersion(
            org_id=sub.org_id,
            subscription_id=sub.id,
            version_no=sub.version,
            snapshot=snapshot(sub),
            reason=f"Renewed from deal {change.source_deal_id}",
            changed_by=uow.actor.id,
        )
    )
    if sub.pricing_model == "saas" and sub.fee_paise is not None and sub.billing_frequency:
        for p in periods(start, sub.billing_frequency, term):
            uow.session.add(
                BillingSchedule(
                    org_id=sub.org_id,
                    subscription_id=sub.id,
                    period_start=p.start,
                    period_end=p.end,
                    due_date=p.due,
                    expected_amount_paise=sub.fee_paise,
                )
            )
    uow.session.add(
        Renewal(
            org_id=sub.org_id,
            subscription_id=sub.id,
            outcome="renewed",
            deal_id=change.source_deal_id,
            recorded_by=uow.actor.id,
        )
    )


async def confirm_change(
    actor: Actor, request: RequestInfo, change_id: uuid.UUID, reason: str | None
) -> dict[str, Any]:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        change = await s.get(SubscriptionChange, change_id)
        if change is None:
            raise NotFound("Subscription change not found")
        if change.status != "pending":
            raise ValidationFailed("This change is not awaiting confirmation")
        sub = await s.get(Subscription, change.subscription_id)
        if sub is None:
            raise NotFound("Subscription not found")
        await uow.authorize(CONFIRM_KEY, entity=("customer", sub.customer_id))
        cleaned = uow.reason(CONFIRM_KEY, reason)
        if change.change_type == "top_up":
            await _apply_top_up(s, sub, change)
        elif change.change_type in ("plan_change", "increase", "decrease"):
            await _apply_plan_change(uow, sub, change, model_switch=False)
        elif change.change_type == "model_switch":
            await _apply_plan_change(uow, sub, change, model_switch=True)
        elif change.change_type == "other_product":
            await _apply_other_product(s, sub, change)
        elif change.change_type == "renewal":
            await _apply_renewal(uow, sub, change)
        else:
            raise ValidationFailed(f"Unknown change type {change.change_type!r}")
        now = utc_now()
        change.status = "confirmed"
        change.confirmed_by, change.confirmed_at = actor.id, now
        sub.updated_at = now
        # P5-06: any confirmed change on a churned customer's subscription un-churns them --
        # generic, not limited to the "return" request type, since a returning customer may file
        # any existing_customer request type once they're back.
        customer = await s.get(Customer, sub.customer_id)
        if customer is not None and customer.status == "churned":
            customer.status = "active"
            customer.updated_at = now
        await s.flush()
        await uow.audit(
            CONFIRM_KEY,
            "subscription_change",
            change.id,
            new={"status": change.status, "change_type": change.change_type},
            reason=cleaned,
        )
        await uow.emit(
            events.SUBSCRIPTION_CHANGE_CONFIRMED,
            {"subscription_change_id": str(change.id), "subscription_id": str(sub.id)},
        )
        out = _out(change)
    return out


async def reject_change(
    actor: Actor, request: RequestInfo, change_id: uuid.UUID, reason: str | None
) -> dict[str, Any]:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        change = await s.get(SubscriptionChange, change_id)
        if change is None:
            raise NotFound("Subscription change not found")
        if change.status != "pending":
            raise ValidationFailed("This change is not awaiting confirmation")
        sub = await s.get(Subscription, change.subscription_id)
        if sub is None:
            raise NotFound("Subscription not found")
        await uow.authorize(CONFIRM_KEY, entity=("customer", sub.customer_id))
        cleaned = uow.reason(CONFIRM_KEY, reason)
        change.status = "rejected"
        change.confirmed_by = actor.id
        await s.flush()
        await uow.audit(CONFIRM_KEY, "subscription_change", change.id, reason=cleaned)
        await uow.emit(
            events.SUBSCRIPTION_CHANGE_REJECTED,
            {"subscription_change_id": str(change.id), "subscription_id": str(sub.id)},
        )
        out = _out(change)
    return out
