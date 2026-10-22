# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""HTTP endpoints for activities; thin, they call the service."""

import uuid
from typing import Any

from fastapi import APIRouter

from app.core.db import get_sessionmaker
from app.modules.activities import meetings, service
from app.modules.activities.schemas import (
    ActivityIn,
    ActivityOut,
    ActivityPatch,
    ActivityVersionOut,
    MeetingCancelIn,
    MeetingHeldIn,
    MeetingPostponeIn,
)
from app.platform.deps import ActorDep, RequestDep

router = APIRouter(tags=["activities"])


async def _one(actor: Any, activity_id: uuid.UUID) -> dict[str, Any]:
    from sqlalchemy import select

    from app.modules.activities.models import Activity

    async with get_sessionmaker()() as s:
        act = (await s.execute(select(Activity).where(Activity.id == activity_id))).scalar_one()
        return (await service._out(s, [act]))[0]


@router.get("/deals/{deal_id}/activities", response_model=list[ActivityOut])
async def list_activities(deal_id: uuid.UUID, actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await service.list_for_deal(s, actor, deal_id)


@router.post("/deals/{deal_id}/activities", response_model=ActivityOut, status_code=201)
async def log_activity(
    deal_id: uuid.UUID, body: ActivityIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await _one(actor, await service.log_activity(actor, req, deal_id, body))


@router.patch("/activities/{activity_id}", response_model=ActivityOut)
async def edit_activity(
    activity_id: uuid.UUID, body: ActivityPatch, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await _one(actor, await service.edit_activity(actor, req, activity_id, body))


@router.delete("/activities/{activity_id}", status_code=204)
async def delete_activity(
    activity_id: uuid.UUID, actor: ActorDep, req: RequestDep, reason: str | None = None
) -> None:
    await service.delete_activity(actor, req, activity_id, reason)


@router.get("/activities/{activity_id}/versions", response_model=list[ActivityVersionOut])
async def versions(activity_id: uuid.UUID, actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await service.versions_of(s, actor, activity_id)


# ------------------------------------------------------------------------------ meetings
@router.post("/activities/{activity_id}/meeting/held", response_model=ActivityOut)
async def meeting_held(
    activity_id: uuid.UUID, body: MeetingHeldIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    """A booked meeting took place: attendees and what was discussed."""
    return await _one(actor, await meetings.mark_held(actor, req, activity_id, body))


@router.post("/activities/{activity_id}/meeting/postpone", response_model=ActivityOut)
async def meeting_postpone(
    activity_id: uuid.UUID, body: MeetingPostponeIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    """Returns the NEW booking; the old one is kept, marked postponed, with the reason."""
    return await _one(actor, await meetings.postpone(actor, req, activity_id, body))


@router.post("/activities/{activity_id}/meeting/cancel", response_model=ActivityOut)
async def meeting_cancel(
    activity_id: uuid.UUID, body: MeetingCancelIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    """A booked meeting will not happen (or the customer did not turn up)."""
    return await _one(actor, await meetings.cancel(actor, req, activity_id, body))
