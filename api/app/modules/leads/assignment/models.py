# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Assignment rules, rep groups, round-robin pointers, caps and settings."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import TIMESTAMPTZ, Base, created_at_col, org_fk, uuid_pk


class AssignmentRule(Base):
    __tablename__ = "assignment_rules"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    match: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    target_type: Mapped[str] = mapped_column(Text, nullable=False)  # team | group | user
    target_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = created_at_col()
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)


class RepGroup(Base):
    __tablename__ = "rep_groups"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    team_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("teams.id"))
    created_at: Mapped[datetime] = created_at_col()
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)


class RepGroupMember(Base):
    __tablename__ = "rep_group_members"

    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("rep_groups.id"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )


class RrPointer(Base):
    __tablename__ = "rr_pointers"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id"), primary_key=True
    )
    scope_type: Mapped[str] = mapped_column(Text, primary_key=True)  # group | team
    scope_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    last_assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = created_at_col()


class RepCap(Base):
    __tablename__ = "rep_caps"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    org_id: Mapped[uuid.UUID] = org_fk()
    cap: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = created_at_col()


class AssignmentSettings(Base):
    __tablename__ = "assignment_settings"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id"), primary_key=True
    )
    default_team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("teams.id")
    )
    default_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=25)
    # minutes a rep-created lead waits for its Team Leader before automatic round robin
    rep_lead_hold_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=20)
    updated_at: Mapped[datetime] = created_at_col()
