# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Pydantic request and response shapes."""

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class UnmatchedOut(BaseModel):
    id: uuid.UUID
    source_record_id: str
    voice_customer_id: str
    occurred_at: datetime
    minutes: Decimal
    revision: int
    received_at: datetime


class SyncHealthOut(BaseModel):
    cursor: str | None
    last_success_at: datetime | None
    last_attempt_at: datetime | None
    last_error: str | None
    consecutive_failures: int


class SyncResultOut(BaseModel):
    pulled: int
    cursor: str | None
