# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Request and response shapes for moving deals and ticking criteria."""

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class MoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to_stage: str
    reason: str | None = None
    # ask to skip stages / override unticked criteria (only Team Leaders, Admin, Super admin may)
    override: bool = False
    expected_stage: str | None = None
    expected_version: int | None = None


class MoveOut(BaseModel):
    deal_id: uuid.UUID
    kind: str
    from_stage: str
    to_stage: str
    overridden: list[str]
    won: bool


class TickIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proof_ref: dict[str, Any] | None = None  # pick a specific activity or document
    reason: str | None = None


class ReasonOnly(BaseModel):
    reason: str | None = None


class VerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["verify", "reject"]
    reason: str | None = None
    entry_id: uuid.UUID | None = None
