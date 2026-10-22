# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""HTTP endpoints for the deal page, closing, next steps, Today, the board and stage pages."""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query

from app.core.db import get_sessionmaker
from app.modules.deals import board as board_service
from app.modules.deals import lifecycle, nextsteps, today
from app.modules.deals import service as deal_service
from app.modules.deals.board_schemas import BoardCard, BoardOut, StageMetrics, StagePageOut
from app.modules.deals.build import deal_detail
from app.modules.deals.schemas import (
    BulkLoseIn,
    BulkOut,
    BulkOwnerIn,
    BulkSnoozeIn,
    DealDetail,
    DealPatch,
    DeleteIn,
    LoseIn,
    NextStepIn,
    ReopenIn,
    SnoozeIn,
    StepDoneIn,
    TodayOut,
    UndoIn,
)
from app.modules.leads import service as leads_service
from app.modules.leads.schemas import DealOut
from app.platform.deps import ActorDep, RequestDep

router = APIRouter(tags=["deals"])


async def _detail(actor: Any, deal_id: uuid.UUID, **extra: Any) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return {**await deal_detail(s, actor, deal_id), **extra}


@router.get("/deals", response_model=list[DealOut])
async def list_deals(
    actor: ActorDep,
    status: str | None = None,
    limit: Annotated[int, Query(ge=0, le=100_000)] = 100,
    offset: Annotated[int, Query(ge=0, le=1_000_000_000)] = 0,
    owner_id: uuid.UUID | None = None,
    stage_key: str | None = None,
    team_id: uuid.UUID | None = None,
    lead_source: str | None = None,
) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await leads_service.list_deals(
            s, actor, status, min(limit, 500), owner_id, stage_key, team_id, lead_source, offset
        )


# ---------------------------------------------------------------------------- bulk
@router.post("/deals/bulk/lose", response_model=BulkOut)
async def bulk_lose(body: BulkLoseIn, actor: ActorDep, req: RequestDep) -> dict[str, Any]:
    return await lifecycle.bulk_lose(actor, req, body.deal_ids, body.lost_reason, body.notes)


@router.post("/deals/bulk/snooze", response_model=BulkOut)
async def bulk_snooze(body: BulkSnoozeIn, actor: ActorDep, req: RequestDep) -> dict[str, Any]:
    return await nextsteps.bulk_snooze(actor, req, body.deal_ids, body.reason)


