# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Usage received from the voice platform, the unmatched queue and the sync state. Phase 4."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import TIMESTAMPTZ, Base, created_at_col, org_fk, uuid_pk

MINUTES = Numeric(14, 3)


class UsageRecord(Base):
    __tablename__ = "usage_records"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("customers.id"), nullable=False
    )
    source_record_id: Mapped[str] = mapped_column(Text, nullable=False)  # unique per org
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    minutes: Mapped[Decimal] = mapped_column(MINUTES, nullable=False)
    received_at: Mapped[datetime] = created_at_col()
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class UnmatchedUsage(Base):
    """Usage whose voice customer id maps to no customer. Queued for Admins, never dropped."""

    __tablename__ = "unmatched_usage"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    source_record_id: Mapped[str] = mapped_column(Text, nullable=False)
    voice_customer_id: Mapped[str] = mapped_column(Text, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    minutes: Mapped[Decimal] = mapped_column(MINUTES, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    received_at: Mapped[datetime] = created_at_col()
    resolved_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)


class UsageSyncState(Base):
    __tablename__ = "usage_sync_state"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id"), primary_key=True
    )
    cursor: Mapped[str | None] = mapped_column(Text)
    last_success_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    last_attempt_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = created_at_col()
