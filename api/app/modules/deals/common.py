# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Small helpers shared by every module that works on one deal."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.modules.leads.models import Deal
from app.platform.context import Actor
from app.platform.uow import UnitOfWork

SCOPE_KEY = "deal.view"


async def load_deal(
    uow: UnitOfWork, deal_id: uuid.UUID, *, lock: bool = False, deleted: bool = False
) -> Deal:
    """The deal in the actor's org (never one from another org). `lock` takes the row lock that
    serialises every change to a deal: moves, ticks, edits, closing."""
    stmt = select(Deal).where(Deal.id == deal_id, Deal.org_id == uow.actor.org_id)
    if not deleted:
        stmt = stmt.where(Deal.deleted_at.is_(None))
    if lock:
        stmt = stmt.with_for_update()
    deal = (await uow.session.execute(stmt)).scalar_one_or_none()
    if deal is None:
        raise NotFound("Deal not found")
    return deal


async def ensure_live_deal(session: AsyncSession, actor: Actor, deal_id: uuid.UUID) -> None:
    """Read paths: a soft-deleted deal (and so everything under it) is not readable (PRD matrix)."""
    found = (
        await session.execute(
            select(Deal.id).where(
                Deal.id == deal_id, Deal.org_id == actor.org_id, Deal.deleted_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if found is None:
        raise NotFound("Deal not found")
