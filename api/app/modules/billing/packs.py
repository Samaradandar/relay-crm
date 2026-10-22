# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Minute packs and the usage ledger: drawdown, corrections, overage (P4-07).

Drawdown always draws from the oldest non-expired pack with balance first; a usage record bigger
than the remaining balance splits across packs, and once packs are exhausted the rest becomes
unbilled overage. A balance is always `size_minutes - sum(ledger rows for that pack)` -- never
stored separately, so it can never drift from the ledger that explains it.

A corrected usage record (same `source_record_id`, a higher `revision`, different minutes) draws
or returns the difference: if its pack still has room the correction lands on it, otherwise the
difference becomes a `late_correction` overage item. Minutes are kept to 3 decimal places and are
never rounded in a calculation; only the display layer rounds them.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, PermissionDenied, ValidationFailed
from app.core.time import utc_now
from app.modules.billing.models import MinuteLedger, MinutePack, OverageItem, Subscription
from app.modules.billing.schedule import ist_today
from app.modules.customers.models import Customer
from app.platform.context import Actor, RequestInfo
from app.platform.permissions.roles import Role
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork

ADD_PACK_KEY = "customer.view"  # gated to Admin/SA below (PRD: Admin + reason, Super admin)
VIEW_KEY = "customer.view"
ZERO = Decimal(0)


def _pack_out(p: MinutePack, balance: Decimal) -> dict[str, Any]:
    return {
        "id": p.id,
        "customer_id": p.customer_id,
        "subscription_id": p.subscription_id,
        "size_minutes": p.size_minutes,
        "price_paise": p.price_paise,
        "purchased_on": p.purchased_on,
        "expires_on": p.expires_on,
        "balance_minutes": balance,
        "used_minutes": p.size_minutes - balance,
    }


async def _pack_balance(session: AsyncSession, pack_id: uuid.UUID) -> Decimal:
    rows = await session.execute(
        select(MinuteLedger.minutes).where(MinuteLedger.minute_pack_id == pack_id)
    )
    used = sum((r[0] for r in rows.all()), ZERO)
    pack = await session.get(MinutePack, pack_id)
    assert pack is not None
    return pack.size_minutes - used


async def _draw_packs(
    session: AsyncSession, customer_id: uuid.UUID, today: date
) -> list[tuple[MinutePack, Decimal]]:
    """Non-expired packs with room, oldest purchase first (the drawdown order)."""
    # FOR UPDATE: two concurrent drawdowns (e.g. two usage-sync workers) for the same customer
    # must never both read the same stale balance and overdraw a pack past its size - the second
    # transaction blocks here until the first commits its ledger rows, then reads the fresh sum.
    rows = (
        await session.execute(
            select(MinutePack)
            .where(
                MinutePack.customer_id == customer_id,
                MinutePack.deleted_at.is_(None),
                (MinutePack.expires_on.is_(None)) | (MinutePack.expires_on >= today),
            )
            .order_by(MinutePack.purchased_on, MinutePack.id)
            .with_for_update()
        )
    ).scalars()
    out = []
    for p in rows:
        bal = await _pack_balance(session, p.id)
        if bal > 0:
            out.append((p, bal))
    return out


