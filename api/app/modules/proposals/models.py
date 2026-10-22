# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Proposal template, proposals (versioned per deal) and their line items. Phase 4."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import TIMESTAMPTZ, Base, created_at_col, org_fk, uuid_pk


class ProposalTemplate(Base):
    """Every save is a new version row; older versions stay for the proposals built on them."""

    __tablename__ = "proposal_templates"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    terms_html: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = created_at_col()
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class Proposal(Base):
    __tablename__ = "proposals"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    template_version: Mapped[int | None] = mapped_column(Integer)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    # draft|sent|accepted|changes_requested|rejected|delivery_failed
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    pdf_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("files.id")
    )
    sent_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    sent_to: Mapped[str | None] = mapped_column(Text)
    email_message_id: Mapped[str | None] = mapped_column(Text)
    feedback: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = created_at_col()
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)


class ProposalItem(Base):
    __tablename__ = "proposal_items"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    proposal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("proposals.id"), nullable=False
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id")
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    discount_bp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    line_total_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ProposalRound(Base):
    """One answer from the customer about the proposal. Changes requested or objections close the
    round (a new meeting revises the proposal, then the next round); an accepted round stays open
    until the proposal has been sent. Every round is kept."""

    __tablename__ = "proposal_rounds"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id"), nullable=False
    )
    round_no: Mapped[int] = mapped_column(Integer, nullable=False)
    meeting_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("activities.id")
    )
    feedback: Mapped[str] = mapped_column(Text, nullable=False)
    changes: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    recorded_at: Mapped[datetime] = created_at_col()
    closed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
