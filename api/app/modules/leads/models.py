# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""deals (minimal, Phase 3 adds the pipeline), next steps, Lead Pool entries, transfers and
duplicate overrides."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import TIMESTAMPTZ, Base, created_at_col, org_fk, uuid_pk

LEAD_SOURCES = (
    "Website",
    "Referral",
    "LinkedIn",
    "Event",
    "Cold outbound",
    "Inbound call",
    "Inbound email",
)
# used only by Deal.lead_source on a Phase 5 request-creation deal (pipeline="existing_customer");
# it never came from a lead source, but the column is NOT NULL and checked (0002/0010).
EXISTING_CUSTOMER_SOURCE = "Existing customer"


class Deal(Base):
    __tablename__ = "deals"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    primary_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contacts.id")
    )
    pipeline: Mapped[str] = mapped_column(Text, nullable=False, default="new_business")
    # only meaningful when pipeline == "existing_customer": upsell/top_up, renewal, plan_change,
    # other_product, increase, decrease (Phase 5 PRD data model section), plus "return" (P5-06:
    # a churned customer coming back -- "other product / return" in the PRD)
    request_type: Mapped[str | None] = mapped_column(Text)
    stage_key: Mapped[str] = mapped_column(Text, nullable=False, default="lead")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pooled")
    owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"))
    lead_source: Mapped[str] = mapped_column(Text, nullable=False)
    generated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # system-locked
    generated_source: Mapped[str | None] = mapped_column(Text)  # e.g. "website form" (Phase 7)
    first_contact_date: Mapped[date | None] = mapped_column(Date)
    assigned_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    assignment_method: Mapped[str | None] = mapped_column(Text)
    assignment_rule_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # null = System
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = created_at_col()
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = created_at_col()
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    deleted_reason: Mapped[str | None] = mapped_column(Text)
    # ---- Phase 3: pipeline progress
    pipeline_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    stage_entered_at: Mapped[datetime] = created_at_col()
    revision_round: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    won_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    lost_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    # ---- Phase 6: Won-credit snapshot, set once at Won and never moved by later reassignment.
    won_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    won_team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id")
    )
    won_value_paise: Mapped[int | None] = mapped_column(BigInteger)
    lost_reason: Mapped[str | None] = mapped_column(Text)
    lost_notes: Mapped[str | None] = mapped_column(Text)
    lost_from_stage_key: Mapped[str | None] = mapped_column(Text)
    expected_close_date: Mapped[date | None] = mapped_column(Date)
    expected_close_reason: Mapped[str | None] = mapped_column(Text)
    value_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    pricing_model: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[str | None] = mapped_column(Text)
    expected_minutes_per_month: Mapped[int | None] = mapped_column(Integer)
    use_case: Mapped[str | None] = mapped_column(Text)
    pain_point: Mapped[str | None] = mapped_column(Text)
    fit_checklist: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    last_activity_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    health: Mapped[str] = mapped_column(Text, nullable=False, default="on_track")
    flags: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # ---- Phase 5 (existing_customer pipeline only): Closure's "new plan and effective date
    # recorded" criterion. {"change_type": ..., "new_plan_values": {...}, "effective_date": ...}
    closure_change: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # ---- Phase 7: how a captured lead arrived (null = a rep entered it by hand).
    source_kind: Mapped[str | None] = mapped_column(Text)
    capture_source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class NextStep(Base):
    __tablename__ = "next_steps"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    responsible_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    done_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    created_at: Mapped[datetime] = created_at_col()
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    lane: Mapped[str] = mapped_column(Text, nullable=False, default="your_move")
    lane_since: Mapped[datetime] = created_at_col()
    snoozed_until: Mapped[date | None] = mapped_column(Date)
    snooze_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # written by the system from the pipeline (a stage move may replace it), not by a person
    auto_generated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class LeadPoolEntry(Base):
    __tablename__ = "lead_pool_entries"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id"), nullable=False
    )
    entered_at: Mapped[datetime] = created_at_col()
    entered_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    entered_source: Mapped[str] = mapped_column(Text, nullable=False, default="manual entry")
    routed_team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    waiting_reason: Mapped[str | None] = mapped_column(Text)
    tl_notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    manual_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # a lead a sales rep created: held for the Team Leader's decision until the hold window ends
    rep_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    left_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    left_reason: Mapped[str | None] = mapped_column(Text)


class TransferRequest(Base):
    __tablename__ = "transfer_requests"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id"), nullable=False
    )
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    from_team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    to_team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    from_owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    proposed_owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    other_tl_comment: Mapped[str | None] = mapped_column(Text)
    other_tl_comment_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    approval_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    created_at: Mapped[datetime] = created_at_col()
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)


class DuplicateOverride(Base):
    __tablename__ = "duplicate_overrides"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id"), nullable=False
    )
    match_type: Mapped[str] = mapped_column(Text, nullable=False)
    matched_account_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    in_scope: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    by_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()
