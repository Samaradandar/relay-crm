# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Request and response shapes for the catalog and deal lines."""

import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_QUANTITY = 2_147_483_647  # the quantity column is a 32-bit integer
MAX_PAISE = 9_000_000_000_000_000_000  # stays inside a signed 64-bit column


Frequency = Literal["monthly", "quarterly", "yearly"]
ALL_FREQUENCIES: tuple[Frequency, ...] = ("monthly", "quarterly", "yearly")


class PackOption(BaseModel):
    """A minute pack the customer can buy: size in whole minutes and its price."""

    model_config = ConfigDict(extra="forbid")

    minutes: int = Field(gt=0, le=MAX_QUANTITY)
    price_paise: int = Field(ge=0, le=MAX_PAISE)


class OneTimeFee(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    amount_paise: int = Field(ge=0, le=MAX_PAISE)


class ProductIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    unit: Literal["per_minute", "fixed"]
    list_price_paise: int = Field(ge=0, le=MAX_PAISE)
    floor_price_paise: int = Field(ge=0, le=MAX_PAISE)
    active: bool = True
    pricing_model: Literal["per_minute", "saas"] = "per_minute"
    pack_options: list[PackOption] = Field(default_factory=list)
    subscription_fees: dict[Frequency, int] = Field(default_factory=dict)
    allowed_contract_terms: list[Frequency] = Field(default_factory=lambda: list(ALL_FREQUENCIES))
    allowed_billing_frequencies: list[Frequency] = Field(
        default_factory=lambda: list(ALL_FREQUENCIES)
    )
    one_time_fees: list[OneTimeFee] = Field(default_factory=list)

    @model_validator(mode="after")
    def _pricing_shape(self) -> "ProductIn":
        if self.pricing_model == "per_minute" and self.subscription_fees:
            raise ValueError("a per-minute product has packs, not subscription fees")
        if self.pricing_model == "saas" and self.pack_options:
            raise ValueError("a SaaS product has subscription fees, not minute packs")
        if len({p.minutes for p in self.pack_options}) != len(self.pack_options):
            raise ValueError("pack sizes must be unique")
        if any(f < 0 or f > MAX_PAISE for f in self.subscription_fees.values()):
            raise ValueError("a subscription fee is out of range")
        if not self.allowed_contract_terms or not self.allowed_billing_frequencies:
            raise ValueError("allow at least one contract term and one billing frequency")
        if self.pricing_model == "saas":
            missing = [
                f for f in self.allowed_billing_frequencies if f not in self.subscription_fees
            ]
            if self.subscription_fees and missing:
                raise ValueError(f"no subscription fee set for: {', '.join(missing)}")
        return self


class ProductPut(BaseModel):
    products: list[ProductIn]
    reason: str | None = None


class ProductOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    unit: str
    list_price_paise: int
    floor_price_paise: int
    active: bool
    pricing_model: str
    pack_options: list[dict[str, Any]]
    subscription_fees: dict[str, int]
    allowed_contract_terms: list[str]
    allowed_billing_frequencies: list[str]
    one_time_fees: list[dict[str, Any]]


def _two_places(v: Decimal | None) -> Decimal | None:
    """A discount is stored with two decimals, so price it with two decimals."""
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if v is not None else None


class LineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    quantity: int = Field(gt=0, le=MAX_QUANTITY)
    discount_pct: Decimal = Field(default=Decimal(0), ge=Decimal(0), le=Decimal(100))
    reason: str | None = None

    _round = field_validator("discount_pct")(_two_places)


class LinePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quantity: int | None = Field(default=None, gt=0, le=MAX_QUANTITY)
    discount_pct: Decimal | None = Field(default=None, ge=Decimal(0), le=Decimal(100))
    reason: str | None = None

    _round = field_validator("discount_pct")(_two_places)


class LineOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    quantity: int
    unit_price_paise: int
    discount_pct: Decimal
    line_total_paise: int
    floor_price_paise: int
    discount_status: str
    pending_discount_pct: Decimal | None
    awaiting_approval: bool


class LinesOut(BaseModel):
    lines: list[LineOut]
    value_paise: int
    pending_requests: list[dict[str, Any]]
    approval_id: uuid.UUID | None = None
