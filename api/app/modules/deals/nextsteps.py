# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Next steps: set, done (always with the next one), snooze +2 days, nudge, 'they replied', and
Team Leader task assignment (PRD 11 Feature 6).

A deal is never left without a next step, so Done and Set both replace the open step with a new
one. Every snooze is logged and counted; there is no limit.
"""

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Conflict, NotFound, OutOfScope
from app.core.time import utc_now
from app.modules.deals import health
from app.modules.deals.common import load_deal
from app.modules.deals.lifecycle import bulk_each
from app.modules.deals.steps import replace_step
from app.modules.leads import events as lead_events
from app.modules.leads.models import Deal, NextStep
from app.modules.leads.ownership import company_name, deal_rt
from app.modules.users.models import User
from app.platform.context import Actor, RequestInfo
from app.platform.permissions.roles import Scope
from app.platform.uow import UnitOfWork

KEY = "next_step.set"
ACT_KEY = "deal.snooze_nudge_replied"
SNOOZE_DAYS = 2
TASK_ASSIGNED = "task.assigned"


async def _step_deal(uow: UnitOfWork, step_id: uuid.UUID) -> tuple[NextStep, Deal]:
    step = (
        await uow.session.execute(
            select(NextStep)
            .where(NextStep.id == step_id, NextStep.org_id == uow.actor.org_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if step is None:
        raise NotFound("Next step not found")
    deal = await load_deal(uow, step.deal_id, lock=True)
    return step, deal


async def _set(
    uow: UnitOfWork,
    deal: Deal,
    *,
    text: str,
    due: Any,
    lane: str,
    responsible_id: uuid.UUID | None,
    reason: str | None,
    action: str,
) -> None:
    s = uow.session
    decision = await uow.authorize(KEY, entity=("deal", deal.id), owner_id=deal.owner_id)
    cleaned = uow.reason(KEY, reason)
    if deal.status not in ("open", "won"):
        raise Conflict("Next steps belong to open deals")
    assigned_by = None
    if responsible_id is not None and responsible_id != deal.owner_id:
        person = (
            await s.execute(
                select(User).where(User.id == responsible_id, User.org_id == deal.org_id)
            )
        ).scalar_one_or_none()
        if person is None:
            raise NotFound("Person not found")
        if decision.scope == Scope.OWN or (
            decision.scope == Scope.TEAM and person.team_id != uow.actor.team_id
        ):
            raise OutOfScope("You can only give a task to someone in your own scope")
        assigned_by = uow.actor.id
    await apply_step(
        uow, deal, text=text, due=due, lane=lane, responsible_id=responsible_id,
        assigned_by=assigned_by, reason=cleaned, action=action,
    )  # fmt: skip


async def apply_step(
    uow: UnitOfWork,
    deal: Deal,
    *,
    text: str,
    due: Any,
    lane: str = "your_move",
    responsible_id: uuid.UUID | None = None,
    assigned_by: uuid.UUID | None = None,
    reason: str | None = None,
    action: str,
    auto: bool = False,
    note: str | None = None,
) -> NextStep:
    """Replace the open step: the one write behind every way a next step changes (a person's Set,
    Done, and the system's own step from the pipeline). One audit row and one event."""
    s = uow.session
    old = await _current(s, deal)
    step = await replace_step(
        uow, deal, text=text, due=due, responsible_id=responsible_id, lane=lane,
        assigned_by=assigned_by, auto=auto,
    )  # fmt: skip
    deal.version, deal.updated_at = deal.version + 1, utc_now()
    await uow.audit(
        KEY,
        "deal",
        deal.id,
        old=old,
        new={"text": step.text, "due": step.due_date.isoformat(), "lane": step.lane,
             "action": action},
        reason=reason,
        note=note,
    )  # fmt: skip
    await uow.emit(lead_events.DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))
    if assigned_by is not None and step.responsible_id:
        await uow.emit(
            TASK_ASSIGNED,
            {
                "deal_id": str(deal.id),
                "user_id": str(step.responsible_id),
                "company": await company_name(s, deal),
                "text": step.text,
                "by": uow.actor.name,
            },
        )
    return step


async def _current(s: AsyncSession, deal: Deal) -> dict[str, Any] | None:
    from app.modules.deals.steps import open_step

    st = await open_step(s, deal.id)
    return {"text": st.text, "due": st.due_date.isoformat(), "lane": st.lane} if st else None


