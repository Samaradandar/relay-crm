# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Churn recording and reversal (Phase 5, P5-06).

Permission key for recording and reversing a churn in the *normal* course: same key as renewal
and cancellation, `renewal_cancellation_churn.record` (owner/TL/Admin/SA, reason ALWAYS required
-- see reasons/policy.ALWAYS_REQUIRED_KEYS). Reversing a churn recorded *by mistake* is stricter:
Admin or Super admin only, via the new `churn.reverse` key (own/team denied, matching
catalog.template.manage's Admin+SA-only precedent in the matrix).

`ChurnIn.notes` is the only free-text field the API takes; it is used both as the audit reason
passed to `uow.reason()` (which enforces ALWAYS_REQUIRED_KEYS for this key) and as the stored
`ChurnEvent.notes`. The structured `reason_code` is validated separately against the org's
active `ChurnReason` rows and stored as `ChurnEvent.reason`.
"""

import uuid
from datetime import date
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound, ValidationFailed
from app.core.time import utc_now
from app.modules.billing.lifecycle import RECORD_KEY, _cancel_future_schedule
from app.modules.billing.models import BillingSchedule, MinutePack, Renewal, Subscription
from app.modules.billing.schedule import add_months
from app.modules.churn import events
from app.modules.churn.models import ChurnEvent, ChurnReason
from app.modules.customers.models import Customer
from app.modules.leads.models import Deal
from app.platform.context import Actor, RequestInfo
from app.platform.reads import authorize_read
from app.platform.uow import UnitOfWork

REVERSE_KEY = "churn.reverse"
REASONS_KEY = "churn_reasons.manage"


def _out(e: ChurnEvent) -> dict[str, Any]:
    return {
        "id": e.id,
        "customer_id": e.customer_id,
        "churn_date": e.churn_date,
        "reason": e.reason,
        "notes": e.notes,
        "monthly_value_lost_paise": e.monthly_value_lost_paise,
        "subscription_start": e.subscription_start,
        "time_as_customer_days": e.time_as_customer_days,
        "last_renewal_date": e.last_renewal_date,
        "recorded_by": e.recorded_by,
        "recorded_at": e.recorded_at,
        "returned_deal_id": e.returned_deal_id,
        "reversed_at": e.reversed_at,
        "reversed_by": e.reversed_by,
    }


async def _sub_for_customer(session: AsyncSession, customer_id: uuid.UUID) -> Subscription | None:
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


def compute_monthly_value_lost(
    subscription: Subscription, pack_history: list[tuple[date, int]], as_of: date
) -> int:
    """Pure, unit-testable. Computed once at churn time and stored on `ChurnEvent`; never
    recomputed later (a pack bought after churn must not change the stored figure -- callers
    pass only history up to `as_of`).

    SaaS: `fee_paise` converted to a monthly figure -- quarterly / 3, yearly / 12, monthly as-is
    (integer division, paise).

    Per-minute: the average monthly pack spend over the trailing 3 *calendar* months up to
    `as_of` (inclusive): sum of pack `price_paise` for packs purchased in that window, divided
    by 3 if there is a full 3 months of purchase history, or by the number of distinct months
    that have at least one purchase if there are fewer (never divide by zero -- a customer with
    no pack purchases in the window has 0 lost value, not a division error).
    """
    if subscription.pricing_model == "saas":
        fee = subscription.fee_paise or 0
        divisor = {"monthly": 1, "quarterly": 3, "yearly": 12}.get(
            subscription.billing_frequency or "monthly", 1
        )
        return fee // divisor
    window_start = add_months(as_of, -3)
    relevant = [(d, amt) for d, amt in pack_history if window_start <= d <= as_of]
    if not relevant:
        return 0
    months_with_purchase = {(d.year, d.month) for d, _ in relevant}
    total = sum(amt for _, amt in relevant)
    denom = len(months_with_purchase) if len(months_with_purchase) < 3 else 3
    return total // max(denom, 1)


async def _pack_history(
    session: AsyncSession, customer_id: uuid.UUID, as_of: date
) -> list[tuple[date, int]]:
    window_start = add_months(as_of, -3)
    rows = await session.execute(
        select(MinutePack.purchased_on, MinutePack.price_paise).where(
            MinutePack.customer_id == customer_id,
            MinutePack.deleted_at.is_(None),
            MinutePack.purchased_on >= window_start,
            MinutePack.purchased_on <= as_of,
        )
    )
    return [(r[0], r[1]) for r in rows.all()]


async def _flag_open_deals(session: AsyncSession, account_id: uuid.UUID) -> None:
    """Open Existing-customer deals for the account are flagged for the owner to close, mirroring
    the generic `Deal.flags` marker pattern used elsewhere (e.g. pipeline/engine/service.py's
    'review_needed'/'proof_missing') -- Deal already has this JSONB column, so no schema change
    is needed."""
    deals = (
        (
            await session.execute(
                select(Deal).where(
                    Deal.account_id == account_id,
                    Deal.pipeline == "existing_customer",
                    Deal.status == "open",
                    Deal.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for d in deals:
        flags = dict(d.flags or {})
        flags["churn_close_needed"] = True
        d.flags = flags


async def record_churn(
    actor: Actor,
    request: RequestInfo,
    customer_id: uuid.UUID,
    churn_date: date,
    reason_code: str,
    notes: str | None,
) -> dict[str, Any]:
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        customer = await s.get(Customer, customer_id)
        if customer is None or customer.deleted_at is not None:
            raise NotFound("Customer not found")
        await uow.authorize(RECORD_KEY, entity=("customer", customer.id))
        cleaned = uow.reason(RECORD_KEY, notes)
        reason_row = (
            await s.execute(
                select(ChurnReason).where(
                    ChurnReason.org_id == actor.org_id,
                    ChurnReason.label == reason_code,
                    ChurnReason.active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if reason_row is None:
            raise ValidationFailed("Choose a valid, active churn reason", {"field": "reason_code"})
        sub = await _sub_for_customer(s, customer_id)
        if sub is None:
            raise NotFound("This customer has no subscription")
        now = utc_now()
        # if a prior manual cancellation (lifecycle.record_cancellation) already ended this
        # subscription, don't double-process: leave ended_at/end_reason and the already-flagged
        # schedule rows as they are.
        if sub.cancellation_status != "cancelled":
            sub.ended_at = now
            sub.end_reason = cleaned
            sub.cancellation_status = "cancelled"
            await _cancel_future_schedule(s, sub, churn_date)
            sub.updated_at = now
        customer.status = "churned"
        customer.updated_at = now
        await _flag_open_deals(s, customer.account_id)
        history = await _pack_history(s, customer_id, churn_date)
        monthly_value = compute_monthly_value_lost(sub, history, churn_date)
        last_renewal_row = (
            await s.execute(
                select(Renewal.recorded_at)
                .where(Renewal.subscription_id == sub.id, Renewal.outcome == "renewed")
                .order_by(Renewal.recorded_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        start = sub.start_date or churn_date
        event = ChurnEvent(
            org_id=actor.org_id,
            customer_id=customer.id,
            churn_date=churn_date,
            reason=reason_row.label,
            notes=notes,
            monthly_value_lost_paise=monthly_value,
            subscription_start=start,
            time_as_customer_days=(churn_date - start).days,
            last_renewal_date=last_renewal_row.date() if last_renewal_row else None,
            recorded_by=actor.id,
        )
        s.add(event)
        await s.flush()
        await uow.audit(
            RECORD_KEY,
            "customer",
            customer.id,
            new={"status": "churned", "reason": reason_row.label},
            reason=cleaned,
        )
        await uow.emit(
            events.CHURN_RECORDED,
            {"customer_id": str(customer.id), "churn_event_id": str(event.id)},
        )
        out = _out(event)
    return out


async def _latest_unreversed_event(
    session: AsyncSession, customer_id: uuid.UUID
) -> ChurnEvent | None:
    return (
        (
            await session.execute(
                select(ChurnEvent)
                .where(ChurnEvent.customer_id == customer_id, ChurnEvent.reversed_at.is_(None))
                .order_by(ChurnEvent.churn_date.desc(), ChurnEvent.recorded_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def link_return_deal(session: AsyncSession, customer: Customer, deal_id: uuid.UUID) -> None:
    """Called from leads.service.create_request when a request/return is raised for a churned
    customer's account: links the new deal to their latest unreversed churn event so Churn
    History (P5-07) can show it as 'Returned'."""
    if customer.status != "churned":
        return
    event = await _latest_unreversed_event(session, customer.id)
    if event is not None and event.returned_deal_id is None:
        event.returned_deal_id = deal_id


async def record_return(
    actor: Actor, request: RequestInfo, customer_id: uuid.UUID, requirement: str
) -> uuid.UUID:
    """Thin convenience wrapper: a churned customer coming back raises a new deal under the same
    account via leads.service.create_request (pipeline=existing_customer, request_type='return'),
    which auto-links ChurnEvent.returned_deal_id (see `link_return_deal`). customer.status flips
    back to 'active' only once that deal's SubscriptionChange is confirmed (billing/changes.py)."""
    from app.modules.leads import service as leads_service

    async with UnitOfWork(actor, request) as uow:
        customer = await uow.session.get(Customer, customer_id)
    if customer is None:
        raise NotFound("Customer not found")
    body = SimpleNamespace(request_type="return", requirement=requirement, reason=None)
    return await leads_service.create_request(actor, request, customer.account_id, body)


async def reverse_churn(
    actor: Actor, request: RequestInfo, churn_event_id: uuid.UUID, reason: str | None
) -> dict[str, Any]:
    """Admin/Super admin only. Does not delete the original row -- `reversed_at`/`reversed_by`
    mark it, matching PR1's schema note ("stays, marked reversed").

    Also undoes the churn's side effects: Customer.status back to 'active', and -- only if this
    subscription's cancellation wasn't independently set before the churn (i.e. `ended_at` is
    still set, meaning this churn is the thing that ended it) -- Subscription.ended_at/end_reason
    cleared and the `cancelled` billing_schedule rows this churn's `_cancel_future_schedule` call
    flagged (every not-yet-invoiced row after `churn_date`) are un-flagged. If a subscription was
    already independently cancelled before the churn, this reversal intentionally leaves that
    cancellation alone -- there is no reliable way to tell which flagged rows came from which
    event, so we only touch schedule rows when we know this churn is what flagged them (`ended_at`
    was still set, i.e. no independent cancellation happened after)."""
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        event = await s.get(ChurnEvent, churn_event_id)
        if event is None:
            raise NotFound("Churn event not found")
        await uow.authorize(REVERSE_KEY, entity=("customer", event.customer_id))
        cleaned = uow.reason(REVERSE_KEY, reason)
        if event.reversed_at is not None:
            raise ValidationFailed("This churn record was already reversed")
        now = utc_now()
        event.reversed_at = now
        event.reversed_by = actor.id
        customer = await s.get(Customer, event.customer_id)
        if customer is not None:
            customer.status = "active"
            customer.updated_at = now
        sub = await _sub_for_customer(s, event.customer_id)
        if sub is not None and sub.ended_at is not None:
            sub.ended_at = None
            sub.end_reason = None
            sub.cancellation_status = "none"
            sub.updated_at = now
            rows = (
                await s.execute(
                    select(BillingSchedule).where(
                        BillingSchedule.subscription_id == sub.id,
                        BillingSchedule.period_start > event.churn_date,
                        BillingSchedule.invoice_id.is_(None),
                        BillingSchedule.cancelled.is_(True),
                    )
                )
            ).scalars()
            for row in rows:
                row.cancelled = False
        await s.flush()
        await uow.audit(
            REVERSE_KEY, "churn_event", event.id, new={"reversed": True}, reason=cleaned
        )
        await uow.emit(events.CHURN_REVERSED, {"churn_event_id": str(event.id)})
        out = _out(event)
    return out


def _reason_out(r: ChurnReason) -> dict[str, Any]:
    return {"id": r.id, "label": r.label, "active": r.active, "sort_order": r.sort_order}


async def list_churn_reasons(session: AsyncSession, actor: Actor) -> list[dict[str, Any]]:
    """Any authenticated role may read the list (it's shown in the churn-recording form)."""
    await authorize_read(session, actor, RECORD_KEY)
    rows = (
        (
            await session.execute(
                select(ChurnReason)
                .where(ChurnReason.org_id == actor.org_id)
                .order_by(ChurnReason.sort_order, ChurnReason.label)
            )
        )
        .scalars()
        .all()
    )
    return [_reason_out(r) for r in rows]


async def update_churn_reasons(
    actor: Actor, request: RequestInfo, items: list[dict[str, Any]], reason: str | None
) -> list[dict[str, Any]]:
    """Admin (with reason) or Super admin edits the org's churn-reason list (PRD Feature 9a).
    Upsert-only, never deletes: an item with a known `id` updates label/active/sort_order; an
    item with no `id` is a new reason. A reason already referenced by a `ChurnEvent` must stay
    selectable there even if retired here, so retiring a reason sets `active=False` instead of
    removing the row (`ChurnEvent.reason` stores the label as free text, not a foreign key)."""
    async with UnitOfWork(actor, request) as uow:
        s = uow.session
        await uow.authorize(REASONS_KEY)
        cleaned = uow.reason(REASONS_KEY, reason)
        existing = {
            r.id: r
            for r in (
                await s.execute(select(ChurnReason).where(ChurnReason.org_id == actor.org_id))
            )
            .scalars()
            .all()
        }
        touched: list[ChurnReason] = []
        for item in items:
            row = existing.get(item.get("id")) if item.get("id") else None
            if row is None:
                row = ChurnReason(org_id=actor.org_id)
                s.add(row)
            row.label = item["label"]
            row.active = item["active"]
            row.sort_order = item["sort_order"]
            touched.append(row)
        await s.flush()
        await uow.audit(
            REASONS_KEY,
            "org",
            actor.org_id,
            new={"churn_reasons": [{"label": r.label, "active": r.active} for r in touched]},
            reason=cleaned,
        )
        out = [_reason_out(r) for r in touched]
    return out
