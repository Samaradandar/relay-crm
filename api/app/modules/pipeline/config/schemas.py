# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Request and response shapes for pipeline configuration and the settings lists."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

# Trimmed, non-empty text: whitespace-only is rejected, not stored as an empty string.
Text100 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Text200 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Text300 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]

Slug = Field(pattern=r"^[a-z][a-z0-9_]{0,48}$")


class StageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Slug
    name: Text100
    probability_pct: int = Field(ge=0, le=100)
    is_entry: bool = False
    is_final: bool = False
    won_on_entry: bool = False
    is_conditional: bool = False
    display_group: str | None = None
    entry_checks: list[dict[str, Any]] = Field(default_factory=list)


class TransitionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_stage_key: str
    to_stage_key: str
    kind: Literal["forward", "backward", "conditional"]


class CriterionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage_key: str
    key: str = Slug
    label: Text300
    help_text: str | None = None
    proof_type: str
    proof_config: dict[str, Any] = Field(default_factory=dict)


class DraftIn(BaseModel):
    """Create or update the draft. Send `from_version_no` alone to start a draft as a copy of an
    earlier version, or send the whole definition (stages, transitions, criteria) to replace it."""

    model_config = ConfigDict(extra="forbid")

    notes: str | None = None
    from_version_no: int | None = None
    stages: list[StageIn] | None = None
    transitions: list[TransitionIn] | None = None
    criteria: list[CriterionIn] | None = None
    reason: str | None = None


class PublishIn(BaseModel):
    # removed stage key -> stage key (in the draft) that its open deals move to
    stage_mapping: dict[str, str] = Field(default_factory=dict)
    reason: str | None = None


class RollbackIn(PublishIn):
    pass


class StageOut(StageIn):
    id: uuid.UUID
    position: int


class CriterionOut(CriterionIn):
    id: uuid.UUID
    position: int


class VersionSummary(BaseModel):
    id: uuid.UUID
    version_no: int
    status: str
    notes: str | None
    created_at: datetime
    published_at: datetime | None
    published_by_name: str | None = None
    diff_summary: dict[str, Any] | None


class VersionOut(VersionSummary):
    pipeline_key: str
    stages: list[StageOut]
    transitions: list[TransitionIn]
    criteria: list[CriterionOut]


class Problem(BaseModel):
    code: str
    message: str
    stage_key: str | None = None


class ValidationOut(BaseModel):
    ok: bool
    problems: list[Problem]


class ImpactStage(BaseModel):
    key: str
    name: str
    open_deals: int
    in_draft: bool


class ImpactOut(BaseModel):
    stages: list[ImpactStage]
    needs_mapping: list[str]  # removed stages that still hold deals


class CustomFieldIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID | None = None
    entity: Literal["deal", "contact", "account"]
    key: str = Slug
    label: Text100
    type: Literal["text", "number", "date", "dropdown", "yes_no", "file", "contact"]
    options: list[str] = Field(default_factory=list)
    active: bool = True


class CustomFieldOut(CustomFieldIn):
    id: uuid.UUID


class CustomFieldsPut(BaseModel):
    fields: list[CustomFieldIn]
    reason: str | None = None


class ListItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID | None = None
    label: Text200


class ListItemOut(BaseModel):
    id: uuid.UUID
    label: str
    position: int
    active: bool


class ListPut(BaseModel):
    items: list[ListItemIn]
    reason: str | None = None
