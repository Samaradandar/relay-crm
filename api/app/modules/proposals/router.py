# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""HTTP endpoints for proposals and the proposal template; thin, calls the service."""

import uuid
from typing import Any

from fastapi import APIRouter

from app.core.db import get_sessionmaker
from app.modules.proposals import rounds, service
from app.modules.proposals.schemas import (
    DraftIn,
    FeedbackIn,
    PrefillOut,
    ProposalOut,
    ProposalPatch,
    RoundsOut,
    SendIn,
    StatusIn,
    TemplateIn,
    TemplateOut,
)
from app.platform.deps import ActorDep, RequestDep

router = APIRouter(tags=["proposals"])


@router.get("/settings/proposal-template", response_model=TemplateOut | None)
async def get_template(actor: ActorDep) -> dict[str, Any] | None:
    async with get_sessionmaker()() as s:
        return await service.read_template(s, actor.org_id)


@router.put("/settings/proposal-template", response_model=TemplateOut)
async def put_template(body: TemplateIn, actor: ActorDep, req: RequestDep) -> dict[str, Any]:
    return await service.save_template(
        actor, req, body.name, body.body, body.terms_html, body.reason
    )


@router.post("/deals/{deal_id}/proposals", response_model=ProposalOut, status_code=201)
async def create_proposal(
    deal_id: uuid.UUID,
    actor: ActorDep,
    req: RequestDep,
    reason: str | None = None,
    recipient_name: str | None = None,
    recipient_email: str | None = None,
    body: DraftIn | None = None,
) -> dict[str, Any]:
    return await service.draft_proposal(
        actor, req, deal_id, reason, recipient_name, recipient_email, body
    )


@router.get("/deals/{deal_id}/proposal-prefill", response_model=PrefillOut)
async def proposal_prefill(deal_id: uuid.UUID, actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await service.prefill(s, actor, deal_id)


@router.get("/deals/{deal_id}/proposals", response_model=list[ProposalOut])
async def list_proposal_versions(deal_id: uuid.UUID, actor: ActorDep) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await service.versions(s, actor, deal_id)


@router.get("/proposals/{proposal_id}", response_model=ProposalOut)
async def get_proposal(proposal_id: uuid.UUID, actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await service.get_proposal(s, actor, proposal_id)


@router.patch("/proposals/{proposal_id}", response_model=ProposalOut)
async def patch_proposal(
    proposal_id: uuid.UUID, body: ProposalPatch, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await service.patch_proposal(actor, req, proposal_id, body.content, body.reason)


@router.post("/proposals/{proposal_id}/send", response_model=ProposalOut)
async def send_proposal(
    proposal_id: uuid.UUID, body: SendIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await service.send_proposal(actor, req, proposal_id, body.to, body.reason, body.to_name)


@router.post("/proposals/{proposal_id}/status", response_model=ProposalOut)
async def set_status(
    proposal_id: uuid.UUID, body: StatusIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    return await service.set_status(
        actor, req, proposal_id, body.status, body.feedback, body.reason
    )


# ------------------------------------------------------------------------------ rounds
@router.get("/deals/{deal_id}/proposal-rounds", response_model=RoundsOut)
async def proposal_rounds(deal_id: uuid.UUID, actor: ActorDep) -> dict[str, Any]:
    """Every answer the customer has given about the proposal, and where the current round is."""
    async with get_sessionmaker()() as s:
        return await rounds.list_rounds(s, actor, deal_id)


@router.post("/deals/{deal_id}/proposal-rounds", response_model=RoundsOut, status_code=201)
async def record_proposal_feedback(
    deal_id: uuid.UUID, body: FeedbackIn, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    """Record the customer's answer after the walkthrough meeting. Changes or objections close
    the round: a new meeting revises the proposal before the next answer."""
    await rounds.record_feedback(actor, req, deal_id, body)
    async with get_sessionmaker()() as s:
        return await rounds.list_rounds(s, actor, deal_id)
