# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""HTTP endpoints for leads, deals, the Lead Pool and transfers; thin, they call the services."""

import logging
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response
from fastapi.responses import JSONResponse

from app.core.db import get_sessionmaker
from app.modules.leads import service, transfers
from app.modules.leads.assignment.service import assign_pooled
from app.modules.leads.schemas import (
    AssignIn,
    BulkReassignIn,
    BulkResult,
    CommentIn,
    DealOut,
    DecideIn,
    DecisionOut,
    Distribution,
    LeadIn,
    MoveTeamIn,
    PoolRow,
    RequestIn,
    TransferIn,
    TransferLookupRow,
    TransferOut,
    WaitingLeadOut,
)
from app.platform.deps import ActorDep, RequestDep

log = logging.getLogger("relay.leads")
router = APIRouter(tags=["leads"])


async def _deal(actor: Any, deal_id: uuid.UUID, **extra: Any) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        out = await service.get_deal(s, actor, deal_id)
    return {**out, **extra}


@router.post("/leads", response_model=DealOut, status_code=201)
async def create_lead(body: LeadIn, actor: ActorDep, req: RequestDep) -> dict[str, Any]:
    deal_id, warn, assigned = await service.create_lead(actor, req, body)
    if not assigned:
        try:  # run the engine now; the outbox 'deal.pooled' event is the safety net if this fails
            await assign_pooled(actor.org_id, deal_id)
        except Exception:  # noqa: BLE001
            log.exception("inline assignment failed for %s; the outbox will retry", deal_id)
    async with get_sessionmaker()() as s:
        out = await service.created_deal(s, actor, deal_id)
    # a rep's lead is not assigned: it waits for the Team Leader (the form says so)
    held = assigned and out["owner"] is None  # engine skipped and no owner: held for the leader
    return {**out, "warning": warn, "awaiting_team_leader": held}


@router.post("/accounts/{account_id}/requests", response_model=DealOut, status_code=201)
async def create_request(
    account_id: uuid.UUID, body: RequestIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    """Phase 5 (P5-01): a request on an existing customer's account. Skips the Lead Pool."""
    deal_id = await service.create_request(actor, req, account_id, body)
    return await _deal(actor, deal_id)


@router.get("/leads/waiting", response_model=list[WaitingLeadOut])
async def waiting_leads(actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await service.waiting_for_leader(s, actor)


@router.post("/deals/{deal_id}/reassign", response_model=DealOut)
async def reassign(
    deal_id: uuid.UUID, body: AssignIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    _, warn = await service.reassign(actor, req, deal_id, body.user_id, body.reason)
    return await _deal(actor, deal_id, warning=warn)


@router.post("/deals/bulk-reassign", response_model=BulkResult)
async def bulk_reassign(body: BulkReassignIn, actor: ActorDep, req: RequestDep) -> dict[str, Any]:
    return await service.bulk_reassign(
        actor, req, body.from_user_id, body.to_user_id, body.spread, body.reason
    )


@router.post("/deals/{deal_id}/move-team", response_model=DealOut)
async def move_team(
    deal_id: uuid.UUID, body: MoveTeamIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    await service.move_team_direct(actor, req, deal_id, body.to_team_id, body.owner_id, body.reason)
    return await _deal(actor, deal_id)


# ---------------------------------------------------------------------------- Lead Pool
@router.get("/pool", response_model=list[PoolRow])
async def pool(
    actor: ActorDep,
    response: Response,
    source: str | None = None,
    team_id: uuid.UUID | None = None,
    waiting_reason: str | None = None,
    min_minutes: int | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[dict[str, Any]]:
    total: list[int] = []
    async with get_sessionmaker()() as s:
        rows = await service.list_pool(
            s,
            actor,
            source=source,
            team_id=team_id,
            waiting_reason=waiting_reason,
            min_minutes=min_minutes,
            limit=limit,
            offset=offset,
            total=total,
        )
    response.headers["X-Total-Count"] = str(total[0] if total else len(rows))
    return rows


@router.get("/pool/distribution", response_model=Distribution)
async def distribution(actor: ActorDep, days: int = 30) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await service.distribution(s, actor, min(max(days, 1), 365))


@router.post("/pool/{deal_id}/assign", response_model=DealOut)
async def assign_from_pool(
    deal_id: uuid.UUID, body: AssignIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    _, warn = await service.assign_from_pool(actor, req, deal_id, body.user_id, body.reason)
    return await _deal(actor, deal_id, warning=warn)


@router.post("/pool/{deal_id}/decide", response_model=DecisionOut)
async def decide(
    deal_id: uuid.UUID, body: DecideIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await service.decide_rep_lead(
        actor, req, deal_id, body.action, body.user_id, body.reason
    )


# ---------------------------------------------------------------------------- transfers
@router.get("/transfers", response_model=list[TransferOut])
async def list_transfers(actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await transfers.list_transfers(s, actor)


@router.get("/transfers/lookup", response_model=list[TransferLookupRow])
async def lookup(q: str, actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await transfers.lookup(s, actor, q)


@router.post("/transfers", status_code=202)
async def request_transfer(body: TransferIn, actor: ActorDep, req: RequestDep) -> JSONResponse:
    approval = await transfers.request_transfer(actor, req, body)
    return JSONResponse(
        {
            "code": "approval_requested",
            "approval_id": str(approval.id),
            "message": "Your request was sent for approval",
        },
        status_code=202,
    )


@router.post("/transfers/{transfer_id}/comment", status_code=204)
async def comment(
    transfer_id: uuid.UUID, body: CommentIn, actor: ActorDep, req: RequestDep
) -> None:
    await transfers.comment(actor, req, transfer_id, body.comment)
