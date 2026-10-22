# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""The customer page billing tab: one call with everything it needs (P4-11).

Revenue is ex-GST by default; `gst_inclusive=True` adds the GST back in. Both views are built
from the same stored paise, so they always add up to the figure actually invoiced.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.modules.billing.invoices import list_invoices
from app.modules.billing.models import Subscription, SubscriptionVersion
from app.modules.billing.packs import list_packs
from app.modules.billing.payments import list_payments, payment_status
from app.modules.billing.subscriptions import _out as subscription_out
from app.modules.billing.subscriptions import _scoped_sub
from app.platform.context import Actor
from app.platform.reads import authorize_read

VIEW_KEY = "customer.view"


async def billing_tab(
    session: AsyncSession, actor: Actor, customer_id: uuid.UUID, *, gst_inclusive: bool = False
) -> dict[str, Any]:
    await authorize_read(session, actor, VIEW_KEY, entity=("customer", customer_id))

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
    subscription = None
    sub_versions: list[dict[str, Any]] = []
    if sub is not None:
        await _scoped_sub(session, actor, sub)
        subscription = subscription_out(sub)
        rows = (
            await session.execute(
                select(SubscriptionVersion)
                .where(SubscriptionVersion.subscription_id == sub.id)
                .order_by(SubscriptionVersion.version_no)
            )
        ).scalars()
        sub_versions = [
            {
                "version_no": v.version_no,
                "snapshot": v.snapshot,
                "reason": v.reason,
                "changed_at": v.changed_at,
            }
            for v in rows
        ]
    elif not await _customer_exists(session, actor.org_id, customer_id):
        raise NotFound("Customer not found")

    usage = await list_packs(session, actor, customer_id)
    invoices = await list_invoices(session, actor, customer_id)
    payments = await list_payments(session, actor, customer_id)
    status = await payment_status(session, customer_id)

    revenue_ex_gst = sum(i["amount_ex_gst_paise"] for i in invoices if i["status"] != "void")
    revenue_gst = sum(i["gst_paise"] for i in invoices if i["status"] != "void")
    revenue_paise = revenue_ex_gst + revenue_gst if gst_inclusive else revenue_ex_gst

    return {
        "customer_id": customer_id,
        "payment_status": status,
        "subscription": subscription,
        "subscription_versions": sub_versions,
        "minute_packs": usage["packs"],
        "overage": usage["overage"],
        "balance_minutes": usage["balance_minutes"],
        "unbilled_overage_minutes": usage["unbilled_overage_minutes"],
        "invoices": invoices,
        "payments": payments,
        "revenue_paise": revenue_paise,
        "revenue_ex_gst_paise": revenue_ex_gst,
        "revenue_gst_paise": revenue_gst,
        "gst_inclusive": gst_inclusive,
    }


async def _customer_exists(
    session: AsyncSession, org_id: uuid.UUID, customer_id: uuid.UUID
) -> bool:
    from app.modules.customers.models import Customer

    row = await session.get(Customer, customer_id)
    return row is not None and row.org_id == org_id and row.deleted_at is None
