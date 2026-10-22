# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Invoices: add, edit (direct for Admin/SA, TL via approval), void (P4-05).

Invoices are recorded manually against a billing-schedule row or a minute pack; never both
(the schema enforces it). Every add writes `invoice_versions` version 1 and every edit appends
another, so the full history is visible. A TL's edit is never applied directly: it is turned
into an approval request routed to the Admin pool, and only runs when approved.
"""

import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.core.time import utc_now
from app.modules.billing.models import BillingSchedule, Invoice, InvoiceVersion, MinutePack
from app.platform.approvals import service as approvals
from app.platform.approvals.models import ApprovalRequest
from app.platform.context import Actor, RequestInfo
from app.platform.permissions.guard import Effect
from app.platform.permissions.roles import Role
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork

ADD_KEY = "invoice.add"
EDIT_KEY = "invoice.edit"
# The approval *type* must differ from the permission key: documents.service already registers
# an executor under the string "invoice.edit" for Phase 3's manual-invoice-record edits, and
# approvals.register_type keys its executor map by this exact string, so reusing it here would
# silently replace that executor instead of adding a second one.
APPROVAL_TYPE = "billing.invoice_edit"
VIEW_KEY = "customer.view"
NEEDS_APPROVAL = (Effect.NEEDS_APPROVAL, Effect.REQUEST_ONLY)
EDITABLE_FIELDS = (
    "number",
    "issue_date",
    "due_date",
    "amount_ex_gst_paise",
    "gst_paise",
    "file_id",
)


def _out(inv: Invoice) -> dict[str, Any]:
    return {
        "id": inv.id,
        "customer_id": inv.customer_id,
        "subscription_id": inv.subscription_id,
        "schedule_id": inv.schedule_id,
        "minute_pack_id": inv.minute_pack_id,
        "number": inv.number,
        "issue_date": inv.issue_date,
        "due_date": inv.due_date,
        "amount_ex_gst_paise": inv.amount_ex_gst_paise,
        "gst_paise": inv.gst_paise,
        "total_paise": inv.total_paise,
        "file_id": inv.file_id,
        "version": inv.version,
        "status": inv.status,
    }


def _snapshot(inv: Invoice) -> dict[str, Any]:
    d = _out(inv)
    d["issue_date"] = inv.issue_date.isoformat()
    d["due_date"] = inv.due_date.isoformat()
    d["id"], d["customer_id"] = str(d["id"]), str(d["customer_id"])
    for k in ("subscription_id", "schedule_id", "minute_pack_id", "file_id"):
        d[k] = str(d[k]) if d[k] else None
    return d


async def list_invoices(
    session: AsyncSession, actor: Actor, customer_id: uuid.UUID
) -> list[dict[str, Any]]:
    await authorize_read(session, actor, VIEW_KEY, entity=("customer", customer_id))
    rows = (
        await session.execute(
            select(Invoice)
            .where(Invoice.customer_id == customer_id, Invoice.deleted_at.is_(None))
            .order_by(Invoice.issue_date, Invoice.number)
        )
    ).scalars()
    return [_out(r) for r in rows]


async def get_invoice(session: AsyncSession, actor: Actor, invoice_id: uuid.UUID) -> dict[str, Any]:
    inv = await session.get(Invoice, invoice_id)
    if inv is None or inv.deleted_at is not None:
        raise NotFound("Invoice not found")
    await authorize_read(session, actor, VIEW_KEY, entity=("customer", inv.customer_id))
    return _out(inv)


async def add_invoice(
    actor: Actor, request: RequestInfo, customer_id: uuid.UUID, body: dict[str, Any]
) -> dict[str, Any]:
    ex, gst = body["amount_ex_gst_paise"], body["gst_paise"]
    schedule_id, pack_id = body.get("schedule_id"), body.get("minute_pack_id")
    if schedule_id and pack_id:
        raise ValidationFailed("link the invoice to at most one of a schedule row or a pack")
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        await uow.authorize(ADD_KEY, entity=("customer", customer_id))
        cleaned = uow.reason(ADD_KEY, body.get("reason"))
        sub_id = None
        if schedule_id:
            sched = await s.get(BillingSchedule, schedule_id)
            if sched is None:
                raise NotFound("Billing schedule row not found")
            sub_id = sched.subscription_id
        if pack_id:
            pack = await s.get(MinutePack, pack_id)
            if pack is None:
                raise NotFound("Minute pack not found")
            sub_id = pack.subscription_id
        inv = Invoice(
            org_id=actor.org_id,
            customer_id=customer_id,
            subscription_id=sub_id,
            schedule_id=schedule_id,
            minute_pack_id=pack_id,
            number=body["number"],
            issue_date=body["issue_date"],
            due_date=body["due_date"],
            amount_ex_gst_paise=ex,
            gst_paise=gst,
            total_paise=ex + gst,
            file_id=body.get("file_id"),
            created_by=actor.id,
        )
        s.add(inv)
        try:
            await s.flush()
        except Exception as exc:  # noqa: BLE001 - a duplicate number is the caller's mistake
            raise Conflict("An invoice with this number already exists") from exc
        if schedule_id:
            sched.invoice_id = inv.id  # type: ignore[union-attr]
        if pack_id:
            pack.invoice_id = inv.id  # type: ignore[union-attr]
        s.add(
            InvoiceVersion(
                org_id=actor.org_id,
                invoice_id=inv.id,
                version_no=1,
                snapshot=_snapshot(inv),
                reason=cleaned,
                changed_by=actor.id,
            )
        )
        await uow.audit(ADD_KEY, "invoice", inv.id, new=_snapshot(inv), reason=cleaned)
        out = _out(inv)
    return out


async def _apply_edit(
    uow: UnitOfWork, inv: Invoice, fields: dict[str, Any], reason: str | None
) -> None:
    s = uow.session
    old = _snapshot(inv)
    for k, v in fields.items():
        if k in EDITABLE_FIELDS and v is not None:
            setattr(inv, k, v)
    if "amount_ex_gst_paise" in fields or "gst_paise" in fields:
        inv.total_paise = inv.amount_ex_gst_paise + inv.gst_paise
    inv.version += 1
    inv.updated_at = utc_now()
    await s.flush()
    s.add(
        InvoiceVersion(
            org_id=inv.org_id,
            invoice_id=inv.id,
            version_no=inv.version,
            snapshot=_snapshot(inv),
            reason=reason,
            changed_by=uow.actor.id,
        )
    )
    await uow.audit(EDIT_KEY, "invoice", inv.id, old=old, new=_snapshot(inv), reason=reason)


async def edit_invoice(
    actor: Actor, request: RequestInfo, invoice_id: uuid.UUID, body: dict[str, Any]
) -> tuple[dict[str, Any] | None, ApprovalRequest | None]:
    """Direct for Admin / Super admin; a Team Leader's edit becomes an approval request."""
    fields = {k: v for k, v in body.items() if k != "reason" and k in EDITABLE_FIELDS}
    async with UnitOfWork(actor, request) as uow:
        inv = await uow.session.get(Invoice, invoice_id)
        if inv is None or inv.deleted_at is not None:
            raise NotFound("Invoice not found")
        if inv.status == "void":
            raise ValidationFailed("A voided invoice cannot be edited")
        decision = await uow.authorize(EDIT_KEY, entity=("customer", inv.customer_id))
        cleaned = uow.reason(EDIT_KEY, body.get("reason"))
        if decision.effect in NEEDS_APPROVAL:
            req = await approvals.create_request(
                uow,
                type_=APPROVAL_TYPE,
                action_key=EDIT_KEY,
                summary=f"Edit invoice {inv.number}",
                payload={
                    "invoice_id": str(invoice_id),
                    **{k: _jsonable(v) for k, v in fields.items()},
                },
                reason=cleaned,
                routed_to=(decision.rule.routed_to if decision.rule else None) or "admin_pool",
                team_id=actor.team_id,
                entity=("invoice", str(invoice_id)),
                record_owner_id=None,
            )
            return None, req
        await _apply_edit(uow, inv, fields, cleaned)
        out = _out(inv)
    return out, None


