# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Request and response shapes for manual document records."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Kind = Literal["proposal", "agreement", "invoice", "advance", "prerequisite", "technical"]
Status = Literal["draft", "sent", "countersigned", "terminated", "received"]


class DocumentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Kind
    status: Status = "draft"
    file_id: uuid.UUID | None = None
    sent_at: datetime | None = None
    amount_paise: int | None = Field(default=None, ge=0)
    number: str | None = None
    notes: str | None = None
    reason: str | None = None


class DocumentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Status | None = None
    file_id: uuid.UUID | None = None
    sent_at: datetime | None = None
    amount_paise: int | None = Field(default=None, ge=0)
    number: str | None = None
    notes: str | None = None
    reason: str | None = None


class DocumentOut(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID
    kind: str
    version_no: int
    status: str
    sent_at: datetime | None
    file_id: uuid.UUID | None
    file_name: str | None
    amount_paise: int | None
    number: str | None
    notes: str | None
    created_by_name: str | None
    created_at: datetime
