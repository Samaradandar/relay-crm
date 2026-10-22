# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Billing tables: settings, subscriptions, schedule, invoices, payments, packs, ledger, overage.

Money is integer paise, minutes are Decimal to 3 places, percentages are basis points.
Built in: Phase 4.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import TIMESTAMPTZ, Base, created_at_col, org_fk, uuid_pk

MINUTES = Numeric(14, 3)


def _uuid(fk: str | None = None, *, nullable: bool = True) -> Mapped[Any]:
    col = UUID(as_uuid=True)
    if fk:
        return mapped_column(col, ForeignKey(fk), nullable=nullable)
    return mapped_column(col, nullable=nullable)


class BillingSettings(Base):
    __tablename__ = "billing_settings"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id"), primary_key=True
    )
    gst_rate_bp: Mapped[int] = mapped_column(Integer, nullable=False, default=1800)
    updated_at: Mapped[datetime] = created_at_col()
    updated_by: Mapped[uuid.UUID | None] = _uuid()


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    customer_id: Mapped[uuid.UUID] = _uuid("customers.id", nullable=False)
    source_deal_id: Mapped[uuid.UUID] = _uuid("deals.id", nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="draft")
    pricing_model: Mapped[str] = mapped_column(Text, nullable=False)
    pack_price_paise: Mapped[int | None] = mapped_column(BigInteger)
    pack_size_minutes: Mapped[Decimal | None] = mapped_column(MINUTES)
    fee_paise: Mapped[int | None] = mapped_column(BigInteger)
    billing_frequency: Mapped[str | None] = mapped_column(Text)
    term_type: Mapped[str | None] = mapped_column(Text)
    contract_term: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    renewal_date: Mapped[date | None] = mapped_column(Date)
    contract_end_date: Mapped[date | None] = mapped_column(Date)
    paid_through_date: Mapped[date | None] = mapped_column(Date)
    billing_contact_id: Mapped[uuid.UUID | None] = _uuid("contacts.id")
    one_time_fees: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    confirmed_by: Mapped[uuid.UUID | None] = _uuid("users.id")
    confirmed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # ---- Phase 5: renewal / cancellation (existing customer pipeline)
    renewal_status: Mapped[str] = mapped_column(Text, nullable=False, default="upcoming")
    cancellation_status: Mapped[str] = mapped_column(Text, nullable=False, default="none")
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    ended_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    end_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()
    created_by: Mapped[uuid.UUID | None] = _uuid()
    updated_at: Mapped[datetime] = created_at_col()
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)


class SubscriptionVersion(Base):
    __tablename__ = "subscription_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    subscription_id: Mapped[uuid.UUID] = _uuid("subscriptions.id", nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[uuid.UUID | None] = _uuid()
    changed_at: Mapped[datetime] = created_at_col()


class BillingSchedule(Base):
    __tablename__ = "billing_schedule"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    subscription_id: Mapped[uuid.UUID] = _uuid("subscriptions.id", nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    expected_amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    invoice_id: Mapped[uuid.UUID | None] = _uuid("invoices.id")
    created_at: Mapped[datetime] = created_at_col()
    # Phase 5 (P5-03): billing_schedule is append-only by DB grant (no DELETE for relay_app), so
    # a cancellation can never drop future rows -- it flags them instead. A flagged row is never
    # invoiced (invoice generation must filter on this); past/invoiced rows are untouched.
    cancelled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class MinutePack(Base):
    __tablename__ = "minute_packs"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    customer_id: Mapped[uuid.UUID] = _uuid("customers.id", nullable=False)
    subscription_id: Mapped[uuid.UUID] = _uuid("subscriptions.id", nullable=False)
    size_minutes: Mapped[Decimal] = mapped_column(MINUTES, nullable=False)
    price_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    purchased_on: Mapped[date] = mapped_column(Date, nullable=False)
    expires_on: Mapped[date | None] = mapped_column(Date)
    source_deal_id: Mapped[uuid.UUID | None] = _uuid("deals.id")
    invoice_id: Mapped[uuid.UUID | None] = _uuid("invoices.id")
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()
    created_by: Mapped[uuid.UUID | None] = _uuid()
    updated_at: Mapped[datetime] = created_at_col()
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)


