# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Next-step helpers that run inside a caller's UnitOfWork (a deal never has more than one open
next step, and never none: saving anything that completes one asks for the next)."""

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationFailed
from app.core.time import utc_now
from app.modules.deals import health
from app.modules.leads.models import Deal, NextStep
from app.modules.users.models import User
from app.platform.uow import UnitOfWork

LANES = ("your_move", "waiting_on_them")


async def open_step(
    session: AsyncSession, deal_id: uuid.UUID, *, lock: bool = False
) -> NextStep | None:
    stmt = (
        select(NextStep)
        .where(NextStep.deal_id == deal_id, NextStep.done_at.is_(None))
        .order_by(NextStep.created_at.desc())
        .limit(1)
    )
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalars().first()


def check_step(text: str | None, due: date | None, lane: str = "your_move") -> str:
    clean = (text or "").strip()
    if not clean:
        raise ValidationFailed("A next step is required", {"field": "next_step_text"})
    if due is None:
        raise ValidationFailed("A due date is required", {"field": "next_step_due_date"})
    if due < health.ist_date(utc_now()):
        raise ValidationFailed(
            "The due date cannot be in the past", {"field": "next_step_due_date"}
        )
    if lane not in LANES:
        raise ValidationFailed("Unknown lane", {"field": "lane"})
    return clean


async def replace_step(
    uow: UnitOfWork,
    deal: Deal,
    *,
    text: str | None,
    due: date | None,
    responsible_id: uuid.UUID | None = None,
    lane: str = "your_move",
    assigned_by: uuid.UUID | None = None,
    auto: bool = False,
) -> NextStep:
    """Complete the open step (if any) and open the next one; refresh the deal's health."""
    clean = check_step(text, due, lane)
    s = uow.session
    who = responsible_id or deal.owner_id
    if who is not None:
        person = (
            await s.execute(select(User).where(User.id == who, User.org_id == deal.org_id))
        ).scalar_one_or_none()
        if person is None or not person.is_active:
            raise ValidationFailed("Choose an active person", {"field": "responsible_id"})
    now = utc_now()
    old = await open_step(s, deal.id, lock=True)
    if old is not None:
        old.done_at = now
    step = NextStep(
        org_id=deal.org_id,
        deal_id=deal.id,
        text=clean,
        due_date=due,
        responsible_id=who,
        created_by=uow.actor.id,
        lane=lane,
        lane_since=now,
        assigned_by=assigned_by,
        auto_generated=auto,
    )
    s.add(step)
    await s.flush()
    health.apply(deal, step, now)
    return step
