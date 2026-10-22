# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""files: metadata for objects in the private bucket."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import TIMESTAMPTZ, Base, created_at_col, org_fk, uuid_pk


class FileRecord(Base):
    __tablename__ = "files"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    owner_entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    owner_entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = created_at_col()
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