class Invoice(Base):
    """Recorded manually (number, date, amount, file). GST is stored, never recomputed."""

    __tablename__ = "invoices"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    customer_id: Mapped[uuid.UUID] = _uuid("customers.id", nullable=False)
    subscription_id: Mapped[uuid.UUID | None] = _uuid("subscriptions.id")
    schedule_id: Mapped[uuid.UUID | None] = _uuid("billing_schedule.id")
    minute_pack_id: Mapped[uuid.UUID | None] = _uuid("minute_packs.id")
    number: Mapped[str] = mapped_column(Text, nullable=False)
    issue_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_ex_gst_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    gst_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_id: Mapped[uuid.UUID | None] = _uuid("files.id")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    void_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()
    created_by: Mapped[uuid.UUID | None] = _uuid()
    updated_at: Mapped[datetime] = created_at_col()
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)


class InvoiceVersion(Base):
    __tablename__ = "invoice_versions"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    invoice_id: Mapped[uuid.UUID] = _uuid("invoices.id", nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    changed_by: Mapped[uuid.UUID | None] = _uuid()
    changed_at: Mapped[datetime] = created_at_col()


class Payment(Base):
    """A receipt (positive) or a correction (negative, reverses one receipt). Never edited."""

    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    customer_id: Mapped[uuid.UUID] = _uuid("customers.id", nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="receipt")
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)  # GST-inclusive
    gst_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    received_on: Mapped[date] = mapped_column(Date, nullable=False)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    reference: Mapped[str | None] = mapped_column(Text)
    proof_file_id: Mapped[uuid.UUID] = _uuid("files.id", nullable=False)
    reverses_payment_id: Mapped[uuid.UUID | None] = _uuid("payments.id")
    reason: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[uuid.UUID] = _uuid("users.id", nullable=False)
    created_at: Mapped[datetime] = created_at_col()


class PaymentAllocation(Base):
    __tablename__ = "payment_allocations"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    payment_id: Mapped[uuid.UUID] = _uuid("payments.id", nullable=False)
    invoice_id: Mapped[uuid.UUID] = _uuid("invoices.id", nullable=False)
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = created_at_col()


class MinuteLedger(Base):
    """Append-only. A pack's balance is size_minutes minus the sum of its ledger rows."""

    __tablename__ = "minute_ledger"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    customer_id: Mapped[uuid.UUID] = _uuid("customers.id", nullable=False)
    usage_record_id: Mapped[uuid.UUID | None] = _uuid("usage_records.id")
    minute_pack_id: Mapped[uuid.UUID | None] = _uuid("minute_packs.id")  # NULL = overage
    minutes: Mapped[Decimal] = mapped_column(MINUTES, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)  # draw|correction|overage
    created_at: Mapped[datetime] = created_at_col()


class OverageItem(Base):
    __tablename__ = "overage_items"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    customer_id: Mapped[uuid.UUID] = _uuid("customers.id", nullable=False)
    minutes: Mapped[Decimal] = mapped_column(MINUTES, nullable=False)
    reason: Mapped[str] = mapped_column(
        Text, nullable=False
    )  # no_pack|pack_expired|late_correction
    status: Mapped[str] = mapped_column(Text, nullable=False, default="unbilled")
    invoice_id: Mapped[uuid.UUID | None] = _uuid("invoices.id")
    usage_record_id: Mapped[uuid.UUID | None] = _uuid("usage_records.id")
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = created_at_col()


class SubscriptionChange(Base):
    """A pending edit to a confirmed subscription, raised from an existing-customer deal.
    Built in: Phase 5 (schema only; confirmation logic is a later PR)."""

    __tablename__ = "subscription_changes"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    subscription_id: Mapped[uuid.UUID] = _uuid("subscriptions.id", nullable=False)
    source_deal_id: Mapped[uuid.UUID | None] = _uuid("deals.id")
    change_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    effective_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    confirmed_by: Mapped[uuid.UUID | None] = _uuid("users.id")
    confirmed_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    created_at: Mapped[datetime] = created_at_col()


class Renewal(Base):
    """One renewal decision recorded against a subscription. Built in: Phase 5 (schema only)."""

    __tablename__ = "renewals"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    subscription_id: Mapped[uuid.UUID] = _uuid("subscriptions.id", nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    deal_id: Mapped[uuid.UUID | None] = _uuid("deals.id")
    reason: Mapped[str | None] = mapped_column(Text)
    recorded_by: Mapped[uuid.UUID] = _uuid("users.id", nullable=False)
    recorded_at: Mapped[datetime] = created_at_col()


class BillingAlert(Base):
    """De-duplication ledger: one row per (customer, alert type, pack or invoice, date).
    `customer_id` has no foreign key: an org-level alert (usage sync failing) uses the all-zero
    uuid instead of a real customer."""

    __tablename__ = "billing_alerts"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, default=uuid.UUID(int=0)
    )
    alert_type: Mapped[str] = mapped_column(Text, nullable=False)
    ref_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, default=uuid.UUID(int=0)
    )
    alert_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = created_at_col()
