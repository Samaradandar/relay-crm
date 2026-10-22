# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""HTTP endpoints for users and teams; thin, they call the service."""

import uuid
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.db import get_sessionmaker
from app.modules.users import service
from app.modules.users.schemas import (
    ReasonBody,
    TeamCreate,
    TeamOut,
    TeamPatch,
    UserCreate,
    UserOut,
    UserPatch,
)
from app.platform.deps import ActorDep, RequestDep

router = APIRouter(tags=["users"])


def _accepted(outcome: service.Outcome) -> JSONResponse | None:
    if outcome.approval is None:
        return None
    return JSONResponse(
        {
            "code": "approval_requested",
            "approval_id": str(outcome.approval.id),
            "message": "Your request was sent for approval",
        },
        status_code=202,
    )


@router.get("/users", response_model=list[UserOut])
async def list_users(actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await service.list_users(s, actor)


@router.post(
    "/users",
    response_model=UserOut,
    status_code=201,
    responses={202: {"description": "Sent for approval"}},
)
async def create_user(body: UserCreate, actor: ActorDep, req: RequestDep) -> Any:
    out = await service.create_user(
        actor,
        req,
        name=body.name,
        email=body.email,
        role=body.role,
        team_id=body.team_id,
        reason=body.reason,
    )
    if (accepted := _accepted(out)) is not None:
        return accepted
    async with get_sessionmaker()() as s:
        return await service.get_user(s, actor, out.user.id)  # type: ignore[union-attr]


@router.get("/users/{user_id}", response_model=UserOut)
async def get_user(user_id: uuid.UUID, actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await service.get_user(s, actor, user_id)


@router.patch(
    "/users/{user_id}",
    response_model=UserOut,
    responses={202: {"description": "Sent for approval"}},
)
async def patch_user(user_id: uuid.UUID, body: UserPatch, actor: ActorDep, req: RequestDep) -> Any:
    out = await service.update_user(
        actor,
        req,
        user_id,
        name=body.name,
        role=body.role,
        team_id=body.team_id,
        is_active=body.is_active,
        reason=body.reason,
        fields_set=set(body.model_fields_set),
        open_deals=body.open_deals,
    )
    if (accepted := _accepted(out)) is not None:
        return accepted
    async with get_sessionmaker()() as s:
        return await service.get_user(s, actor, user_id)


@router.post("/users/{user_id}/password-reset", status_code=204)
async def reset_password(
    user_id: uuid.UUID, body: ReasonBody, actor: ActorDep, req: RequestDep
) -> None:
    await service.reset_password(actor, req, user_id, body.reason)


@router.get("/teams", response_model=list[TeamOut])
async def list_teams(actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await service.list_teams(s, actor)


@router.post("/teams", response_model=TeamOut, status_code=201)
async def create_team(body: TeamCreate, actor: ActorDep, req: RequestDep) -> dict[str, Any]:
    team = await service.create_team(actor, req, body.name, body.reason)
    return {
        "id": team.id,
        "name": team.name,
        "leader_id": None,
        "leader_name": None,
        "member_count": 0,
    }


@router.patch("/teams/{team_id}", response_model=TeamOut)
async def rename_team(
    team_id: uuid.UUID, body: TeamPatch, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    await service.rename_team(actor, req, team_id, body.name, body.reason)
    async with get_sessionmaker()() as s:
        teams = await service.list_teams(s, actor)
    return next(t for t in teams if t["id"] == team_id)
