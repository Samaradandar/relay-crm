# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Draft a subscription when a deal is Won (P4-02).

The Closure checklist stores the onboarding answers as free text on the deal ("12 months",
"Monthly"), so this module reads them leniently and leaves anything it cannot read empty: the
Team Leader or Admin completes it on confirmation. A draft never bills: it has no schedule, no
pack and no reminders until it is confirmed.

The outbox delivers at least once, and a deal can be re-won: the draft is unique per source deal
(database unique index), so a repeat creates nothing.
"""

import re
import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.accounts.models import Contact
from app.modules.billing import events
from app.modules.billing.models import Subscription, SubscriptionVersion
from app.modules.billing.schedule import MONTHS
from app.modules.catalog.models import DealProduct, Product
from app.modules.customers.service import ensure_customer
from app.modules.leads.models import Deal
from app.modules.pipeline.engine import events as pipeline_events
from app.platform.audit import service as audit_service
from app.platform.context import RequestInfo, system_actor
from app.platform.outbox import service as outbox
from app.platform.outbox.models import OutboxEvent
from app.platform.outbox.service import on_event

DRAFT_KEY = "subscription.draft"
_NUMBER = re.compile(r"(\d+)\s*(month|mo|year|yr)", re.I)


def parse_pricing_model(text: str | None) -> str | None:
    t = (text or "").lower()
    if "saas" in t or "subscription" in t or "own cloud" in t or "customer infra" in t:
        return "saas"
    if "minute" in t or "pack" in t or "managed" in t or "prepaid" in t:
        return "per_minute"
    return None


def parse_period(text: str | None) -> str | None:
    """'Monthly', '1 month', '3 months', 'quarterly', '12 months', 'Annual', '1 year' -> an enum
    value, or None when it is not one of monthly / quarterly / yearly."""
    t = (text or "").strip().lower()
    if not t:
        return None
    if t in MONTHS:
        return t
    if "quarter" in t:
        return "quarterly"
    if "annual" in t or "yearly" in t:
        return "yearly"
    if "month" in t and not _NUMBER.search(t):
        return "monthly"
    m = _NUMBER.search(t)
    if m:
        months = int(m.group(1)) * (12 if m.group(2).lower() in ("year", "yr") else 1)
        return {1: "monthly", 3: "quarterly", 12: "yearly"}.get(months)
    return None


def parse_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def snapshot(sub: Subscription) -> dict[str, Any]:
    """A full copy of the subscription's terms, as stored in subscription_versions."""

    def d(v: Any) -> Any:
        return v.isoformat() if isinstance(v, date) else (str(v) if v is not None else None)

    return {
        "status": sub.status,
        "pricing_model": sub.pricing_model,
        "pack_price_paise": sub.pack_price_paise,
        "pack_size_minutes": d(sub.pack_size_minutes),
        "fee_paise": sub.fee_paise,
        "billing_frequency": sub.billing_frequency,
        "term_type": sub.term_type,
        "contract_term": sub.contract_term,
        "start_date": d(sub.start_date),
        "renewal_date": d(sub.renewal_date),
        "contract_end_date": d(sub.contract_end_date),
        "paid_through_date": d(sub.paid_through_date),
        "billing_contact_id": d(sub.billing_contact_id),
        "one_time_fees": sub.one_time_fees,
        "version": sub.version,
    }


async def _billing_contact(session: AsyncSession, deal: Deal, text: str | None) -> uuid.UUID | None:
    """The account contact whose email the deal's 'billing contact' answer names, if any."""
    needle = (text or "").strip().lower()
    if not needle:
        return None
    rows = await session.execute(
        select(Contact.id, Contact.email, Contact.name).where(
            Contact.account_id == deal.account_id, Contact.deleted_at.is_(None)
        )
    )
    for cid, email, name in rows.all():
        if needle in ((email or "").lower(), (name or "").lower()):
            return cid
    return None


