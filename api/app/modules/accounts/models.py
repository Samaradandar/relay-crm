# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""accounts and contacts (PRD 11 Feature 1, 3)."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Text, event
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from app.core.base import TIMESTAMPTZ, Base, created_at_col, org_fk, uuid_pk
from app.core.trigram import name_grams


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str | None] = mapped_column(Text)  # normalised, lowercase, no www.
    website: Mapped[str | None] = mapped_column(Text)
    industry: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    account_owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id")
    )
    created_at: Mapped[datetime] = created_at_col()
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = created_at_col()
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    # sorted trigrams of the company name (legal-form words dropped): a GIN-indexed prefilter for
    # the duplicate check so it does not scan every account. NULL = not computed (raw SQL insert).
    name_grams: Mapped[list[str] | None] = mapped_column(ARRAY(Text))


@event.listens_for(Account, "before_insert")
@event.listens_for(Account, "before_update")
def _set_name_grams(_mapper: Mapper[Account], _conn: Connection, target: Account) -> None:
    target.name_grams = name_grams(target.name)


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("accounts.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str | None] = mapped_column(Text)
    phone_e164: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    is_decision_maker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = created_at_col()
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = created_at_col()
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
