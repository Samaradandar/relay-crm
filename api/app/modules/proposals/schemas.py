# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Request and response shapes for proposals and their template."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["draft", "sent", "accepted", "changes_requested", "rejected", "delivery_failed"]


class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
    terms_html: str = ""
    reason: str | None = None


class TemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    body: str
    terms_html: str
    version: int
    active: bool


class ProposalItemOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID | None
    description: str
    quantity: int
    unit_price_paise: int
    discount_bp: int
    line_total_paise: int


class ProposalOut(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID
    version_no: int
    template_version: int | None
    content: dict[str, Any]
    status: str
    pdf_file_id: uuid.UUID | None
    sent_at: datetime | None
    sent_to: str | None
    feedback: str | None
    items: list[ProposalItemOut]


class ProposalPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: dict[str, Any] | None = None
    reason: str | None = None


class SendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str | None = None
    to_name: str | None = Field(default=None, max_length=200)  # who it is addressed to
    reason: str | None = None


class StatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Status
    feedback: str | None = None
    reason: str | None = None


class DraftLine(BaseModel):
    """One product line of the proposal: what it is and its price per unit."""

    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID | None = None
    description: str = Field(min_length=1, max_length=500)
    quantity: int = Field(ge=1, le=100_000_000)
    unit_price_paise: int = Field(ge=0)


class DraftIn(BaseModel):
    """The terms the customer accepted. They give the proposal its structure; the draft is built
    from them (owner decision, 20 Oct 2026)."""

    model_config = ConfigDict(extra="forbid")

    lines: list[DraftLine] = Field(min_length=1, max_length=50)
    business_model: str = Field(min_length=1, max_length=100)
    billing_frequency: str = Field(min_length=1, max_length=50)
    min_committed_minutes: int | None = Field(default=None, ge=0)  # per month
    concurrency: int = Field(ge=1, le=100_000)  # calls at the same time
    tax_percent: float = Field(ge=0, le=100)
    terms_and_conditions: str = Field(min_length=3, max_length=20_000)
    scope_of_work: str = Field(min_length=3, max_length=20_000)
    company_name: str = Field(min_length=1, max_length=300)
    company_details: str | None = Field(default=None, max_length=4000)


class PrefillLine(BaseModel):
    product_id: uuid.UUID
    description: str
    quantity: int
    unit_price_paise: int


class PrefillOut(BaseModel):
    company_name: str
    business_model: str | None
    min_committed_minutes: int | None
    scope_of_work: str | None
    lines: list[PrefillLine]
    last_terms: dict[str, Any] | None


class FeedbackIn(BaseModel):
    """The customer's answer after the proposal walkthrough meeting. A rejection is not recorded
    here: it is a lost deal (Mark lost, with the reason)."""

    model_config = ConfigDict(extra="forbid")

    feedback: Literal["accepted", "changes_requested", "objections"]
    changes: str | None = Field(default=None, max_length=4000)  # required unless accepted
    # changes or objections: when the meeting to revise the proposal is booked for (required then)
    next_meeting_at: datetime | None = None
    reason: str | None = None


class RoundMeetingOut(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    attendees: list[Any]
    discussed: str | None


class RoundOut(BaseModel):
    id: uuid.UUID
    round_no: int
    feedback: str
    changes: str | None
    recorded_by_name: str | None
    recorded_at: datetime
    closed_at: datetime | None
    meeting: RoundMeetingOut | None


class RoundsOut(BaseModel):
    rounds: list[RoundOut]
    # the round being worked on now: its number, the held meeting that counts for it (the
    # walkthrough, or the meeting to revise the proposal) and whether the customer accepted
    current_round_no: int
    held_meeting: RoundMeetingOut | None
    booked_meeting: RoundMeetingOut | None
    accepted: bool
