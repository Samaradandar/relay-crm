# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Request and response shapes for activities."""

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ActivityType = Literal["call", "email", "meeting", "discovery", "walkthrough", "kickoff", "note"]


class ActivityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ActivityType
    occurred_at: datetime | None = None  # defaults to now
    two_way: bool | None = None  # required for calls, emails and meetings
    attendees: list[str] = Field(default_factory=list)
    discussed: str = Field(min_length=1)
    requirements: str | None = None
    objections: str | None = None
    decisions: str | None = None
    actions: str | None = None
    # what happens next (always required: a deal never has no next step)
    next_step_text: str = Field(min_length=1, max_length=500)
    next_step_due_date: date
    next_step_responsible_id: uuid.UUID | None = None
    next_step_lane: Literal["your_move", "waiting_on_them"] = "your_move"
    # Phase 8 (side track): "Booking a meeting" ticks. Ignored for every other activity type.
    send_invite: bool = False
    add_meet_link: bool = False
    reason: str | None = None


class ActivityPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    occurred_at: datetime | None = None
    two_way: bool | None = None
    attendees: list[str] | None = None
    send_invite: bool | None = None
    add_meet_link: bool | None = None
    discussed: str | None = Field(default=None, min_length=1)
    requirements: str | None = None
    objections: str | None = None
    decisions: str | None = None
    actions: str | None = None
    reason: str | None = None


class ActivityOut(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID
    type: str
    occurred_at: datetime
    two_way: bool
    attendees: list[Any]
    discussed: str | None
    requirements: str | None
    objections: str | None
    decisions: str | None
    actions: str | None
    version_no: int
    created_by: uuid.UUID
    created_by_name: str | None
    attributed_to_name: str | None
    created_at: datetime
    edited: bool
    deleted: bool = False
    # Phase 8 (side track): null when the owner isn't connected or this isn't a meeting.
    calendar: dict[str, Any] | None = None
    # meetings only: booked / held / postponed / cancelled / no_show, and why (null otherwise)
    meeting_status: str | None = None
    status_reason_code: str | None = None
    status_reason: str | None = None
    status_at: datetime | None = None
    status_by_name: str | None = None
    rescheduled_from: uuid.UUID | None = None


class ActivityVersionOut(BaseModel):
    version_no: int
    body: dict[str, Any]
    edited_by_name: str | None
    edited_at: datetime
    reason: str | None


class MeetingHeldIn(BaseModel):
    """The meeting happened: who came and what was discussed. A deal is never left without a next
    step, so one comes with it, exactly as when logging any activity."""

    model_config = ConfigDict(extra="forbid")

    occurred_at: datetime | None = None  # when it really happened; default: the booked time
    attendees: list[str] | None = None  # omit to keep the booked attendees
    discussed: str = Field(min_length=1)
    requirements: str | None = None
    objections: str | None = None
    decisions: str | None = None
    actions: str | None = None
    next_step_text: str = Field(min_length=1, max_length=500)
    next_step_due_date: date
    next_step_responsible_id: uuid.UUID | None = None
    next_step_lane: Literal["your_move", "waiting_on_them"] = "your_move"
    reason: str | None = None


class MeetingPostponeIn(BaseModel):
    """The date moved: a new booking replaces this one, and the reason is kept on both."""

    model_config = ConfigDict(extra="forbid")

    new_at: datetime
    reason_code: Literal["customer_asked", "we_asked", "customer_unavailable", "other"]
    why: str = Field(min_length=3, max_length=1000)
    attendees: list[str] | None = None  # omit to keep the booked attendees
    reason: str | None = None


class MeetingCancelIn(BaseModel):
    """It will not happen (or the customer did not turn up): the reason is kept, and the lead
    needs a new meeting before it can move on."""

    model_config = ConfigDict(extra="forbid")

    reason_code: Literal["customer_cancelled", "we_cancelled", "no_show", "other"]
    why: str = Field(min_length=3, max_length=1000)
    reason: str | None = None