@router.post("/deals/bulk/reassign", response_model=BulkOut)
async def bulk_reassign_selected(
    body: BulkOwnerIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    async def one(did: uuid.UUID) -> None:
        await leads_service.reassign(actor, req, did, body.user_id, body.reason)

    return await lifecycle.bulk_each(body.deal_ids, one)


@router.get("/deals/{deal_id}", response_model=DealDetail)
async def get_deal(deal_id: uuid.UUID, actor: ActorDep) -> dict[str, Any]:
    return await _detail(actor, deal_id)


@router.patch("/deals/{deal_id}", response_model=DealDetail)
async def patch_deal(
    deal_id: uuid.UUID, body: DealPatch, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    _, audit_id = await deal_service.patch_deal(actor, req, deal_id, body)
    return await _detail(actor, deal_id, last_audit_id=str(audit_id) if audit_id else None)


@router.post("/deals/{deal_id}/undo", response_model=DealDetail)
async def undo_deal(
    deal_id: uuid.UUID, body: UndoIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    await deal_service.undo_patch(actor, req, deal_id, body.audit_id)
    return await _detail(actor, deal_id)


@router.post("/deals/{deal_id}/lose", response_model=DealDetail)
async def lose(
    deal_id: uuid.UUID, body: LoseIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    await lifecycle.lose(actor, req, deal_id, lost_reason=body.lost_reason, notes=body.notes)
    return await _detail(actor, deal_id)


@router.post("/deals/{deal_id}/reopen", response_model=DealDetail)
async def reopen(
    deal_id: uuid.UUID, body: ReopenIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    await lifecycle.reopen(actor, req, deal_id, body.reason)
    return await _detail(actor, deal_id)


@router.post("/deals/{deal_id}/delete", status_code=204)
async def delete_deal(deal_id: uuid.UUID, body: DeleteIn, actor: ActorDep, req: RequestDep) -> None:
    await lifecycle.delete_deal(actor, req, deal_id, body.reason)


@router.post("/deals/{deal_id}/restore", response_model=DealDetail)
async def restore_deal(
    deal_id: uuid.UUID, body: DeleteIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    await lifecycle.restore_deal(actor, req, deal_id, body.reason)
    return await _detail(actor, deal_id)


@router.get("/deleted/deals", response_model=list[DealOut])
async def deleted_deals(actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await lifecycle.list_deleted(s, actor)


# ---------------------------------------------------------------------------- next steps
@router.post("/deals/{deal_id}/next-step", status_code=204)
async def set_next_step(
    deal_id: uuid.UUID, body: NextStepIn, actor: ActorDep, req: RequestDep
) -> None:
    await nextsteps.set_next_step(actor, req, deal_id, body)


@router.post("/next-steps/{step_id}/done", status_code=204)
async def step_done(step_id: uuid.UUID, body: StepDoneIn, actor: ActorDep, req: RequestDep) -> None:
    await nextsteps.done(actor, req, step_id, body)


@router.post("/next-steps/{step_id}/snooze", status_code=204)
async def step_snooze(step_id: uuid.UUID, body: SnoozeIn, actor: ActorDep, req: RequestDep) -> None:
    await nextsteps.snooze(actor, req, step_id, body.reason)


@router.post("/next-steps/{step_id}/replied", status_code=204)
async def step_replied(
    step_id: uuid.UUID, body: SnoozeIn, actor: ActorDep, req: RequestDep
) -> None:
    await nextsteps.replied(actor, req, step_id, body.reason)


@router.post("/next-steps/{step_id}/nudge", status_code=204)
async def step_nudge(step_id: uuid.UUID, body: SnoozeIn, actor: ActorDep, req: RequestDep) -> None:
    await nextsteps.nudge(actor, req, step_id, body.reason)


@router.get("/today", response_model=TodayOut)
async def today_view(actor: ActorDep, user_id: uuid.UUID | None = None) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await today.today_view(s, actor, user_id)


# ---------------------------------------------------------------------------- board and stages
PipelineKey = Literal["new_business", "existing_customer"]


@router.get("/board", response_model=BoardOut)
async def board(
    actor: ActorDep,
    pipeline: PipelineKey = "new_business",
    team_id: uuid.UUID | None = None,
    owner_id: uuid.UUID | None = None,
    source: str | None = None,
    health: str | None = None,
    min_age_days: Annotated[int | None, Query(le=36_500)] = None,
    q: str | None = None,
    per_column: Annotated[int, Query(ge=0)] = 30,
) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await board_service.board(
            s,
            actor,
            pipeline=pipeline,
            team_id=team_id,
            owner_id=owner_id,
            source=source,
            health_=health,
            min_age_days=min_age_days,
            q=q,
            per_column=min(per_column, 100),
        )


@router.get("/board/cards", response_model=list[BoardCard])
async def board_cards(
    column: str,
    actor: ActorDep,
    pipeline: PipelineKey = "new_business",
    offset: Annotated[int, Query(ge=0, le=1_000_000_000)] = 0,
    limit: Annotated[int, Query(ge=0)] = 30,
    team_id: uuid.UUID | None = None,
    owner_id: uuid.UUID | None = None,
    source: str | None = None,
    health: str | None = None,
    min_age_days: Annotated[int | None, Query(le=36_500)] = None,
    q: str | None = None,
) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await board_service.board_cards(
            s,
            actor,
            column,
            pipeline=pipeline,
            offset=offset,
            limit=limit,
            team_id=team_id,
            owner_id=owner_id,
            source=source,
            health_=health,
            min_age_days=min_age_days,
            q=q,
        )


@router.get("/stages/metrics", response_model=list[StageMetrics])
async def all_metrics(
    actor: ActorDep, pipeline: PipelineKey = "new_business"
) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await board_service.all_stage_metrics(s, actor, pipeline)


@router.get("/stages/{key}/metrics", response_model=StageMetrics)
async def stage_metrics(
    key: str, actor: ActorDep, pipeline: PipelineKey = "new_business"
) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await board_service.stage_metrics(s, actor, key, pipeline)


@router.get("/stages/{key}/deals", response_model=StagePageOut)
async def stage_deals(
    key: str,
    actor: ActorDep,
    pipeline: PipelineKey = "new_business",
    page: Annotated[int, Query(ge=1, le=1_000_000)] = 1,
    page_size: int = 25,
    sort: Literal["age", "value", "company", "due"] = "age",
    desc: bool = False,
    team_id: uuid.UUID | None = None,
    owner_id: uuid.UUID | None = None,
    source: str | None = None,
    health: str | None = None,
    min_age_days: Annotated[int | None, Query(le=36_500)] = None,
    q: str | None = None,
) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await board_service.stage_page(
            s,
            actor,
            key,
            pipeline=pipeline,
            page=page,
            page_size=page_size,
            sort=sort,
            desc=desc,
            team_id=team_id,
            owner_id=owner_id,
            source=source,
            health_=health,
            min_age_days=min_age_days,
            q=q,
        )
