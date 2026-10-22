# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""A deal's journey: one row per stage visit (with the checklist copied at entry) and one row
per ticked criterion."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import TIMESTAMPTZ, Base, created_at_col, org_fk, uuid_pk


class DealStageEntry(Base):
    __tablename__ = "deal_stage_entries"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id"), nullable=False
    )
    stage_key: Mapped[str] = mapped_column(Text, nullable=False)
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pipeline_versions.id"), nullable=False
    )
    revision_round: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    entered_at: Mapped[datetime] = created_at_col()
    entered_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    exited_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    exit_kind: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    checklist_snapshot: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )


class DealCriterion(Base):
    __tablename__ = "deal_criteria"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id"), nullable=False
    )
    entry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deal_stage_entries.id"), nullable=False
    )
    criterion_key: Mapped[str] = mapped_column(Text, nullable=False)
    ticked_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    ticked_at: Mapped[datetime] = created_at_col()
    proof_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    verify_status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    verified_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    verified_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    verify_reason: Mapped[str | None] = mapped_column(Text)
    proof_missing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
