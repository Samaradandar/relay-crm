# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Pydantic request and response shapes for accounts and contacts."""

import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.core.fields import Name200, Opt200, Opt2000, Raw320, Raw2000


class AccountOut(BaseModel):
    id: uuid.UUID
    name: str
    website: str | None
    domain: str | None
    industry: str | None
    city: str | None
    account_owner_id: uuid.UUID | None


class ContactOut(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    name: str
    role: str | None
    phone: str | None
    email: str | None
    is_decision_maker: bool


class AccountDealRow(BaseModel):
    id: uuid.UUID
    status: str
    stage_key: str
    lead_source: str
    owner_name: str | None
    team_name: str | None


class LockedDealRow(BaseModel):
    """A deal on this account outside your scope: shown only as a locked row."""

    label: str = "Deal owned by another team"


class AccountDetail(AccountOut):
    deals: list[AccountDealRow]
    locked_deals: list[LockedDealRow]
    contacts: list[ContactOut]


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name200
    website: Raw2000 = None
    industry: Opt200 = None
    city: Opt200 = None
    create_anyway: bool = False
    reason: Opt2000 = None


class AccountPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name200 | None = None
    website: Raw2000 = None
    industry: Opt200 = None
    city: Opt200 = None
    reason: Opt2000 = None


class MergeBody(BaseModel):
    into_account_id: uuid.UUID
    reason: Opt2000 = None


class ContactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: uuid.UUID
    name: Name200
    role: Opt200 = None
    phone: Raw320 = None
    email: Raw320 = None
    is_decision_maker: bool = False
    reason: Opt2000 = None


class ContactPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name200 | None = None
    role: Opt200 = None
    phone: Raw320 = None
    email: Raw320 = None
    is_decision_maker: bool | None = None
    reason: Opt2000 = None


class DuplicateCheckIn(BaseModel):
    company: Name200
    website: Raw2000 = None
    phone: Raw320 = None
    email: Raw320 = None


class DuplicateCheckOut(BaseModel):
    block: list[dict[str, Any]]
    warn: list[dict[str, Any]]