def _jsonable(v: Any) -> Any:
    return v.isoformat() if isinstance(v, date) else v


async def _exec_edit(uow: UnitOfWork, req: ApprovalRequest) -> None:
    p = dict(req.payload)
    invoice_id = uuid.UUID(p.pop("invoice_id"))
    p.pop("summary", None)
    inv = await uow.session.get(Invoice, invoice_id)
    if inv is None:
        raise NotFound("Invoice not found")
    fields: dict[str, Any] = {}
    for k, v in p.items():
        if k in ("issue_date", "due_date") and isinstance(v, str):
            v = date.fromisoformat(v)
        fields[k] = v
    await _apply_edit(uow, inv, fields, req.request_reason)


async def void_invoice(
    actor: Actor, request: RequestInfo, invoice_id: uuid.UUID, reason: str | None
) -> dict[str, Any]:
    async with UnitOfWork(actor, request) as uow:
        inv = await uow.session.get(Invoice, invoice_id)
        if inv is None or inv.deleted_at is not None:
            raise NotFound("Invoice not found")
        await uow.authorize("invoice.edit", entity=("customer", inv.customer_id))
        if actor.role not in (Role.ADMIN, Role.SUPER_ADMIN):
            from app.core.errors import PermissionDenied

            raise PermissionDenied("Only an Admin or Super admin may void an invoice")
        cleaned = uow.reason("invoice.edit", reason)
        old = _snapshot(inv)
        inv.status, inv.void_reason, inv.updated_at = "void", cleaned, utc_now()
        await uow.session.flush()
        await uow.audit(
            "invoice.edit", "invoice", inv.id, old=old, new=_snapshot(inv), reason=cleaned
        )
        out = _out(inv)
    return out


def register() -> None:
    approvals.register_type(APPROVAL_TYPE, _exec_edit)
