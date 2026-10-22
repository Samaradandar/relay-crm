# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""SQLAlchemy tables owned by this module: a churned customer's record, and the org's editable
list of churn reasons (Settings list, same shape as pipeline.config's LostReason/FitItem).

Money is integer paise, matching billing. Built in: Phase 5 (schema only in this PR; recording,
reversal and alert logic are later PRs).
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import TIMESTAMPTZ, Base, created_at_col, org_fk, uuid_pk


def _uuid(fk: str | None = None, *, nullable: bool = True) -> Mapped[Any]:
    col = UUID(as_uuid=True)
    if fk:
        return mapped_column(col, ForeignKey(fk), nullable=nullable)
    return mapped_column(col, nullable=nullable)


class ChurnEvent(Base):
    """A customer's subscription ended. Append-mostly: `reversed_at`/`reversed_by` mark a later
    win-back without deleting the record."""

    __tablename__ = "churn_events"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    customer_id: Mapped[uuid.UUID] = _uuid("customers.id", nullable=False)
    churn_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    monthly_value_lost_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    subscription_start: Mapped[date] = mapped_column(Date, nullable=False)
    time_as_customer_days: Mapped[int] = mapped_column(Integer, nullable=False)
    last_renewal_date: Mapped[date | None] = mapped_column(Date)
    recorded_by: Mapped[uuid.UUID] = _uuid("users.id", nullable=False)
    recorded_at: Mapped[datetime] = created_at_col()
    returned_deal_id: Mapped[uuid.UUID | None] = _uuid("deals.id")
    reversed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    reversed_by: Mapped[uuid.UUID | None] = _uuid("users.id")


class ChurnReason(Base):
    """The org's editable list of churn reasons (Settings). Never erased: `active=False` hides
    it from new entries while past churn_events keep pointing at the label."""

    __tablename__ = "churn_reasons"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    label: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
