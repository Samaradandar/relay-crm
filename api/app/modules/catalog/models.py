# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Product catalog and the product lines on a deal. Money is integer paise."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import TIMESTAMPTZ, Base, created_at_col, org_fk, uuid_pk

TERMS = ("monthly", "quarterly", "yearly")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    unit: Mapped[str] = mapped_column(Text, nullable=False)
    list_price_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    floor_price_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Phase 4: pricing model. Subscriptions, proposals and invoices copy these values, so a
    # catalog edit never changes them.
    pricing_model: Mapped[str] = mapped_column(Text, nullable=False, default="per_minute")
    pack_options: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )  # [{"minutes": 1000, "price_paise": 150000}]
    subscription_fees: Mapped[dict[str, int]] = mapped_column(
        JSONB, nullable=False, default=dict
    )  # {"monthly": paise, "quarterly": paise, "yearly": paise}
    allowed_contract_terms: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=lambda: list(TERMS)
    )
    allowed_billing_frequencies: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=lambda: list(TERMS)
    )
    one_time_fees: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )  # [{"name": "Setup", "amount_paise": 500000}]
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = created_at_col()


class DealProduct(Base):
    __tablename__ = "deal_products"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    deal_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("deals.id"), nullable=False
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("products.id"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    line_total_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    discount_status: Mapped[str] = mapped_column(Text, nullable=False, default="none")
    pending_discount_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    approval_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = created_at_col()
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    updated_at: Mapped[datetime] = created_at_col()
    deleted_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
