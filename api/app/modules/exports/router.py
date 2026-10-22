# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""HTTP endpoints for exports."""

import uuid
from typing import Any

from fastapi import APIRouter

from app.core.db import get_sessionmaker
from app.modules.exports import service
from app.modules.exports.schemas import ExportIn, ExportOut
from app.platform.deps import ActorDep, RequestDep

router = APIRouter(tags=["exports"])


@router.post("/exports", response_model=ExportOut, status_code=201)
async def create_export(body: ExportIn, actor: ActorDep, req: RequestDep) -> dict[str, Any]:
    export_id = await service.request_export(
        actor, req, entity=body.entity, filters=body.filters, reason=body.reason
    )
    async with get_sessionmaker()() as s:
        return await service.get_export(s, actor, export_id)


@router.get("/exports/log", response_model=list[ExportOut])
async def export_log(actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await service.list_log(s, actor)


@router.get("/exports/mine", response_model=list[ExportOut])
async def my_exports(actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await service.list_mine(s, actor)


@router.get("/exports/{export_id}", response_model=ExportOut)
async def get_export(export_id: uuid.UUID, actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await service.get_export(s, actor, export_id)
