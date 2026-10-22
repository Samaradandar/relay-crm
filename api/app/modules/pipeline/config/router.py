# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""HTTP endpoints for pipeline configuration and the settings lists."""

from typing import Any

from fastapi import APIRouter

from app.core.db import get_sessionmaker
from app.modules.pipeline.config import service
from app.modules.pipeline.config.schemas import (
    CustomFieldOut,
    CustomFieldsPut,
    DraftIn,
    ImpactOut,
    ListItemOut,
    ListPut,
    PublishIn,
    RollbackIn,
    ValidationOut,
    VersionOut,
    VersionSummary,
)
from app.platform.deps import ActorDep, RequestDep

router = APIRouter(tags=["pipeline"])


@router.get("/pipelines/{key}/versions", response_model=list[VersionSummary])
async def versions(key: str, actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await service.list_versions(s, actor, key)


@router.get("/pipelines/{key}/versions/{version_no}", response_model=VersionOut)
async def version(key: str, version_no: int, actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await service.get_version(s, actor, key, version_no)


@router.post("/pipelines/{key}/versions", response_model=VersionOut)
async def save_draft(key: str, body: DraftIn, actor: ActorDep, req: RequestDep) -> dict[str, Any]:
    return await service.save_draft(actor, req, key, body)


@router.delete("/pipelines/{key}/draft", status_code=204)
async def discard_draft(
    key: str, actor: ActorDep, req: RequestDep, reason: str | None = None
) -> None:
    await service.discard_draft(actor, req, key, reason)


@router.post("/pipelines/{key}/versions/{version_no}/validate", response_model=ValidationOut)
async def validate(key: str, version_no: int, actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await service.validate(s, actor, key, version_no)


@router.get("/pipelines/{key}/versions/{version_no}/impact", response_model=ImpactOut)
async def impact(key: str, version_no: int, actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await service.impact(s, actor, key, version_no)


@router.post("/pipelines/{key}/versions/{version_no}/publish", response_model=VersionOut)
async def publish(
    key: str, version_no: int, body: PublishIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await service.publish(actor, req, key, version_no, body)


@router.post("/pipelines/{key}/rollback/{version_no}", response_model=VersionOut)
async def rollback(
    key: str, version_no: int, body: RollbackIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await service.rollback(actor, req, key, version_no, body)


# ---------------------------------------------------------------------------- settings lists
@router.get("/settings/custom-fields", response_model=list[CustomFieldOut])
async def get_custom_fields(actor: ActorDep) -> list[Any]:
    async with get_sessionmaker()() as s:
        return await service.list_custom_fields(s, actor)


@router.put("/settings/custom-fields", response_model=list[CustomFieldOut])
async def put_custom_fields(body: CustomFieldsPut, actor: ActorDep, req: RequestDep) -> list[Any]:
    return await service.put_custom_fields(actor, req, body.fields, body.reason)


@router.get("/settings/lost-reasons", response_model=list[ListItemOut])
async def get_lost_reasons(actor: ActorDep) -> list[Any]:
    async with get_sessionmaker()() as s:
        return await service.list_lost_reasons(s, actor)


@router.put("/settings/lost-reasons", response_model=list[ListItemOut])
async def put_lost_reasons(body: ListPut, actor: ActorDep, req: RequestDep) -> list[Any]:
    return await service.put_lost_reasons(actor, req, body.items, body.reason)


@router.get("/settings/fit-checklist", response_model=list[ListItemOut])
async def get_fit_checklist(actor: ActorDep) -> list[Any]:
    async with get_sessionmaker()() as s:
        return await service.list_fit_items(s, actor)


@router.put("/settings/fit-checklist", response_model=list[ListItemOut])
async def put_fit_checklist(body: ListPut, actor: ActorDep, req: RequestDep) -> list[Any]:
    return await service.put_fit_items(actor, req, body.items, body.reason)
