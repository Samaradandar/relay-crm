# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""HTTP endpoints for rep profiles, team monitoring and team comparison."""

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query

from app.core.db import get_sessionmaker
from app.modules.profiles import service
from app.modules.profiles.schemas import MonitoringOut, ProfileOut, TeamCompareRow
from app.platform.deps import ActorDep

router = APIRouter(tags=["profiles"])
FromDate = Annotated[date | None, Query(alias="from")]


@router.get("/profiles/{user_id}", response_model=ProfileOut)
async def profile(
    user_id: uuid.UUID,
    actor: ActorDep,
    from_: FromDate = None,
    to: date | None = None,
) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await service.rep_profile(s, actor, user_id, from_, to)


@router.get("/monitoring", response_model=MonitoringOut)
async def monitoring(
    actor: ActorDep,
    team_id: uuid.UUID | None = None,
    from_: FromDate = None,
    to: date | None = None,
) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await service.monitoring(s, actor, team_id, from_, to)


@router.get("/teams-compare", response_model=list[TeamCompareRow])
async def compare(
    actor: ActorDep, from_: FromDate = None, to: date | None = None
) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await service.compare_teams(s, actor, from_, to)
