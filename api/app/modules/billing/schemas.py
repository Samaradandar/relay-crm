# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Request and response shapes for billing."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Frequency = Literal["monthly", "quarterly", "yearly"]
TermType = Literal["plan", "contract"]


class GstOut(BaseModel):
    gst_rate_bp: int  # basis points: 1800 = 18%
    gst_rate_percent: str  # exact decimal text, e.g. "18" or "12.5"


class GstPut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gst_rate_bp: int = Field(ge=0, le=10000)
    reason: str | None = None


class SubscriptionOut(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    source_deal_id: uuid.UUID
    status: str
    pricing_model: str
    pack_price_paise: int | None
    pack_size_minutes: Decimal | None
    fee_paise: int | None
    billing_frequency: str | None
    term_type: str | None
    contract_term: str | None
    start_date: date | None
    renewal_date: date | None
    contract_end_date: date | None
    paid_through_date: date | None
    billing_contact_id: uuid.UUID | None
    one_time_fees: list[dict[str, Any]]
    confirmed_by: uuid.UUID | None
    confirmed_at: datetime | None
    version: int
    renewal_status: str
    cancellation_status: str
    cancellation_requested_at: datetime | None
    ended_at: datetime | None
    end_reason: str | None


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pricing_model: Literal["per_minute", "saas"] | None = None
    pack_price_paise: int | None = Field(default=None, ge=0)
    pack_size_minutes: Decimal | None = Field(default=None, gt=Decimal(0))
    fee_paise: int | None = Field(default=None, ge=0)
    billing_frequency: Frequency | None = None
    term_type: TermType | None = None
    contract_term: Frequency | None = None
    start_date: date | None = None
    renewal_date: date | None = None
    contract_end_date: date | None = None
    billing_contact_id: uuid.UUID | None = None
    one_time_fees: list[dict[str, Any]] | None = None
    reason: str | None = None


class SubscriptionPatch(ConfirmIn):
    """Same fields as a confirm; used to adjust an already-confirmed subscription."""


class InvoiceOut(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    subscription_id: uuid.UUID | None
    schedule_id: uuid.UUID | None
    minute_pack_id: uuid.UUID | None
    number: str
    issue_date: date
    due_date: date
    amount_ex_gst_paise: int
    gst_paise: int
    total_paise: int
    file_id: uuid.UUID | None
    version: int
    status: str


class InvoiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schedule_id: uuid.UUID | None = None
    minute_pack_id: uuid.UUID | None = None
    number: str = Field(min_length=1, max_length=100)
    issue_date: date
    due_date: date
    amount_ex_gst_paise: int = Field(ge=0)
    gst_paise: int = Field(ge=0)
    file_id: uuid.UUID | None = None
    reason: str | None = None


class InvoicePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: str | None = Field(default=None, min_length=1, max_length=100)
    issue_date: date | None = None
    due_date: date | None = None
    amount_ex_gst_paise: int | None = Field(default=None, ge=0)
    gst_paise: int | None = Field(default=None, ge=0)
    file_id: uuid.UUID | None = None
    reason: str | None = None


class VoidIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


class AllocationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_id: uuid.UUID
    amount_paise: int = Field(gt=0)


class PaymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount_paise: int = Field(gt=0)
    gst_paise: int = Field(default=0, ge=0)
    received_on: date
    method: str = Field(min_length=1)
    reference: str | None = None
    proof_file_id: uuid.UUID
    allocations: list[AllocationIn] | None = None
    reason: str | None = None


class PaymentOut(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    kind: str
    amount_paise: int
    gst_paise: int
    received_on: date
    method: str
    reference: str | None
    proof_file_id: uuid.UUID
    reverses_payment_id: uuid.UUID | None
    reason: str | None
    recorded_by: uuid.UUID
    created_at: datetime
    unallocated_paise: int | None = None


class ReverseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str


class SubscriptionChangeOut(BaseModel):
    id: uuid.UUID
    subscription_id: uuid.UUID
    source_deal_id: uuid.UUID | None
    change_type: str
    payload: dict[str, Any]
    effective_date: date | None
    status: str
    confirmed_by: uuid.UUID | None
    confirmed_at: datetime | None
    created_at: datetime


class ChangeDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None


class RenewalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["renewed", "not_renewing"]
    reason: str | None = None


class CancellationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["request", "cancel", "revert"]
    end_date: date | None = None
    reason: str | None = None


class SubscriptionLifecycleOut(BaseModel):
    id: uuid.UUID
    customer_id: uuid.UUID
    renewal_status: str
    cancellation_status: str
    cancellation_requested_at: datetime | None
    contract_end_date: date | None
    renewal_date: date | None
    ended_at: datetime | None
    end_reason: str | None


class PackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subscription_id: uuid.UUID | None = None
    size_minutes: Decimal = Field(gt=Decimal(0))
    price_paise: int = Field(ge=0)
    purchased_on: date
    expires_on: date | None = None
    reason: str | None = None
