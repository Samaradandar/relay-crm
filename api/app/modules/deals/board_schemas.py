# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Response shapes for the board, stage pages and stage metrics."""

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from app.modules.leads.schemas import PersonRef


class BoardCard(BaseModel):
    id: uuid.UUID
    company: str
    owner: PersonRef | None
    stage_key: str
    value_paise: int
    health: str
    flags: dict[str, Any]
    revision_round: int | None
    next_step_text: str | None
    next_step_due: date | None
    overdue: bool
    age_days: int
    lead_source: str


class BoardColumn(BaseModel):
    key: str
    name: str
    stage_keys: list[str]
    probability_pct: int
    count: int
    value_paise: int
    cards: list[BoardCard]


class BoardSummary(BaseModel):
    open_deals: int
    pipeline_value_paise: int
    stale: int


class BoardOut(BaseModel):
    columns: list[BoardColumn]
    summary: BoardSummary


class ExtraColumn(BaseModel):
    key: str
    label: str


class StageRow(BaseModel):
    id: uuid.UUID
    company: str
    owner: PersonRef | None
    team_name: str | None
    lead_source: str
    value_paise: int
    health: str
    flags: dict[str, Any]
    stage_key: str
    revision_round: int | None
    age_days: int
    stage_entered_at: datetime
    next_step_text: str | None
    next_step_due: date | None
    overdue: bool
    extra: dict[str, Any]


class StagePageOut(BaseModel):
    key: str
    name: str
    probability_pct: int
    total: int
    value_paise: int
    page: int
    page_size: int
    columns: list[ExtraColumn]
    # the published exit criteria of this stage, in order (what a deal needs to leave it)
    criteria: list[ExtraColumn] = []
    rows: list[StageRow]


class StageMetrics(BaseModel):
    key: str
    name: str
    configured_probability_pct: int
    open_deals: int = 0
    deals_entered: int
    won: int
    lost: int
    win_rate_pct: float | None
    avg_days_in_stage: float | None
    conversion_to_next_pct: float | None
