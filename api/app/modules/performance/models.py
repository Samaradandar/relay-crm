# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""SQLAlchemy tables owned by this module: the leaderboard/dashboard/reports read model
(rep_month_stats, never computed from raw deals per request), targets, incentives (private --
served only from the performance module's own endpoints, see P6-06) and the daily-active-use
tracker.

Built in: Phase 6. See Relay CRM Build Plan.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import TIMESTAMPTZ, Base, org_fk, uuid_pk


def _uuid(fk: str | None = None, *, nullable: bool = True) -> Mapped[Any]:
    col = UUID(as_uuid=True)
    if fk:
        return mapped_column(col, ForeignKey(fk), nullable=nullable)
    return mapped_column(col, nullable=nullable)


class RepMonthStats(Base):
    """One row per rep per calendar month (IST). The only table the leaderboard, personal
    dashboard and reports read money/rank figures from."""

    __tablename__ = "rep_month_stats"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID] = _uuid("users.id", nullable=False)
    month: Mapped[date] = mapped_column(nullable=False)
    team_id: Mapped[uuid.UUID | None] = _uuid("teams.id")
    active_deals: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    won_deals: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lost_deals: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revenue_current_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    revenue_projected_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    best_to_date_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    target_paise: Mapped[int | None] = mapped_column(BigInteger)
    rank: Mapped[int | None] = mapped_column(Integer)
    is_closed: Mapped[bool] = mapped_column(nullable=False, default=False)
    computed_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)


class Target(Base):
    """A rep's target for one month, set by their Team Leader (or Admin/SA with reason).
    One row per (user, month); the full change history lives in the audit trail."""

    __tablename__ = "targets"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID] = _uuid("users.id", nullable=False)
    month: Mapped[date] = mapped_column(nullable=False)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    set_by: Mapped[uuid.UUID] = _uuid("users.id", nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)


class Incentive(Base):
    """A deal's private incentive: X% of that deal's value, never a flat rate. Frozen at
    won_value_paise once the deal is fully paid. Never served by any serializer other than this
    module's own.

    Leaderboard & Incentive module (approved 6 Oct 2026, supersedes P6-05/P6-06's "TL sets
    directly"): a TL can only request (on a Won deal) or pledge (on an Active deal); only
    Admin/SA approves or sets directly -- see `IncentiveRequest` and `source`/`is_pledge` below.
    `status`: no_rate | pending_approval | approved_pending_closure | approved | earned."""

    __tablename__ = "incentives"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    deal_id: Mapped[uuid.UUID] = _uuid("deals.id", nullable=False)
    user_id: Mapped[uuid.UUID] = _uuid("users.id", nullable=False)
    rate_bp: Mapped[int | None] = mapped_column(Integer)
    won_value_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    # Snapshot of the open deal's value for a pledge's "if won" ₹ preview -- won_value_paise is
    # only set once the deal is actually Won.
    pledge_value_paise: Mapped[int | None] = mapped_column(BigInteger)
    received_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    pending_amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    earned_amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="no_rate")
    is_pledge: Mapped[bool] = mapped_column(nullable=False, default=False)
    source: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    set_by: Mapped[uuid.UUID | None] = _uuid("users.id")
    earned_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    updated_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)


class IncentiveRequest(Base):
    """A Team Leader's request (on a Won deal) or pledge (on an Active deal); kept separate from
    the live `Incentive` record so the approval history stays clean. Routed to the Admin pool,
    never to the requester's own TL -- only Admin/SA decide."""

    __tablename__ = "incentive_requests"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    deal_id: Mapped[uuid.UUID] = _uuid("deals.id", nullable=False)
    requested_by: Mapped[uuid.UUID] = _uuid("users.id", nullable=False)
    request_type: Mapped[str] = mapped_column(Text, nullable=False)
    rate_bp: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    decided_by: Mapped[uuid.UUID | None] = _uuid("users.id")
    decision_reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)


class UserActivityDay(Base):
    """One row per user per IST day, written on first authenticated request that day. Backs
    the 'daily active use by the sales team' measure (PRD section 12)."""

    __tablename__ = "user_activity_days"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    user_id: Mapped[uuid.UUID] = _uuid("users.id", nullable=False)
    day: Mapped[date] = mapped_column(nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
