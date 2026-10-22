# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""SQLAlchemy tables owned by this module: training datasets, the model registry and deal
scores. Training/shipping/scoring logic is deferred (minimum history and ship margin not yet
confirmed with Samar) -- only the dataset builder (dataset.py) is built in this PR.

Built in: Phase 7.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.base import TIMESTAMPTZ, Base, org_fk, uuid_pk


def _uuid(fk: str | None = None, *, nullable: bool = True) -> Mapped[Any]:
    col = UUID(as_uuid=True)
    if fk:
        return mapped_column(col, ForeignKey(fk), nullable=nullable)
    return mapped_column(col, nullable=nullable)


class MlDataset(Base):
    __tablename__ = "ml_datasets"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    as_of: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    s3_key: Mapped[str | None] = mapped_column(Text)
    rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    won: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lost: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    feature_list: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    built_by: Mapped[uuid.UUID | None] = _uuid("users.id")
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)


class MlModel(Base):
    __tablename__ = "ml_models"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    dataset_id: Mapped[uuid.UUID] = _uuid("ml_datasets.id", nullable=False)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    baseline_metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="candidate")
    calibration_s3_key: Mapped[str | None] = mapped_column(Text)
    trained_by: Mapped[uuid.UUID | None] = _uuid("users.id")
    shipped_by: Mapped[uuid.UUID | None] = _uuid("users.id")
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
    shipped_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    retired_at: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)


class DealScore(Base):
    __tablename__ = "deal_scores"

    id: Mapped[uuid.UUID] = uuid_pk()
    org_id: Mapped[uuid.UUID] = org_fk()
    deal_id: Mapped[uuid.UUID] = _uuid("deals.id", nullable=False)
    model_id: Mapped[uuid.UUID] = _uuid("ml_models.id", nullable=False)
    probability_bp: Mapped[int] = mapped_column(Integer, nullable=False)
    factors: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    scored_at: Mapped[datetime] = mapped_column(TIMESTAMPTZ, nullable=False)
