# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Request and response shapes for exports."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ExportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity: Literal["deals", "activities", "churn_history"]
    filters: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class ExportOut(BaseModel):
    id: uuid.UUID
    entity: str
    filters: dict[str, Any]
    row_count: int | None
    reason: str
    status: str
    created_at: datetime
    user_id: uuid.UUID
    user_name: str | None
    download_url: str | None = None
