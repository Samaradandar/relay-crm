# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Response shape for the account timeline."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Band(BaseModel):
    deal_id: uuid.UUID
    stage_key: str
    stage_name: str
    from_: datetime = Field(alias="from")
    to: datetime | None
    exit_kind: str | None
    round: int

    model_config = ConfigDict(populate_by_name=True)


class TimelineEvent(BaseModel):
    at: datetime
    kind: str  # stage | activity | document | ownership | milestone
    title: str
    detail: str | None
    deal_id: uuid.UUID
    by: str | None
    two_way: bool | None = None


class WeekCount(BaseModel):
    week: str
    count: int


class TimelineOut(BaseModel):
    account_id: uuid.UUID
    now: datetime
    bands: list[Band]
    events: list[TimelineEvent]
    engagement: list[WeekCount]
