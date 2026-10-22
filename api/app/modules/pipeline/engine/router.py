# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""HTTP endpoints for stage moves and exit criteria. The deal page itself is in `deals`."""

import uuid
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter

from app.modules.pipeline.engine import service
from app.modules.pipeline.engine.schemas import MoveIn, MoveOut, ReasonOnly, TickIn, VerifyIn
from app.platform.deps import ActorDep, RequestDep

router = APIRouter(tags=["pipeline"])


@router.get("/deals/{deal_id}/stage-history")
async def stage_history(deal_id: uuid.UUID, actor: ActorDep) -> list[dict[str, Any]]:
    from app.core.db import get_sessionmaker

    async with get_sessionmaker()() as s:
        return await service.stage_history(s, actor, deal_id)


@router.post("/deals/{deal_id}/move", response_model=MoveOut)
async def move(
    deal_id: uuid.UUID, body: MoveIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    out = await service.move(
        actor,
        req,
        deal_id,
        to_stage=body.to_stage,
        reason=body.reason,
        override=body.override,
        expected_stage=body.expected_stage,
        expected_version=body.expected_version,
    )
    return asdict(out)


@router.post("/deals/{deal_id}/criteria/{key}/tick", status_code=204)
async def tick(
    deal_id: uuid.UUID, key: str, body: TickIn, actor: ActorDep, req: RequestDep
) -> None:
    await service.tick(actor, req, deal_id, key, proof_ref=body.proof_ref, reason=body.reason)


@router.post("/deals/{deal_id}/criteria/{key}/untick", status_code=204)
async def untick(
    deal_id: uuid.UUID, key: str, body: ReasonOnly, actor: ActorDep, req: RequestDep
) -> None:
    await service.untick(actor, req, deal_id, key, body.reason)


@router.post("/deals/{deal_id}/criteria/{key}/verify", status_code=204)
async def verify(
    deal_id: uuid.UUID, key: str, body: VerifyIn, actor: ActorDep, req: RequestDep
) -> None:
    await service.verify(
        actor,
        req,
        deal_id,
        key,
        decision=body.decision,
        reason=body.reason,
        entry_id=body.entry_id,
    )


@router.post("/deals/{deal_id}/review-done", status_code=204)
async def review_done(
    deal_id: uuid.UUID, body: ReasonOnly, actor: ActorDep, req: RequestDep
) -> None:
    await service.clear_review(actor, req, deal_id, body.reason)
