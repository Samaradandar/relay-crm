# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""HTTP endpoints for manual proposal, agreement and invoice records."""

import uuid
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.db import get_sessionmaker
from app.modules.documents import service
from app.modules.documents.schemas import DocumentIn, DocumentOut, DocumentPatch
from app.platform.deps import ActorDep, RequestDep

router = APIRouter(tags=["documents"])


@router.get("/deals/{deal_id}/documents", response_model=list[DocumentOut])
async def documents(deal_id: uuid.UUID, actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await service.list_for_deal(s, actor, deal_id)


@router.post("/deals/{deal_id}/documents", response_model=DocumentOut, status_code=201)
async def add_document(
    deal_id: uuid.UUID, body: DocumentIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    doc_id = await service.add_document(actor, req, deal_id, body)
    async with get_sessionmaker()() as s:
        return await service.one(s, doc_id)


@router.patch(
    "/documents/{doc_id}",
    response_model=DocumentOut,
    responses={202: {"description": "Sent for approval"}},
)
async def edit_document(
    doc_id: uuid.UUID, body: DocumentPatch, actor: ActorDep, req: RequestDep
) -> Any:
    _, approval_id = await service.edit_document(actor, req, doc_id, body)
    if approval_id is not None:
        return JSONResponse(
            {
                "code": "approval_requested",
                "approval_id": str(approval_id),
                "message": "Your request was sent for approval",
            },
            status_code=202,
        )
    async with get_sessionmaker()() as s:
        return await service.one(s, doc_id)


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(
    doc_id: uuid.UUID, actor: ActorDep, req: RequestDep, reason: str | None = None
) -> None:
    await service.delete_document(actor, req, doc_id, reason)
