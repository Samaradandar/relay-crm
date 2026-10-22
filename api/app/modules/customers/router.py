# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""HTTP endpoints for customers; thin, calls the service."""

import uuid
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.core.db import get_sessionmaker
from app.modules.customers import risk, service
from app.modules.customers.schemas import CustomerOut
from app.platform.deps import ActorDep, RequestDep

router = APIRouter(tags=["customers"])


class VoiceMappingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    voice_platform_customer_id: str | None = None
    reason: str | None = None


@router.get("/customers", response_model=list[CustomerOut])
async def customers(actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await service.list_customers(s, actor)


@router.get("/customers/at-risk")
async def at_risk(actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await risk.at_risk_customers(s, actor)


@router.get("/customers/{customer_id}", response_model=CustomerOut)
async def customer(customer_id: uuid.UUID, actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await service.get_customer(s, actor, customer_id)


@router.patch("/customers/{customer_id}/voice-mapping", response_model=CustomerOut)
async def set_voice_mapping(
    customer_id: uuid.UUID, body: VoiceMappingIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await service.set_voice_mapping(
        actor, req, customer_id, body.voice_platform_customer_id, body.reason
    )
