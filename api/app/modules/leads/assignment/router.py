# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Settings → Assignment: rules, groups, caps and a read-only test tool (Admin and above)."""

from typing import Any

from fastapi import APIRouter

from app.core.db import get_sessionmaker
from app.modules.leads.assignment import service
from app.modules.leads.schemas import (
    AssignmentSettingsIn,
    AssignmentSettingsOut,
    RuleTestIn,
    RuleTestOut,
)
from app.platform.deps import ActorDep, RequestDep

router = APIRouter(tags=["assignment"])


@router.get("/settings/assignment", response_model=AssignmentSettingsOut)
async def get_settings(actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await service.read_settings(s, actor)


@router.put("/settings/assignment", response_model=AssignmentSettingsOut)
async def put_settings(
    body: AssignmentSettingsIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await service.write_settings(actor, req, body)


@router.post("/settings/assignment/test", response_model=RuleTestOut)
async def test_rule(body: RuleTestIn, actor: ActorDep) -> dict[str, Any]:
    facts = {k: v for k, v in body.model_dump().items()}
    async with get_sessionmaker()() as s:
        return await service.test_rule(s, actor, facts)
