# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""approval_requests (PRD 4.5)."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import TIMESTAMPTZ, Base, created_at_col, org_fk, uuid_pk


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    type: Mapped[str] = mapped_column(Text, nullable=False)
    action_key: Mapped[str] = mapped_column(Text, nullable=False)
    target_entity_type: Mapped[str | None] = mapped_column(Text)
    target_entity_id: Mapped[str | None] = mapped_column(Text)
    record_owner_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    requested_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    request_reason: Mapped[str] = mapped_column(Text, nullable=False)
    routed_to: Mapped[str] = mapped_column(Text, nullable=False)
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
