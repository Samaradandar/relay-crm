# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Billing rules; the only place billing writes happen (permission, reason, audit, outbox).

This file holds the org billing settings (GST). Subscriptions, invoices, payments and packs
join it in the following Phase 4 pull requests.
"""

from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.modules.billing.models import BillingSettings
from app.platform.context import Actor, RequestInfo
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork

GST_KEY = "catalog.template.manage"  # Admin + reason, Super admin (PRD 12: GST is a setting)
DEFAULT_GST_BP = 1800


async def settings_row(session: AsyncSession, org_id: object) -> BillingSettings:
    """The org's billing settings; created with the default on first use (new orgs have none)."""
    await session.execute(pg_insert(BillingSettings).values(org_id=org_id).on_conflict_do_nothing())
    row = await session.get(BillingSettings, org_id, populate_existing=True)
    assert row is not None
    return row


def percent_text(bp: int) -> str:
    """Basis points as an exact percentage string, never via float."""
    return format((Decimal(bp) / 100).normalize(), "f")


async def read_gst(session: AsyncSession, actor: Actor) -> dict[str, object]:
    await authorize_read(session, actor, "customer.view")
    row = await settings_row(session, actor.org_id)
    return {"gst_rate_bp": row.gst_rate_bp, "gst_rate_percent": percent_text(row.gst_rate_bp)}


async def write_gst(
    actor: Actor, request: RequestInfo, gst_rate_bp: int, reason: str | None
) -> dict[str, object]:
    """Change the GST rate. Invoices and payments keep the GST they were stored with."""
    async with UnitOfWork(actor, request) as uow:
        await uow.authorize(GST_KEY)
        cleaned = uow.reason(GST_KEY, reason)
        row = await settings_row(uow.session, actor.org_id)
        old = {"gst_rate_bp": row.gst_rate_bp}
        row.gst_rate_bp = gst_rate_bp
        row.updated_at, row.updated_by = utc_now(), actor.id
        await uow.session.flush()
        await uow.audit(
            GST_KEY,
            "billing_settings",
            actor.org_id,
            old=old,
            new={"gst_rate_bp": gst_rate_bp},
            reason=cleaned,
        )
    return {"gst_rate_bp": gst_rate_bp, "gst_rate_percent": percent_text(gst_rate_bp)}
