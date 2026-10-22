# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""HTTP endpoints for the catalog and a deal's product lines."""

import uuid
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.db import get_sessionmaker
from app.modules.catalog import service
from app.modules.catalog.models import Product
from app.modules.catalog.schemas import LineIn, LinePatch, LinesOut, ProductOut, ProductPut
from app.modules.leads.models import Deal
from app.platform.deps import ActorDep, RequestDep

router = APIRouter(tags=["catalog"])


@router.get("/products", response_model=list[ProductOut])
async def products(actor: ActorDep, include_inactive: bool = False) -> list[Product]:
    async with get_sessionmaker()() as s:
        return await service.list_products(s, actor, include_inactive)


@router.put("/products", response_model=list[ProductOut])
async def put_products(body: ProductPut, actor: ActorDep, req: RequestDep) -> list[Product]:
    return await service.put_products(actor, req, body.products, body.reason)


async def _result(actor: Any, deal_id: uuid.UUID, approval: Any) -> Any:
    async with get_sessionmaker()() as s:
        deal = await s.get(Deal, deal_id)
        assert deal is not None
        data = await service.lines_out(s, deal)
    if approval is not None:
        data["approval_id"] = str(approval.id)
        return JSONResponse(
            {
                "code": "approval_requested",
                "approval_id": str(approval.id),
                "message": "Your request was sent for approval",
            },
            status_code=202,
        )
    return data


@router.get("/deals/{deal_id}/lines", response_model=LinesOut)
async def lines(deal_id: uuid.UUID, actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await service.get_lines(s, actor, deal_id)


@router.post(
    "/deals/{deal_id}/lines",
    response_model=LinesOut,
    status_code=201,
    responses={202: {"description": "Sent for approval"}},
)
async def add_line(deal_id: uuid.UUID, body: LineIn, actor: ActorDep, req: RequestDep) -> Any:
    _, approval = await service.change_line(
        actor,
        req,
        deal_id,
        op="add",
        product_id=body.product_id,
        quantity=body.quantity,
        discount_pct=body.discount_pct,
        reason=body.reason,
    )
    return await _result(actor, deal_id, approval)


@router.patch(
    "/deals/{deal_id}/lines/{line_id}",
    response_model=LinesOut,
    responses={202: {"description": "Sent for approval"}},
)
async def update_line(
    deal_id: uuid.UUID, line_id: uuid.UUID, body: LinePatch, actor: ActorDep, req: RequestDep
) -> Any:
    _, approval = await service.change_line(
        actor,
        req,
        deal_id,
        op="update",
        line_id=line_id,
        quantity=body.quantity,
        discount_pct=body.discount_pct,
        reason=body.reason,
    )
    return await _result(actor, deal_id, approval)


@router.delete(
    "/deals/{deal_id}/lines/{line_id}",
    response_model=LinesOut,
    responses={202: {"description": "Sent for approval"}},
)
async def remove_line(
    deal_id: uuid.UUID,
    line_id: uuid.UUID,
    actor: ActorDep,
    req: RequestDep,
    reason: str | None = None,
) -> Any:
    _, approval = await service.change_line(
        actor, req, deal_id, op="remove", line_id=line_id, reason=reason
    )
    return await _result(actor, deal_id, approval)
