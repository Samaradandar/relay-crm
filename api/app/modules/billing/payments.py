# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Payments: record with proof, allocate, derive invoice and customer status, reverse (P4-06).

Allocation default is oldest open invoice first; the caller may send its own list instead.
`payment_allocations` has a database trigger (migration 0008) that refuses to let any allocation
push a payment past its amount or an invoice past its total, so this module only has to not
exceed what it already checked - the trigger is the real guarantee.

A reversal never deletes or edits the original payment: it inserts a negative "correction"
payment for the same amount, with a mirror of the original's allocations (also negative), so the
invoices it paid go back to their derived status. Nothing is overwritten (PRD: "nothing is
deleted").
"""

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, PermissionDenied, ValidationFailed
from app.core.time import utc_now
from app.modules.billing.models import (
    BillingSchedule,
    Invoice,
    Payment,
    PaymentAllocation,
    Subscription,
)
from app.modules.billing.schedule import ist_today
from app.modules.customers.models import Customer
from app.platform.context import Actor, RequestInfo
from app.platform.permissions.roles import Role
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork

RECORD_KEY = "payment.record"
VIEW_KEY = "customer.view"
DUE_WINDOW_DAYS = 14


def _out(p: Payment) -> dict[str, Any]:
    return {
        "id": p.id,
        "customer_id": p.customer_id,
        "kind": p.kind,
        "amount_paise": p.amount_paise,
        "gst_paise": p.gst_paise,
        "received_on": p.received_on,
        "method": p.method,
        "reference": p.reference,
        "proof_file_id": p.proof_file_id,
        "reverses_payment_id": p.reverses_payment_id,
        "reason": p.reason,
        "recorded_by": p.recorded_by,
        "created_at": p.created_at,
    }


async def _open_invoices(session: AsyncSession, customer_id: uuid.UUID) -> list[Invoice]:
    rows = (
        await session.execute(
            select(Invoice)
            .where(
                Invoice.customer_id == customer_id,
                Invoice.status.in_(("open", "part_paid")),
                Invoice.deleted_at.is_(None),
            )
            .order_by(Invoice.due_date, Invoice.issue_date)
        )
    ).scalars()
    return list(rows)


async def _allocated(session: AsyncSession, invoice_id: uuid.UUID) -> int:
    rows = await session.execute(
        select(PaymentAllocation.amount_paise).where(PaymentAllocation.invoice_id == invoice_id)
    )
    return sum(r[0] for r in rows.all())


async def _refresh_invoice_status(session: AsyncSession, invoice: Invoice) -> None:
    if invoice.status == "void":
        return
    allocated = await _allocated(session, invoice.id)
    invoice.status = (
        "paid" if allocated >= invoice.total_paise else "part_paid" if allocated > 0 else "open"
    )
    invoice.updated_at = utc_now()


async def _advance_paid_through(session: AsyncSession, customer_id: uuid.UUID) -> None:
    """Paid-through moves to the end of the last billing period whose invoice is fully paid,
    counting forward with no gap from the subscription's current paid-through date."""
    subs = (
        await session.execute(
            select(Subscription).where(
                Subscription.customer_id == customer_id, Subscription.status == "confirmed"
            )
        )
    ).scalars()
    for sub in subs:
        rows = (
            await session.execute(
                select(BillingSchedule, Invoice.status)
                .outerjoin(Invoice, Invoice.id == BillingSchedule.invoice_id)
                .where(BillingSchedule.subscription_id == sub.id)
                .order_by(BillingSchedule.period_start)
            )
        ).all()
        through = sub.start_date
        for sched, status in rows:
            if status != "paid":
                break
            through = sched.period_end
        if through != sub.paid_through_date:
            sub.paid_through_date, sub.updated_at = through, utc_now()


async def add_payment(
    actor: Actor, request: RequestInfo, customer_id: uuid.UUID, body: dict[str, Any]
) -> dict[str, Any]:
    allocations_in: list[dict[str, Any]] | None = body.get("allocations")
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        await uow.authorize(RECORD_KEY, entity=("customer", customer_id))
        cleaned = uow.reason(RECORD_KEY, body.get("reason"))
        pay = Payment(
            org_id=actor.org_id,
            customer_id=customer_id,
            kind="receipt",
            amount_paise=body["amount_paise"],
            gst_paise=body.get("gst_paise", 0),
            received_on=body["received_on"],
            method=body["method"],
            reference=body.get("reference"),
            proof_file_id=body["proof_file_id"],
            recorded_by=actor.id,
        )
        s.add(pay)
        await s.flush()
        remaining = pay.amount_paise
        touched: list[Invoice] = []
        if allocations_in:
            for a in allocations_in:
                inv = await s.get(Invoice, a["invoice_id"])
                if inv is None or inv.customer_id != customer_id:
                    raise NotFound("Invoice not found for this customer")
                if inv.status == "void":
                    raise ValidationFailed("Cannot allocate a payment to a voided invoice")
                amt = int(a["amount_paise"])
                if amt <= 0:
                    raise ValidationFailed("an allocation must be a positive amount")
                s.add(
                    PaymentAllocation(
                        org_id=actor.org_id, payment_id=pay.id, invoice_id=inv.id, amount_paise=amt
                    )
                )
                remaining -= amt
                touched.append(inv)
        else:
            for inv in await _open_invoices(s, customer_id):
                if remaining <= 0:
                    break
                balance = inv.total_paise - await _allocated(s, inv.id)
                if balance <= 0:
                    continue
                take = min(balance, remaining)
                s.add(
                    PaymentAllocation(
                        org_id=actor.org_id,
                        payment_id=pay.id,
                        invoice_id=inv.id,
                        amount_paise=take,
                    )
                )
                remaining -= take
                touched.append(inv)
        await s.flush()  # the allocation trigger fires here, before we trust the numbers
        for inv in touched:
            await _refresh_invoice_status(s, inv)
        await _advance_paid_through(s, customer_id)
        await uow.audit(
            RECORD_KEY,
            "payment",
            pay.id,
            new={**_jsonable(_out(pay)), "unallocated_paise": remaining},
            reason=cleaned,
        )
        out = {**_out(pay), "unallocated_paise": remaining}
    return out


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in d.items():
        out[k] = (
            v.isoformat() if isinstance(v, date) else (str(v) if isinstance(v, uuid.UUID) else v)
        )
    return out


async def reverse_payment(
    actor: Actor, request: RequestInfo, payment_id: uuid.UUID, reason: str | None
) -> dict[str, Any]:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        original = await s.get(Payment, payment_id)
        if original is None:
            raise NotFound("Payment not found")
        if original.kind != "receipt":
            raise ValidationFailed("Only a receipt can be reversed")
        await uow.authorize(RECORD_KEY, entity=("customer", original.customer_id))
        if actor.role not in (Role.ADMIN, Role.SUPER_ADMIN):
            raise PermissionDenied("Only an Admin or Super admin may reverse a payment")
        cleaned = uow.reason(RECORD_KEY, reason)
        if cleaned is None:
            raise ValidationFailed("A reason is required to reverse a payment")
        allocs = (
            (
                await s.execute(
                    select(PaymentAllocation).where(PaymentAllocation.payment_id == payment_id)
                )
            )
            .scalars()
            .all()
        )
        correction = Payment(
            org_id=actor.org_id,
            customer_id=original.customer_id,
            kind="correction",
            amount_paise=-original.amount_paise,
            gst_paise=-original.gst_paise,
            received_on=ist_today(),
            method=original.method,
            reference=original.reference,
            proof_file_id=original.proof_file_id,
            reverses_payment_id=original.id,
            reason=cleaned,
            recorded_by=actor.id,
        )
        s.add(correction)
        await s.flush()
        touched_ids = {a.invoice_id for a in allocs}
        for a in allocs:
            s.add(
                PaymentAllocation(
                    org_id=actor.org_id,
                    payment_id=correction.id,
                    invoice_id=a.invoice_id,
                    amount_paise=-a.amount_paise,
                )
            )
        await s.flush()
        for inv_id in touched_ids:
            inv = await s.get(Invoice, inv_id)
            if inv is not None:
                await _refresh_invoice_status(s, inv)
        await _advance_paid_through(s, original.customer_id)
        await uow.audit(
            RECORD_KEY,
            "payment",
            correction.id,
            old=_jsonable(_out(original)),
            new=_jsonable(_out(correction)),
            reason=cleaned,
        )
        out = _out(correction)
    return out


# ---------------------------------------------------------------------------- reads
async def list_payments(
    session: AsyncSession, actor: Actor, customer_id: uuid.UUID
) -> list[dict[str, Any]]:
    await authorize_read(session, actor, VIEW_KEY, entity=("customer", customer_id))
    rows = (
        await session.execute(
            select(Payment)
            .where(Payment.customer_id == customer_id)
            .order_by(Payment.received_on, Payment.created_at)
        )
    ).scalars()
    out = []
    for p in rows:
        allocs = (
            await session.execute(
                select(PaymentAllocation).where(PaymentAllocation.payment_id == p.id)
            )
        ).scalars()
        out.append(
            {
                **_out(p),
                "allocations": [
                    {"invoice_id": a.invoice_id, "amount_paise": a.amount_paise} for a in allocs
                ],
            }
        )
    return out


async def payment_status(session: AsyncSession, customer_id: uuid.UUID) -> str:
    """Paid / Due / Overdue, derived from open invoices (P4-06)."""
    today = ist_today()
    rows = (
        (
            await session.execute(
                select(Invoice.due_date).where(
                    Invoice.customer_id == customer_id,
                    Invoice.status.in_(("open", "part_paid")),
                    Invoice.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return "paid"
    if any(d < today for d in rows):
        return "overdue"
    if any(d <= today + timedelta(days=DUE_WINDOW_DAYS) for d in rows):
        return "due"
    return "paid"


async def customer_payments_received(session: AsyncSession, deal_id: uuid.UUID) -> int:
    """Exact paise received against this deal's customer (Phase 6 incentives read this; it must
    never double count or round). Phase 4 gives one deal per customer (the first Won deal), so
    this is every payment allocation for that customer; Phase 5's top-up deals narrow it further.
    """
    customer_id = (
        await session.execute(select(Customer.id).where(Customer.first_won_deal_id == deal_id))
    ).scalar_one_or_none()
    if customer_id is None:
        return 0
    rows = await session.execute(
        select(PaymentAllocation.amount_paise)
        .join(Invoice, Invoice.id == PaymentAllocation.invoice_id)
        .where(Invoice.customer_id == customer_id)
    )
    return sum(r[0] for r in rows.all())