async def set_next_step(actor: Actor, request: RequestInfo, deal_id: uuid.UUID, body: Any) -> None:
    async with UnitOfWork(actor, request) as uow:
        deal = await load_deal(uow, deal_id, lock=True)
        await _set(
            uow, deal, text=body.text, due=body.due_date, lane=body.lane,
            responsible_id=body.responsible_id, reason=body.reason, action="set",
        )  # fmt: skip


async def done(actor: Actor, request: RequestInfo, step_id: uuid.UUID, body: Any) -> None:
    """Complete the step and, in the same save, set the next one."""
    async with UnitOfWork(actor, request) as uow:
        step, deal = await _step_deal(uow, step_id)
        if step.done_at is not None:
            raise Conflict("This step is already done")
        n = body.next
        await _set(
            uow, deal, text=n.text, due=n.due_date, lane=n.lane,
            responsible_id=n.responsible_id, reason=n.reason, action="done",
        )  # fmt: skip


async def snooze(
    actor: Actor,
    request: RequestInfo,
    step_id: uuid.UUID,
    reason: str | None = None,
    *,
    key: str = ACT_KEY,
) -> None:
    async with UnitOfWork(actor, request) as uow:
        step, deal = await _step_deal(uow, step_id)
        await uow.authorize(key, entity=("deal", deal.id), owner_id=deal.owner_id)
        cleaned = uow.reason(key, reason)
        if step.done_at is not None or deal.status != "open":
            raise Conflict("Only an open step can be snoozed")
        base = max(step.due_date, health.ist_date(utc_now()))
        before = step.due_date
        step.due_date = base + timedelta(days=SNOOZE_DAYS)
        step.snoozed_until, step.snooze_count = step.due_date, step.snooze_count + 1
        deal.version, deal.updated_at = deal.version + 1, utc_now()
        health.apply(deal, step)
        await uow.audit(
            key,
            "deal",
            deal.id,
            old={"due": before.isoformat()},
            new={"due": step.due_date.isoformat(), "snoozes": step.snooze_count},
            reason=cleaned,
            note="Snoozed +2 days",
        )
        await uow.emit(lead_events.DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))


async def replied(
    actor: Actor, request: RequestInfo, step_id: uuid.UUID, reason: str | None
) -> None:
    async with UnitOfWork(actor, request) as uow:
        step, deal = await _step_deal(uow, step_id)
        await uow.authorize(ACT_KEY, entity=("deal", deal.id), owner_id=deal.owner_id)
        cleaned = uow.reason(ACT_KEY, reason)
        if step.done_at is not None or step.lane != "waiting_on_them":
            raise Conflict("This step is not waiting on the customer")
        step.lane, step.lane_since = "your_move", utc_now()
        deal.version, deal.updated_at = deal.version + 1, utc_now()
        health.apply(deal, step)
        await uow.audit(
            ACT_KEY, "deal", deal.id, old={"lane": "waiting_on_them"}, new={"lane": "your_move"},
            reason=cleaned, note="They replied",
        )  # fmt: skip
        await uow.emit(lead_events.DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))


async def nudge(actor: Actor, request: RequestInfo, step_id: uuid.UUID, reason: str | None) -> None:
    """The rep nudged the customer: the two-day wait starts again."""
    async with UnitOfWork(actor, request) as uow:
        step, deal = await _step_deal(uow, step_id)
        await uow.authorize(ACT_KEY, entity=("deal", deal.id), owner_id=deal.owner_id)
        cleaned = uow.reason(ACT_KEY, reason)
        if step.done_at is not None or step.lane != "waiting_on_them":
            raise Conflict("This step is not waiting on the customer")
        step.lane_since = utc_now()
        health.apply(deal, step)
        await uow.audit(
            ACT_KEY, "deal", deal.id, new={"lane": "waiting_on_them"}, reason=cleaned,
            note="Nudged the customer",
        )  # fmt: skip
        await uow.emit(lead_events.DEAL_UPDATED, {"deal_id": str(deal.id)}, rt=deal_rt(deal))


async def bulk_snooze(
    actor: Actor, request: RequestInfo, deal_ids: list[uuid.UUID], reason: str | None
) -> dict[str, Any]:
    async def one(deal_id: uuid.UUID) -> None:
        async with UnitOfWork(actor, request) as uow:
            s = uow.session
            deal = await load_deal(uow, deal_id)
            from app.modules.deals.steps import open_step

            st = await open_step(s, deal.id)
        if st is None:
            raise NotFound("No open next step")
        await snooze(actor, request, st.id, reason, key="deal.bulk_snooze")

    return await bulk_each(deal_ids, one)
