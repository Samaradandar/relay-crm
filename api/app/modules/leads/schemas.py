# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Pydantic request and response shapes for leads, the Pool, assignment and transfers."""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.fields import Name200, Opt200, Opt2000, Raw320, Raw2000, Text500

LeadSource = Literal[
    "Website", "Referral", "LinkedIn", "Event", "Cold outbound", "Inbound call", "Inbound email"
]
RequestType = Literal[
    "upsell_top_up", "renewal", "plan_change", "other_product", "increase", "decrease", "return"
]


class RequestIn(BaseModel):
    """Phase 5 (P5-01): a request raised on an existing customer's account. Skips the Lead
    Pool and the assignment engine entirely; the account owner keeps it."""

    model_config = ConfigDict(extra="forbid")

    request_type: RequestType
    requirement: Text500
    reason: Opt2000 = None


class LeadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: Name200
    website: Raw2000 = None
    industry: Opt200 = None
    city: Opt200 = None
    contact_name: Name200
    contact_role: Opt200 = None
    # Decision maker or influencer: a lead's contact is an influencer unless the person entering
    # it knows they decide. Changeable any time afterwards (PATCH /contacts/{id}).
    contact_is_decision_maker: bool = False
    phone: Raw320 = None
    email: Raw320 = None
    lead_source: LeadSource
    # Optional: when absent the system sets the first next step from the pipeline (the first
    # unmet criterion of the entry stage, due tomorrow IST). An explicit value is kept.
    next_step_text: Text500 | None = None
    next_step_due_date: date | None = None
    first_contact_date: date | None = None
    create_anyway: bool = False
    override_block: bool = False
    reason: Opt2000 = None
    # Team Leader / Admin / Super admin only: give the lead to this person now instead of letting
    # the engine pick (same checks as a manual Pool assignment). null = automatic.
    owner_id: uuid.UUID | None = None