async def draw(
    session: AsyncSession,
    customer_id: uuid.UUID,
    usage_record_id: uuid.UUID | None,
    minutes: Decimal,
    *,
    kind: str = "draw",
    today: date | None = None,
) -> None:
    """Draw `minutes` from packs oldest-first; whatever is left becomes overage. `minutes` may
    be negative (a downward correction), in which case it is returned to the pack(s) the original
    draw came from, oldest first, never past that pack's original size."""
    today = today or ist_today()
    remaining = minutes
    if remaining > 0:
        for pack, balance in await _draw_packs(session, customer_id, today):
            if remaining <= 0:
                break
            take = min(balance, remaining)
            if take <= 0:
                continue
            session.add(
                MinuteLedger(
                    org_id=pack.org_id,
                    customer_id=customer_id,
                    usage_record_id=usage_record_id,
                    minute_pack_id=pack.id,
                    minutes=take,
                    kind=kind,
                )
            )
            remaining -= take
        if remaining > 0:
            reason = "no_pack" if kind == "draw" else "late_correction"
            session.add(
                OverageItem(
                    org_id=(await _org_id(session, customer_id)),
                    customer_id=customer_id,
                    minutes=remaining,
                    reason=reason,
                    usage_record_id=usage_record_id,
                )
            )
    elif remaining < 0:
        # a correction giving minutes back: credit the ledger rows this usage record created,
        # most recent first, down to (but not past) zero on each.
        prior = (
            (
                await session.execute(
                    select(MinuteLedger)
                    .where(
                        MinuteLedger.usage_record_id == usage_record_id,
                        MinuteLedger.minute_pack_id.is_not(None),
                    )
                    .order_by(MinuteLedger.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        to_return = -remaining
        for row in prior:
            if to_return <= 0:
                break
            credit = min(row.minutes, to_return)
            session.add(
                MinuteLedger(
                    org_id=row.org_id,
                    customer_id=customer_id,
                    usage_record_id=usage_record_id,
                    minute_pack_id=row.minute_pack_id,
                    minutes=-credit,
                    kind="correction",
                )
            )
            to_return -= credit
        if to_return > 0:
            # more was corrected away than this record ever drew from packs (it was partly
            # overage); reduce the unbilled overage for this record first, oldest first.
            items = (
                (
                    await session.execute(
                        select(OverageItem)
                        .where(
                            OverageItem.usage_record_id == usage_record_id,
                            OverageItem.status == "unbilled",
                        )
                        .order_by(OverageItem.created_at)
                    )
                )
                .scalars()
                .all()
            )
            for item in items:
                if to_return <= 0:
                    break
                cut = min(item.minutes, to_return)
                item.minutes -= cut
                item.updated_at = utc_now()
                to_return -= cut


async def _org_id(session: AsyncSession, customer_id: uuid.UUID) -> uuid.UUID:
    row = await session.get(Customer, customer_id)
    assert row is not None
    return row.org_id


async def add_pack_manually(
    actor: Actor, request: RequestInfo, customer_id: uuid.UUID, body: dict[str, Any]
) -> dict[str, Any]:
    """Admin / Super admin only, with reason (PRD P4-07: top-ups are Phase 5 deals; for now a
    manual add)."""
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        await uow.authorize(ADD_PACK_KEY, entity=("customer", customer_id))
        if actor.role not in (Role.ADMIN, Role.SUPER_ADMIN):
            raise PermissionDenied("Only an Admin or Super admin may add a pack")
        cleaned = uow.reason(ADD_PACK_KEY, body.get("reason"))
        if cleaned is None:
            raise ValidationFailed("A reason is required to add a pack")
        sub_id = body.get("subscription_id")
        if sub_id is None:
            sub = (
                (
                    await s.execute(
                        select(Subscription.id).where(Subscription.customer_id == customer_id)
                    )
                )
                .scalars()
                .first()
            )
            if sub is None:
                raise NotFound("This customer has no subscription")
            sub_id = sub
        pack = MinutePack(
            org_id=actor.org_id,
            customer_id=customer_id,
            subscription_id=sub_id,
            size_minutes=body["size_minutes"],
            price_paise=body["price_paise"],
            purchased_on=body["purchased_on"],
            expires_on=body.get("expires_on"),
            reason=cleaned,
            created_by=actor.id,
        )
        s.add(pack)
        await s.flush()
        await uow.audit(
            ADD_PACK_KEY,
            "minute_pack",
            pack.id,
            new={
                "customer_id": str(customer_id),
                "size_minutes": str(pack.size_minutes),
                "price_paise": pack.price_paise,
            },
            reason=cleaned,
        )
        out = _pack_out(pack, pack.size_minutes)
    return out


async def list_packs(session: AsyncSession, actor: Actor, customer_id: uuid.UUID) -> dict[str, Any]:
    await authorize_read(session, actor, VIEW_KEY, entity=("customer", customer_id))
    rows = (
        await session.execute(
            select(MinutePack)
            .where(MinutePack.customer_id == customer_id, MinutePack.deleted_at.is_(None))
            .order_by(MinutePack.purchased_on)
        )
    ).scalars()
    packs = [_pack_out(p, await _pack_balance(session, p.id)) for p in rows]
    overage_rows = (
        await session.execute(
            select(OverageItem)
            .where(OverageItem.customer_id == customer_id)
            .order_by(OverageItem.created_at)
        )
    ).scalars()
    overage = [
        {
            "id": o.id,
            "minutes": o.minutes,
            "reason": o.reason,
            "status": o.status,
            "invoice_id": o.invoice_id,
        }
        for o in overage_rows
    ]
    balances = (cast(Decimal, p["balance_minutes"]) for p in packs)
    unbilled = (cast(Decimal, o["minutes"]) for o in overage if o["status"] == "unbilled")
    return {
        "packs": packs,
        "overage": overage,
        "balance_minutes": sum(balances, ZERO),
        "unbilled_overage_minutes": sum(unbilled, ZERO),
    }
