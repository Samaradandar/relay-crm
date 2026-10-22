# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""SQLAlchemy declarative base and column helpers shared by every module."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

TIMESTAMPTZ = DateTime(timezone=True)


class Base(DeclarativeBase):
    pass


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def org_fk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), ForeignKey("orgs.id"), nullable=False)


def created_at_col() -> Mapped[datetime]:
    return mapped_column(TIMESTAMPTZ, nullable=False, server_default=func.now())
