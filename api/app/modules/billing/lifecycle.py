# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Renewal and cancellation recording (Phase 5, P5-03).

Two paths exist for a renewal/cancellation and this module is only the *manual* one:
  - billing/changes.py's `_apply_renewal` fires when a Renewal-type existing_customer deal
    reaches Updated (deal.won) -- it both records the outcome and extends the contract dates.
  - This module is the direct owner/TL/Admin/SA action, with no deal involved at all: "Not
    renewing" (decline to renew) has no deal-pipeline equivalent, and "renewed" here is for a
    renewal handled outside the pipeline (e.g. already applied via a confirmed SubscriptionChange
    or a manual contract-date edit). To avoid double-extending the contract, `record_renewal`
    with outcome="renewed" only RECORDS the outcome and writes history -- it never touches
    contract_end_date/renewal_date itself; the caller is expected to have set those dates some
    other way first (confirm_change's renewal path, or subscriptions.update_contract).

Cancellation has three actions, all through `record_cancellation`:
  - request: flags the subscription as at risk (cancellation_requested_at is the trigger a later
    PR's churn-risk alert/banner reads; this module only emits the outbox event for it).
  - cancel: ends the subscription. `billing_schedule` is append-only by DB grant (P5-02's
    discovery, no DELETE for relay_app), so future schedule rows are flagged `cancelled=true`
    rather than deleted; existing/invoiced rows are never touched, so past invoices stand as-is.
  - revert: resets cancellation_status to "none" for history/display. It does NOT re-enable any
    billing-schedule rows a prior `cancel` flagged -- un-cancelling a subscription's future
    billing is a judgement call best made alongside the new contract dates, so it is left as a
    manual follow-up (flagged here, not built: out of scope for P5-03).

Permission key for every action here: `renewal_cancellation_churn.record` (owner with reason, TL
with reason, Admin with reason, Super admin with reason -- this key forces a reason even for a
Super admin; see reasons/policy.py's ALWAYS_REQUIRED_KEYS).
"""

import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, ValidationFailed
from app.core.time import utc_now
from app.modules.billing import events
from app.modules.billing.models import BillingSchedule, Renewal, Subscription
from app.platform.context import Actor, RequestInfo
from app.platform.uow import UnitOfWork

RECORD_KEY = "renewal_cancellation_churn.record"
RENEWAL_OUTCOMES = ("renewed", "not_renewing")
CANCELLATION_ACTIONS = ("request", "cancel", "revert")


def _out(sub: Subscription) -> dict[str, Any]:
    return {
        "id": sub.id,
        "customer_id": sub.customer_id,
        "renewal_status": sub.renewal_status,
        "cancellation_status": sub.cancellation_status,
        "cancellation_requested_at": sub.cancellation_requested_at,
        "contract_end_date": sub.contract_end_date,
        "renewal_date": sub.renewal_date,
        "ended_at": sub.ended_at,
        "end_reason": sub.end_reason,
    }


async def _get_sub(session: AsyncSession, subscription_id: uuid.UUID) -> Subscription:
    sub = await session.get(Subscription, subscription_id)
    if sub is None or sub.deleted_at is not None:
        raise NotFound("Subscription not found")
    return sub


async def record_renewal(
    actor: Actor,
    request: RequestInfo,
    subscription_id: uuid.UUID,
    outcome: str,
    reason: str | None,
) -> dict[str, Any]:
    if outcome not in RENEWAL_OUTCOMES:
        raise ValidationFailed(f"outcome must be one of {RENEWAL_OUTCOMES}")
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        sub = await _get_sub(s, subscription_id)
        await uow.authorize(RECORD_KEY, entity=("customer", sub.customer_id))
        cleaned = uow.reason(RECORD_KEY, reason)
        sub.renewal_status = outcome
        sub.updated_at = utc_now()
        s.add(
            Renewal(
                org_id=sub.org_id,
                subscription_id=sub.id,
                outcome=outcome,
                deal_id=None,
                reason=cleaned,
                recorded_by=actor.id,
            )
        )
        await s.flush()
        await uow.audit(
            RECORD_KEY,
            "subscription",
            sub.id,
            new={"renewal_status": outcome},
            reason=cleaned,
        )
        await uow.emit(
            events.SUBSCRIPTION_RENEWAL_RECORDED,
            {"subscription_id": str(sub.id), "outcome": outcome},
        )
        out = _out(sub)
    return out


async def _cancel_future_schedule(session: AsyncSession, sub: Subscription, end: date) -> None:
    """Flag (never delete -- append-only grant) every not-yet-invoiced schedule row due after
    `end`. Rows already linked to an invoice are untouched; past invoices stand as-is."""
    rows = (
        await session.execute(
            select(BillingSchedule).where(
                BillingSchedule.subscription_id == sub.id,
                BillingSchedule.period_start > end,
                BillingSchedule.invoice_id.is_(None),
            )
        )
    ).scalars()
    for row in rows:
        row.cancelled = True


async def record_cancellation(
    actor: Actor,
    request: RequestInfo,
    subscription_id: uuid.UUID,
    action: str,
    end_date: date | None,
    reason: str | None,
) -> dict[str, Any]:
    if action not in CANCELLATION_ACTIONS:
        raise ValidationFailed(f"action must be one of {CANCELLATION_ACTIONS}")
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        sub = await _get_sub(s, subscription_id)
        await uow.authorize(RECORD_KEY, entity=("customer", sub.customer_id))
        cleaned = uow.reason(RECORD_KEY, reason)
        now = utc_now()
        if action == "request":
            sub.cancellation_status = "requested"
            sub.cancellation_requested_at = now
            event_type = events.SUBSCRIPTION_CANCELLATION_REQUESTED
        elif action == "cancel":
            end = end_date or sub.contract_end_date
            if end is None:
                raise ValidationFailed(
                    "end_date is required: this subscription has no contract_end_date"
                )
            sub.cancellation_status = "cancelled"
            sub.ended_at = now
            sub.end_reason = cleaned
            await _cancel_future_schedule(s, sub, end)
            event_type = events.SUBSCRIPTION_CANCELLED
        else:  # revert
            sub.cancellation_status = "none"
            sub.cancellation_requested_at = None
            sub.ended_at = None
            sub.end_reason = None
            event_type = events.SUBSCRIPTION_CANCELLATION_REVERTED
        sub.updated_at = now
        await s.flush()
        await uow.audit(
            RECORD_KEY,
            "subscription",
            sub.id,
            new={"cancellation_status": sub.cancellation_status, "action": action},
            reason=cleaned,
        )
        await uow.emit(event_type, {"subscription_id": str(sub.id), "action": action})
        out = _out(sub)
    return out
