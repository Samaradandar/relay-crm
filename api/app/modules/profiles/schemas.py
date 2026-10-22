# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Response shapes for profiles, monitoring and team comparison."""

import uuid
from datetime import date, datetime

from pydantic import BaseModel


class ProfileUser(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    role: str
    team_name: str | None
    is_active: bool
    cap: int


class PipelineCount(BaseModel):
    key: str
    name: str
    open: int


class Workload(BaseModel):
    leads_assigned: int
    leads_generated: int
    active_leads: int
    cap: int
    pipeline_by_stage: list[PipelineCount]
    follow_ups_due_today: int
    follow_ups_overdue: int
    # Phase 6: this month's Revenue: Current / target, from rep_month_stats (never raw deals).
    revenue_month_paise: int
    target_paise: int | None


class StageConversion(BaseModel):
    key: str
    name: str
    entered: int
    advanced: int
    pct: float | None


class Outcomes(BaseModel):
    won: int
    lost: int
    lost_reasons: dict[str, int]
    win_rate_pct: float | None
    conversion_by_stage: list[StageConversion]


class ActivityRow(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID
    company: str
    type: str
    occurred_at: datetime
    discussed: str | None


class ActivitySummary(BaseModel):
    total: int
    recent: list[ActivityRow]


class ProfileOut(BaseModel):
    user: ProfileUser
    period: dict[str, datetime]
    workload: Workload
    outcomes: Outcomes
    activities: ActivitySummary


class MonitorRow(BaseModel):
    user_id: uuid.UUID
    name: str
    team_id: uuid.UUID | None
    team_name: str | None
    is_active: bool
    assigned: int
    active_leads: int
    cap: int
    by_stage: dict[str, int]
    last_interaction: datetime | None
    next_action: date | None
    won: int
    lost: int
    lost_reasons: dict[str, int]
    conversion_pct: float | None
    activities: int
    # Phase 6: from rep_month_stats, not computed here.
    revenue_month_paise: int
    target_paise: int | None


class MonitoringOut(BaseModel):
    period: dict[str, datetime]
    reps: list[MonitorRow]


class TeamCompareRow(BaseModel):
    team_id: uuid.UUID
    name: str
    reps: int
    active_leads: int
    assigned: int
    won: int
    lost: int
    win_rate_pct: float | None
    won_value_paise: int
    activities: int
    # Phase 6: this month's Revenue: Current / target summed over the team, plus the weighted
    # forecast (P6-09) for the team's open deals.
    revenue_month_paise: int
    target_paise: int
    weighted_paise: int
