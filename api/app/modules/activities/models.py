# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Logged interactions with a customer. Every edit keeps the earlier version."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import TIMESTAMPTZ, Base, created_at_col, org_fk, uuid_pk

ACTIVITY_TYPES = ("call", "email", "meeting", "discovery", "walkthrough", "kickoff", "note")
TWO_WAY_TYPES = ("call", "email", "meeting")  # the customer must have responded

# A meeting's life: booked (a date the customer gave, locked) -> held (it happened, notes logged),
# or postponed (replaced by a new booking), cancelled, no_show. Other activity types have none.
MEETING_STATUSES = ("booked", "held", "postponed", "cancelled", "no_show")
DEAD_MEETING_STATUSES = ("postponed", "cancelled", "no_show")  # it did not (and will not) happen
# Why a booked meeting did not go ahead, picked from a list and explained in words.
POSTPONE_REASONS = ("customer_asked", "we_asked", "customer_unavailable", "other")
CANCEL_REASONS = ("customer_cancelled", "we_cancelled", "no_show", "other")


class Activity(Base):
    __tablename__ = "activities"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id"), nullable=False
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    two_way: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attendees: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    discussed: Mapped[str | None] = mapped_column(Text)
    requirements: Mapped[str | None] = mapped_column(Text)
    objections: Mapped[str | None] = mapped_column(Text)
    decisions: Mapped[str | None] = mapped_column(Text)
    actions: Mapped[str | None] = mapped_column(Text)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    attributed_to: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    # ---- Phase 8 (side track): "Booking a meeting" ticks, and the Meet link once created.
    send_invite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    add_meet_link: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    meet_link: Mapped[str | None] = mapped_column(Text)
    # ---- meetings only: what became of it, and why (0028)
    meeting_status: Mapped[str | None] = mapped_column(Text)
    status_reason_code: Mapped[str | None] = mapped_column(Text)
    status_reason: Mapped[str | None] = mapped_column(Text)
    status_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    status_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    # a postponed meeting's replacement points back at the booking it replaces
    rescheduled_from: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("activities.id")
    )
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = created_at_col()
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    deleted_reason: Mapped[str | None] = mapped_column(Text)


class ActivityVersion(Base):
    __tablename__ = "activity_versions"

    activity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("activities.id"), primary_key=True
    )
    version_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    body: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    edited_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    edited_at: Mapped[datetime] = created_at_col()
    reason: Mapped[str | None] = mapped_column(Text)
