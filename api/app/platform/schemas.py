# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Response shapes for platform endpoints. The frontend's TypeScript types are generated from
these (via the OpenAPI schema), never hand-written."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class PermissionOut(BaseModel):
    key: str
    action_class: Literal["important", "routine", "view"] = Field(alias="class")
    scope: Literal["none", "own", "team", "all"]
    mode: Literal["allow", "needs_approval", "request_only"]
    own_deals_only: bool
    not_own_deals: bool
    reason: Literal["required", "optional", "none"]

    model_config = {"populate_by_name": True}


class TeamBrief(BaseModel):
    """The caller's own team: its name and its Team Leaders' names (a team may have several)."""

    id: str
    name: str
    leaders: list[str]


class MeOut(BaseModel):
    id: str
    name: str
    email: str | None
    role: Literal["user", "team_leader", "admin", "super_admin"]
    team_id: str | None
    team: TeamBrief | None = None
    org_id: str
    last_login_at: datetime | None
    permissions: list[PermissionOut]


class DevUser(BaseModel):
    email: str
    name: str
    role: str


class TokenOut(BaseModel):
    token: str


class ApprovalOut(BaseModel):
    id: str
    type: str
    status: Literal["pending", "approved", "rejected", "cancelled"]
    summary: str | None
    payload: dict[str, Any]
    requested_by: str
    requested_by_name: str | None
    request_reason: str
    routed_to: Literal["team_leader", "admin_pool"]
    decided_by: str | None
    decided_by_name: str | None
    decision_reason: str | None
    created_at: datetime
    decided_at: datetime | None


class HistoryItem(BaseModel):
    id: str
    seq: int
    at: datetime
    actor: str
    actor_role: str
    on_behalf_of: str | None
    action: str
    outcome: Literal["ok", "denied", "approval_requested"]
    old: dict[str, Any] | None
    new: dict[str, Any] | None
    reason: str | None
    approval_id: str | None
    origin: str
    note: str | None


class NotificationOut(BaseModel):
    id: str
    type: str
    title: str
    body: str
    link: str | None
    read_at: datetime | None
    created_at: datetime


class NotificationList(BaseModel):
    unread: int
    items: list[NotificationOut]


class MarkedOut(BaseModel):
    marked: int


class NotificationPreferenceOut(BaseModel):
    type: str
    label: str
    email_enabled: bool
    required: bool


class NotificationPreferencesList(BaseModel):
    items: list[NotificationPreferenceOut]


class NotificationPreferenceIn(BaseModel):
    type: str
    email_enabled: bool


class NotificationPreferencesPatch(BaseModel):
    preferences: list[NotificationPreferenceIn]


class UploadOut(BaseModel):
    file_id: str
    upload_url: str
    expires_in: int
    method: Literal["PUT"]
    headers: dict[str, str]


class FileOut(BaseModel):
    file_id: str
    status: str
    size: int


class LiveEvent(BaseModel):
    id: int
    type: str
    entity_type: str | None
    entity_id: str | None


class FailedJob(BaseModel):
    id: int
    type: str
    attempts: int
    last_error: str | None
    created_at: datetime
