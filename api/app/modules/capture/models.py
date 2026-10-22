# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""SQLAlchemy tables owned by this module: website/email/CSV capture sources, every capture
attempt and its outcome, the capture review queue, and CSV import jobs.

Built in: Phase 7.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import TIMESTAMPTZ, Base, org_fk, uuid_pk


def _uuid(fk: str | None = None, *, nullable: bool = True) -> Mapped[Any]:
    col = UUID(as_uuid=True)
    if fk:
        return mapped_column(col, ForeignKey(fk), nullable=nullable)
    return mapped_column(col, nullable=nullable)


class CaptureSource(Base):
    __tablename__ = "capture_sources"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    source_key: Mapped[str | None] = mapped_column(Text, unique=True)
    allowed_origins: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    default_next_step: Mapped[str | None] = mapped_column(Text)
    default_lead_source: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(nullable=False, default=True)
    created_by: Mapped[uuid.UUID | None] = _uuid("users.id")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)


class CaptureEvent(Base):
    __tablename__ = "capture_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    source_id: Mapped[uuid.UUID | None] = _uuid("capture_sources.id")
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(Text)
    payload_s3_key: Mapped[str | None] = mapped_column(Text)
    payload_preview: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    deal_id: Mapped[uuid.UUID | None] = _uuid("deals.id")
    matched_account_id: Mapped[uuid.UUID | None] = _uuid("accounts.id")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)


class CaptureReview(Base):
    __tablename__ = "capture_reviews"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    capture_event_id: Mapped[uuid.UUID] = _uuid("capture_events.id", nullable=False)
    matched_deal_id: Mapped[uuid.UUID | None] = _uuid("deals.id")
    routed_team_id: Mapped[uuid.UUID | None] = _uuid("teams.id")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    decided_by: Mapped[uuid.UUID | None] = _uuid("users.id")
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)


class CsvImport(Base):
    __tablename__ = "csv_imports"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    file_s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    mapping: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    default_team_id: Mapped[uuid.UUID | None] = _uuid("teams.id")
    status: Mapped[str] = mapped_column(Text, nullable=False, default="uploaded")
    counts: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID] = _uuid("users.id", nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)


class CsvImportRow(Base):
    __tablename__ = "csv_import_rows"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    import_id: Mapped[uuid.UUID] = _uuid("csv_imports.id", nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    outcome: Mapped[str | None] = mapped_column(Text)
    deal_id: Mapped[uuid.UUID | None] = _uuid("deals.id")
    errors: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)


class InboundEmail(Base):
    __tablename__ = "inbound_emails"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    message_id: Mapped[str] = mapped_column(Text, nullable=False)
    from_email: Mapped[str] = mapped_column(Text, nullable=False)
    from_name: Mapped[str | None] = mapped_column(Text)
    subject: Mapped[str | None] = mapped_column(Text)
    s3_key: Mapped[str | None] = mapped_column(Text)
    spam_verdict: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    deal_id: Mapped[uuid.UUID | None] = _uuid("deals.id")
    forwarded_by_user_id: Mapped[uuid.UUID | None] = _uuid("users.id")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
