# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Pydantic request and response shapes for churn recording and reversal (P5-06).

Built in: Phase 5.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.core.fields import Opt2000, Text500


class ChurnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    churn_date: date
    reason_code: Text500
    # Free-text note. Also doubles as the mandatory `reason` the permission policy requires for
    # `renewal_cancellation_churn.record` (ALWAYS_REQUIRED_KEYS) -- there is no separate field.
    notes: Opt2000 = None


class ReverseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Opt2000 = None


class ChurnOut(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    churn_date: date
    reason: str
    notes: str | None
    monthly_value_lost_paise: int
    subscription_start: date
    time_as_customer_days: int
    last_renewal_date: date | None
    recorded_by: uuid.UUID
    recorded_at: datetime
    returned_deal_id: uuid.UUID | None
    reversed_at: datetime | None
    reversed_by: uuid.UUID | None


# ---------------------------------------------------------------------------- Churn History (P5-07)
class ChurnHistoryRow(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    account_id: uuid.UUID
    account_name: str
    account_owner_name: str | None
    churn_date: date
    reason: str
    notes: str | None
    monthly_value_lost_paise: int
    subscription_start: date
    time_as_customer_days: int
    last_renewal_date: date | None
    recorded_by: uuid.UUID
    recorded_by_name: str | None
    recorded_at: datetime
    returned_deal_id: uuid.UUID | None
    reversed_at: datetime | None
    returned: bool


class ChurnHistoryPageOut(BaseModel):
    total: int
    page: int
    page_size: int
    rows: list[ChurnHistoryRow]


class ChurnReasonCount(BaseModel):
    reason: str
    count: int


class ChurnRepBreakdown(BaseModel):
    owner_id: uuid.UUID | None
    owner_name: str | None
    count: int
    value_lost_paise: int


class ChurnMonthBreakdown(BaseModel):
    month: date
    count: int
    value_lost_paise: int


class ChurnHistorySummaryOut(BaseModel):
    churned_this_month: int
    churned_this_quarter: int
    churned_all_time: int
    churn_rate_this_month: Decimal | None
    total_monthly_value_lost_paise: int
    top_reasons: list[ChurnReasonCount]
    by_reason: list[ChurnReasonCount] | None = None
    by_rep: list[ChurnRepBreakdown] | None = None
    by_month: list[ChurnMonthBreakdown] | None = None
    teams_compare_available: bool


# ---------------------------------------------------------------- Churn reasons settings (P5-09)
class ChurnReasonOut(BaseModel):
    id: uuid.UUID
    label: str
    active: bool
    sort_order: int


class ChurnReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID | None = None
    label: Text500
    active: bool = True
    sort_order: int = 0


class ChurnReasonsPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ChurnReasonIn]
    reason: Opt2000 = None
