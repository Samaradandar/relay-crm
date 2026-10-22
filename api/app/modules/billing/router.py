# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""HTTP endpoints for billing; thin, calls the service."""

import uuid
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.db import get_sessionmaker
from app.modules.billing import (
    changes,
    invoices,
    lifecycle,
    packs,
    payments,
    service,
    subscriptions,
    tab,
)
from app.modules.billing.schemas import (
    CancellationIn,
    ChangeDecisionIn,
    ConfirmIn,
    GstOut,
    GstPut,
    InvoiceIn,
    InvoiceOut,
    InvoicePatch,
    PackIn,
    PaymentIn,
    PaymentOut,
    RenewalIn,
    ReverseIn,
    SubscriptionChangeOut,
    SubscriptionLifecycleOut,
    SubscriptionOut,
    SubscriptionPatch,
    VoidIn,
)
from app.platform.deps import ActorDep, RequestDep

router = APIRouter(tags=["billing"])


@router.get("/settings/gst", response_model=GstOut)
async def get_gst(actor: ActorDep) -> dict[str, object]:
    async with get_sessionmaker()() as s, s.begin():
        return await service.read_gst(s, actor)


@router.put("/settings/gst", response_model=GstOut)
async def put_gst(body: GstPut, actor: ActorDep, req: RequestDep) -> dict[str, object]:
    return await service.write_gst(actor, req, body.gst_rate_bp, body.reason)


@router.get("/customers/{customer_id}/subscription", response_model=SubscriptionOut)
async def get_subscription(customer_id: uuid.UUID, actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await subscriptions.get_subscription(s, actor, customer_id)


@router.patch("/customers/{customer_id}/subscription", response_model=SubscriptionOut)
async def patch_subscription(
    customer_id: uuid.UUID, body: SubscriptionPatch, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=True)
    return await subscriptions.update_contract(actor, req, customer_id, data)


@router.get("/subscriptions/pending", response_model=list[SubscriptionOut])
async def pending(actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await subscriptions.pending_confirmations(s, actor)


@router.post("/subscriptions/{subscription_id}/confirm", response_model=SubscriptionOut)
async def confirm(
    subscription_id: uuid.UUID, body: ConfirmIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    data = body.model_dump(exclude_unset=True)
    return await subscriptions.confirm_by_id(actor, req, subscription_id, data)


@router.get("/subscriptions/{subscription_id}/changes", response_model=list[SubscriptionChangeOut])
async def list_subscription_changes(
    subscription_id: uuid.UUID, actor: ActorDep
) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await changes.list_changes(s, actor, subscription_id)


@router.post("/subscription-changes/{change_id}/confirm", response_model=SubscriptionChangeOut)
async def confirm_subscription_change(
    change_id: uuid.UUID, body: ChangeDecisionIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await changes.confirm_change(actor, req, change_id, body.reason)


@router.post("/subscription-changes/{change_id}/reject", response_model=SubscriptionChangeOut)
async def reject_subscription_change(
    change_id: uuid.UUID, body: ChangeDecisionIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await changes.reject_change(actor, req, change_id, body.reason)


@router.post("/subscriptions/{subscription_id}/renewal", response_model=SubscriptionLifecycleOut)
async def record_renewal(
    subscription_id: uuid.UUID, body: RenewalIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await lifecycle.record_renewal(actor, req, subscription_id, body.outcome, body.reason)


@router.post(
    "/subscriptions/{subscription_id}/cancellation", response_model=SubscriptionLifecycleOut
)
async def record_cancellation(
    subscription_id: uuid.UUID, body: CancellationIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await lifecycle.record_cancellation(
        actor, req, subscription_id, body.action, body.end_date, body.reason
    )


@router.get("/customers/{customer_id}/schedule")
async def schedule(customer_id: uuid.UUID, actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await subscriptions.get_schedule(s, actor, customer_id)


@router.get("/customers/{customer_id}/invoices", response_model=list[InvoiceOut])
async def customer_invoices(customer_id: uuid.UUID, actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await invoices.list_invoices(s, actor, customer_id)


@router.get("/invoices/{invoice_id}", response_model=InvoiceOut)
async def get_invoice(invoice_id: uuid.UUID, actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await invoices.get_invoice(s, actor, invoice_id)


@router.post(
    "/customers/{customer_id}/invoices",
    response_model=InvoiceOut,
    status_code=201,
)
async def add_invoice(
    customer_id: uuid.UUID, body: InvoiceIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await invoices.add_invoice(actor, req, customer_id, body.model_dump())


@router.patch(
    "/invoices/{invoice_id}",
    response_model=InvoiceOut,
    responses={202: {"description": "Sent for approval"}},
)
async def patch_invoice(
    invoice_id: uuid.UUID, body: InvoicePatch, actor: ActorDep, req: RequestDep
) -> Any:
    out, approval = await invoices.edit_invoice(
        actor, req, invoice_id, body.model_dump(exclude_unset=True)
    )
    if approval is not None:
        return JSONResponse(
            {
                "code": "approval_requested",
                "approval_id": str(approval.id),
                "message": "Your request was sent for approval",
            },
            status_code=202,
        )
    return out


@router.post("/invoices/{invoice_id}/void", response_model=InvoiceOut)
async def void_invoice(
    invoice_id: uuid.UUID, body: VoidIn, actor: ActorDep, req: RequestDep
) -> Any:
    return await invoices.void_invoice(actor, req, invoice_id, body.reason)


@router.get("/customers/{customer_id}/payments")
async def customer_payments(customer_id: uuid.UUID, actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await payments.list_payments(s, actor, customer_id)


@router.post("/customers/{customer_id}/payments", response_model=PaymentOut, status_code=201)
async def record_payment(
    customer_id: uuid.UUID, body: PaymentIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await payments.add_payment(actor, req, customer_id, body.model_dump())


@router.post("/payments/{payment_id}/reverse", response_model=PaymentOut)
async def reverse_payment(
    payment_id: uuid.UUID, body: ReverseIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await payments.reverse_payment(actor, req, payment_id, body.reason)


@router.get("/customers/{customer_id}/usage")
async def customer_usage(customer_id: uuid.UUID, actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await packs.list_packs(s, actor, customer_id)


@router.get("/customers/{customer_id}/packs")
async def customer_packs(customer_id: uuid.UUID, actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await packs.list_packs(s, actor, customer_id)


@router.post("/customers/{customer_id}/packs", status_code=201)
async def add_pack(
    customer_id: uuid.UUID, body: PackIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await packs.add_pack_manually(actor, req, customer_id, body.model_dump())


@router.get("/customers/{customer_id}/billing")
async def billing_tab(
    customer_id: uuid.UUID, actor: ActorDep, gst_inclusive: bool = False
) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await tab.billing_tab(s, actor, customer_id, gst_inclusive=gst_inclusive)
