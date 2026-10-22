# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""HTTP endpoints for churn recording, reversal, Churn History, and the churn-reasons settings
list; thin, calls the service.

Built in: Phase 5 (P5-06 record/reverse; P5-07 adds the Churn History read endpoints; the
`/settings/churn-reasons` pair closes the last Feature 9a gap -- "Admin with reason or Super
admin can edit the list"). The returning-customer flow reuses leads' existing
`POST /accounts/{id}/requests` endpoint (request_type='return'); it has no endpoint of its own
here (see churn/service.py's `record_return`/`link_return_deal` docstrings).

P5-07 gap: a "filter by product" option is not implemented. ChurnEvent has no direct product
reference, and the subscription/deal history does not carry a clean, single product link at
churn time (a subscription can span catalog changes over its life) -- adding one would mean a
schema change, which is out of scope for a reporting PR. Filters are churn date range, rep
(owner) and reason only; see churn/history.py's module docstring.
"""

import uuid
from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query

from app.core.db import get_sessionmaker
from app.modules.churn import history, service
from app.modules.churn.schemas import (
    ChurnHistoryPageOut,
    ChurnHistorySummaryOut,
    ChurnIn,
    ChurnOut,
    ChurnReasonOut,
    ChurnReasonsPut,
    ReverseIn,
)
from app.platform.deps import ActorDep, RequestDep

router = APIRouter(tags=["churn"])


@router.post("/customers/{customer_id}/churn", response_model=ChurnOut, status_code=201)
async def record_churn(
    customer_id: uuid.UUID, body: ChurnIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await service.record_churn(
        actor, req, customer_id, body.churn_date, body.reason_code, body.notes
    )


@router.post("/churn/{churn_event_id}/reverse", response_model=ChurnOut)
async def reverse_churn(
    churn_event_id: uuid.UUID, body: ReverseIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await service.reverse_churn(actor, req, churn_event_id, body.reason)


@router.get("/churn-history", response_model=ChurnHistoryPageOut)
async def churn_history(
    actor: ActorDep,
    date_from: date | None = None,
    date_to: date | None = None,
    owner_id: uuid.UUID | None = None,
    reason: str | None = None,
    sort: Literal["churn_date", "value_lost", "time_as_customer"] = "churn_date",
    desc: bool = True,
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await history.list_churn_history(
            s,
            actor,
            date_from=date_from,
            date_to=date_to,
            owner_id=owner_id,
            reason=reason,
            sort=sort,
            desc=desc,
            page=page,
            page_size=page_size,
        )


@router.get("/churn-history/summary", response_model=ChurnHistorySummaryOut)
async def churn_history_summary(
    actor: ActorDep,
    breakdown: Literal["reason", "rep", "month"] | None = None,
) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await history.summary(s, actor, breakdown=breakdown)


@router.get("/settings/churn-reasons", response_model=list[ChurnReasonOut])
async def get_churn_reasons(actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await service.list_churn_reasons(s, actor)


@router.put("/settings/churn-reasons", response_model=list[ChurnReasonOut])
async def put_churn_reasons(
    body: ChurnReasonsPut, actor: ActorDep, req: RequestDep
) -> list[dict[str, Any]]:
    return await service.update_churn_reasons(
        actor, req, [item.model_dump() for item in body.items], body.reason
    )
