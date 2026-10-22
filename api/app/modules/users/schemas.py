# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Pydantic request and response shapes for users and teams."""

import re
import uuid
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _check_email(value: str) -> str:
    """Plain format check. (EmailStr rejects internal domains such as .local, which dev uses.)"""
    value = value.strip()
    if len(value) > 254 or not _EMAIL.match(value):
        raise ValueError("not a valid email address")
    return value.lower()


Email = Annotated[str, AfterValidator(_check_email)]

# Names are trimmed, so a whitespace-only name is empty and refused (BUG-P1-04).
PersonName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
TeamName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
# A reason is free text but not a novel (BUG-P1-22); 10,000 characters still pass.
Reason = Annotated[str, Field(max_length=20_000)]

RoleName = Literal["user", "team_leader", "admin", "super_admin"]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    name: str
    role: RoleName
    team_id: uuid.UUID | None
    team_name: str | None = None
    is_active: bool


class UserCreate(BaseModel):
    name: PersonName
    email: Email
    role: RoleName
    team_id: uuid.UUID | None = None
    reason: Reason | None = None


class UserPatch(BaseModel):
    """Only the fields sent are changed. One reason covers the whole change."""

    name: PersonName | None = None
    role: RoleName | None = None
    team_id: uuid.UUID | None = None
    is_active: bool | None = None
    # What happens to the rep's open deals when they change team (PRD 4.4 B).
    open_deals: Literal["move", "return_to_pool"] = "move"
    reason: Reason | None = None


class ReasonBody(BaseModel):
    reason: Reason | None = None


class TeamOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    leader_id: uuid.UUID | None
    leader_name: str | None = None
    member_count: int = 0


class TeamCreate(BaseModel):
    name: TeamName
    reason: Reason | None = None


class TeamPatch(BaseModel):
    name: TeamName
    reason: Reason | None = None


class ApprovalRequested(BaseModel):
    code: Literal["approval_requested"] = "approval_requested"
    approval_id: uuid.UUID
    message: str = "Your request was sent for approval"
