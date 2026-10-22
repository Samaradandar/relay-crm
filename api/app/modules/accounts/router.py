# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""HTTP endpoints for accounts, contacts and the duplicate check; thin, they call the service."""

import uuid
from typing import Any

from fastapi import APIRouter

from app.core.db import get_sessionmaker
from app.core.normalize import normalize_domain, normalize_email, normalize_phone
from app.modules.accounts import duplicates, service, timeline
from app.modules.accounts.schemas import (
    AccountCreate,
    AccountDetail,
    AccountOut,
    AccountPatch,
    ContactCreate,
    ContactOut,
    ContactPatch,
    DuplicateCheckIn,
    DuplicateCheckOut,
    MergeBody,
)
from app.modules.accounts.timeline_schemas import TimelineOut
from app.platform.deps import ActorDep, RequestDep
from app.platform.reads import authorize_read

router = APIRouter(tags=["accounts"])


@router.post("/duplicates/check", response_model=DuplicateCheckOut)
async def check_duplicates(body: DuplicateCheckIn, actor: ActorDep) -> dict[str, Any]:
    """Live check while the new-lead form is being filled. Out-of-scope matches carry no ids."""
    async with get_sessionmaker()() as s:
        await authorize_read(s, actor, "deal.view")
        matches = await duplicates.find_matches(
            s,
            actor,
            company=body.company,
            domain=normalize_domain(body.website),
            phone_e164=normalize_phone(body.phone),
            email=normalize_email(body.email),
        )
    block, warn = duplicates.split(matches)
    return {"block": [m.public() for m in block], "warn": [m.public() for m in warn]}


@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(actor: ActorDep, q: str | None = None) -> list[dict[str, Any]]:
    async with get_sessionmaker()() as s:
        return await service.list_accounts(s, actor, q)


@router.get("/accounts/{account_id}", response_model=AccountDetail)
async def get_account(account_id: uuid.UUID, actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await service.get_account(s, actor, account_id)


@router.get("/accounts/{account_id}/timeline", response_model=TimelineOut)
async def account_timeline(account_id: uuid.UUID, actor: ActorDep) -> dict[str, Any]:
    async with get_sessionmaker()() as s:
        return await timeline.account_timeline(s, actor, account_id)


@router.post("/accounts", response_model=AccountOut, status_code=201)
async def create_account(body: AccountCreate, actor: ActorDep, req: RequestDep) -> dict[str, Any]:
    acc = await service.create_account(actor, req, body)
    return {
        "id": acc.id,
        "name": acc.name,
        "website": acc.website,
        "domain": acc.domain,
        "industry": acc.industry,
        "city": acc.city,
        "account_owner_id": acc.account_owner_id,
    }


@router.patch("/accounts/{account_id}", response_model=AccountOut)
async def patch_account(
    account_id: uuid.UUID, body: AccountPatch, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    patch = body.model_dump(exclude_unset=True, exclude={"reason"})
    acc = await service.update_account(actor, req, account_id, patch, body.reason)
    return {
        "id": acc.id,
        "name": acc.name,
        "website": acc.website,
        "domain": acc.domain,
        "industry": acc.industry,
        "city": acc.city,
        "account_owner_id": acc.account_owner_id,
    }


@router.post("/accounts/{account_id}/merge", response_model=AccountOut)
async def merge_account(
    account_id: uuid.UUID, body: MergeBody, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    acc = await service.merge_accounts(actor, req, account_id, body.into_account_id, body.reason)
    return {
        "id": acc.id,
        "name": acc.name,
        "website": acc.website,
        "domain": acc.domain,
        "industry": acc.industry,
        "city": acc.city,
        "account_owner_id": acc.account_owner_id,
    }


def _contact(c: Any) -> dict[str, Any]:
    return {
        "id": c.id,
        "account_id": c.account_id,
        "name": c.name,
        "role": c.role,
        "phone": c.phone_e164,
        "email": c.email,
        "is_decision_maker": c.is_decision_maker,
    }


@router.post("/contacts", response_model=ContactOut, status_code=201)
async def create_contact(body: ContactCreate, actor: ActorDep, req: RequestDep) -> dict[str, Any]:
    return _contact(await service.create_contact(actor, req, body))


@router.patch("/contacts/{contact_id}", response_model=ContactOut)
async def patch_contact(
    contact_id: uuid.UUID, body: ContactPatch, actor: ActorDep, req: RequestDep
) -> dict[str, Any]:
    patch = body.model_dump(exclude_unset=True, exclude={"reason"})
    return _contact(await service.update_contact(actor, req, contact_id, patch, body.reason))