class DecideIn(BaseModel):
    """The Team Leader's decision on a lead a rep created: give it to the rep who created it,
    give it to another rep, or let the round robin pick."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["give_creator", "give_rep", "auto"]
    user_id: uuid.UUID | None = None  # give_rep only
    reason: Opt2000 = None


class ReasonIn(BaseModel):
    reason: Opt2000 = None


class AssignIn(BaseModel):
    user_id: uuid.UUID
    reason: Opt2000 = None


class BulkReassignIn(BaseModel):
    from_user_id: uuid.UUID
    to_user_id: uuid.UUID | None = None
    spread: bool = False  # round-robin across the departing rep's team
    reason: Opt2000 = None


class MoveTeamIn(BaseModel):
    to_team_id: uuid.UUID
    owner_id: uuid.UUID | None = None
    reason: Opt2000 = None


class TransferIn(BaseModel):
    direction: Literal["give", "take"]
    deal_id: uuid.UUID
    to_team_id: uuid.UUID | None = None  # give: the target team
    owner_id: uuid.UUID | None = None  # take: the rep in my team who should get it
    reason: Opt2000 = None


class CommentIn(BaseModel):
    comment: str = Field(min_length=1, max_length=2000)


class PersonRef(BaseModel):
    id: uuid.UUID
    name: str


class DecisionOut(BaseModel):
    deal_id: uuid.UUID
    status: Literal["pooled", "open"]
    owner: PersonRef | None = None
    assignment_method: Literal["rule", "round_robin", "manual"] | None = None
    warning: str | None = None


class WaitingLeadOut(BaseModel):
    """A lead the caller created that is waiting for their Team Leader (nothing else about it)."""

    deal_id: uuid.UUID
    company: str
    created_at: datetime
    auto_assign_at: datetime


class ContactRef(BaseModel):
    id: uuid.UUID | None
    name: str | None
    role: str | None
    phone: str | None
    email: str | None
    is_decision_maker: bool = False


class NextStepOut(BaseModel):
    id: uuid.UUID | None = None
    text: str
    due_date: date
    responsible: PersonRef | None
    lane: Literal["your_move", "waiting_on_them"] = "your_move"
    lane_since: datetime | None = None
    snooze_count: int = 0
    overdue: bool = False


class DealOut(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    company: str
    website: str | None
    industry: str | None
    city: str | None
    contact: ContactRef
    lead_source: str
    status: Literal["pooled", "open", "lost", "won"]
    stage_key: str
    pipeline: str = "new_business"
    request_type: RequestType | None = None
    owner: PersonRef | None
    team_id: uuid.UUID | None
    team_name: str | None
    generated_by: str
    first_contact_date: date | None
    assigned_at: datetime | None
    assignment_method: Literal["rule", "round_robin", "manual"] | None
    assigned_by: str | None
    assignment_rule: str | None
    next_step: NextStepOut | None
    waiting_reason: str | None
    version: int
    created_at: datetime
    last_audit_id: str | None = None
    warning: str | None = None
    # true on the response to a sales rep's create: the lead waits for their Team Leader
    awaiting_team_leader: bool = False
    # Phase 3
    stage_name: str | None = None
    value_paise: int = 0
    health: Literal["on_track", "at_risk", "stale"] = "on_track"
    flags: dict[str, Any] = Field(default_factory=dict)
    revision_round: int = 1
    expected_close_date: date | None = None
    won_at: datetime | None = None
    lost_at: datetime | None = None
    stage_entered_at: datetime | None = None
    deleted_at: datetime | None = None
    deleted_reason: str | None = None


class PoolRow(BaseModel):
    deal_id: uuid.UUID
    company: str
    contact_name: str | None
    lead_source: str
    generated_by: str
    entered_at: datetime
    routed_team_id: uuid.UUID | None
    routed_team_name: str | None
    waiting_reason: str | None
    held_for_manual: bool
    # a lead a sales rep created, waiting for this Team Leader's decision (else the rest is null)
    rep_hold: bool = False
    creator_id: uuid.UUID | None = None
    creator_assignable: bool = False  # the creator is active and this viewer may give it to them
    auto_assign_at: datetime | None = None  # when it falls back to automatic round robin


class RepLoad(BaseModel):
    user_id: uuid.UUID
    name: str
    team_id: uuid.UUID | None
    team_name: str | None
    active_leads: int
    cap: int
    assigned_in_period: int


class Distribution(BaseModel):
    pool_size: int
    reps: list[RepLoad]
    methods: dict[str, int]
    reassignments: list[dict[str, Any]]


class BulkResult(BaseModel):
    moved: int
    skipped: int
    deals: list[uuid.UUID]


class TransferOut(BaseModel):
    id: uuid.UUID
    deal_id: uuid.UUID
    company: str | None
    direction: Literal["give", "take"]
    from_team: str | None
    to_team: str | None
    proposed_owner: str | None
    requested_by: str
    reason: str
    other_tl_comment: str | None
    status: str
    approval_id: uuid.UUID | None
    created_at: datetime


class TransferLookupRow(BaseModel):
    """A deal in another team, as a Team Leader may see it: company and team only."""

    deal_id: uuid.UUID
    company: str
    team_name: str | None


class RuleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    priority: int = Field(ge=-2_147_483_648, le=2_147_483_647)
    active: bool = True
    match: dict[str, str] = Field(default_factory=dict)
    target_type: Literal["team", "group", "user"]
    target_id: uuid.UUID


class GroupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=200)
    team_id: uuid.UUID | None = None
    member_ids: list[uuid.UUID] = Field(default_factory=list)


class AssignmentSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_team_id: uuid.UUID
    default_cap: int = Field(ge=0, le=100_000)
    # minutes a sales rep's new lead waits for the Team Leader (1 minute to 7 days).
    # Omitted = keep the current value.
    rep_lead_hold_minutes: int | None = Field(default=None, ge=1, le=10_080)
    rules: list[RuleIn] = Field(default_factory=list)
    groups: list[GroupIn] = Field(default_factory=list)
    caps: dict[uuid.UUID, Annotated[int, Field(ge=0, le=100_000)]] = Field(default_factory=dict)
    reason: Opt2000 = None


class RuleOut(RuleIn):
    id: uuid.UUID
    target_name: str | None = None


class GroupOut(GroupIn):
    id: uuid.UUID


class CapRow(BaseModel):
    user_id: uuid.UUID
    name: str
    team_id: uuid.UUID | None
    cap: int
    is_default: bool
    active_leads: int


class AssignmentSettingsOut(BaseModel):
    default_team_id: uuid.UUID | None
    default_cap: int
    rep_lead_hold_minutes: int
    rules: list[RuleOut]
    groups: list[GroupOut]
    caps: list[CapRow]


class RuleTestIn(BaseModel):
    lead_source: str | None = None
    industry: str | None = None
    city: str | None = None
    category: str | None = None
    lead_type: str | None = None


class RuleTestOut(BaseModel):
    matched_rule: str | None
    target_type: str | None
    target_name: str | None
    eligible_reps: list[str]
    at_cap: list[str]
    outcome: str