async def _price_from_lines(session: AsyncSession, deal: Deal) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(DealProduct, Product)
            .join(Product, Product.id == DealProduct.product_id)
            .where(DealProduct.deal_id == deal.id, DealProduct.deleted_at.is_(None))
        )
    ).all()
    per_minute = [(dp, p) for dp, p in rows if p.unit == "per_minute"]
    fixed = [(dp, p) for dp, p in rows if p.unit == "fixed"]
    return {
        "has_lines": bool(rows),
        "pack_minutes": sum(dp.quantity for dp, _ in per_minute) or None,
        "pack_price": sum(dp.line_total_paise for dp, _ in per_minute) if per_minute else None,
        "fee": sum(dp.line_total_paise for dp, _ in fixed) if fixed else None,
        "model_hint": "per_minute" if per_minute else ("saas" if fixed else None),
    }


async def draft_for_deal(session: AsyncSession, deal: Deal, owner_hint: uuid.UUID | None) -> bool:
    """Create the customer (if needed) and the draft subscription. True if a draft was made."""
    customer, _ = await ensure_customer(session, deal, owner_hint)
    data = deal.data or {}
    lines = await _price_from_lines(session, deal)
    model = parse_pricing_model(deal.pricing_model) or lines["model_hint"] or "per_minute"
    contract_term = parse_period(data.get("contract_term"))
    frequency = parse_period(data.get("billing_frequency"))
    plan_text = (deal.plan or "").lower()
    term_type = "contract" if ("contract" in plan_text or contract_term == "yearly") else "plan"
    fee = lines["fee"] if model == "saas" else None
    if model == "saas" and fee is None and data.get("final_price_paise"):
        fee = int(data["final_price_paise"])
    values: dict[str, Any] = {
        "org_id": deal.org_id,
        "customer_id": customer.id,
        "source_deal_id": deal.id,
        "status": "draft",
        "pricing_model": model,
        "pack_price_paise": lines["pack_price"] if model == "per_minute" else None,
        "pack_size_minutes": lines["pack_minutes"] if model == "per_minute" else None,
        "fee_paise": fee,
        "billing_frequency": frequency,
        "term_type": term_type,
        "contract_term": contract_term,
        "start_date": parse_date(data.get("subscription_start_date")),
        "renewal_date": parse_date(data.get("renewal_date")),
        "contract_end_date": parse_date(data.get("renewal_date")),
        "billing_contact_id": await _billing_contact(session, deal, data.get("billing_contact")),
        "one_time_fees": [],
        "version": 1,
    }
    sub_id = (
        await session.execute(
            pg_insert(Subscription)
            .values(**values)
            .on_conflict_do_nothing(
                index_elements=[Subscription.org_id, Subscription.source_deal_id]
            )
            .returning(Subscription.id)
        )
    ).scalar_one_or_none()
    if sub_id is None:  # this deal already has its subscription
        return False
    sub = await session.get(Subscription, sub_id, populate_existing=True)
    assert sub is not None
    snap = snapshot(sub)
    session.add(
        SubscriptionVersion(
            org_id=deal.org_id,
            subscription_id=sub.id,
            version_no=1,
            snapshot=snap,
            reason="Drafted from the won deal",
        )
    )
    await audit_service.append(
        session,
        system_actor(deal.org_id),
        DRAFT_KEY,
        "subscription",
        sub.id,
        new=snap,
        reason="Deal won",
        request=RequestInfo(origin="system_job"),
    )
    await outbox.enqueue(
        session,
        deal.org_id,
        events.SUBSCRIPTION_DRAFTED,
        {
            "subscription_id": str(sub.id),
            "customer_id": str(customer.id),
            "deal_id": str(deal.id),
            "owner_id": str(customer.account_owner_id),
        },
    )
    return True


@on_event(pipeline_events.DEAL_WON)
async def draft_on_won(session: AsyncSession, event: OutboxEvent) -> None:
    deal = await session.get(Deal, uuid.UUID(event.payload["deal_id"]))
    if deal is None:
        return
    by_id = event.payload.get("by_id")
    await draft_for_deal(session, deal, uuid.UUID(by_id) if by_id else None)
