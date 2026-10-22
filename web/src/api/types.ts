/** Shapes returned by the FastAPI backend. Keep in step with each module's schemas.py. */
import type { ActivityKind, PipelineDef, PipelineKey } from '@/lib/pipeline';
export type Role = 'user' | 'team_leader' | 'admin' | 'super_admin';
export type Scope = 'none' | 'own' | 'team' | 'all';
export type ReasonPolicy = 'none' | 'required' | 'optional';

export interface Team { id: string; name: string; color: string; teamLeaderId: string | null; createdAt: string; memberIds: string[] }

export interface User {
  id: string; name: string; email: string; role: Role; teamId: string | null;
  active: boolean; leadCap: number | null; lastSignInAt: string | null; createdAt: string;
}

export interface Permission { key: string; scope: Scope; mode: 'allow' | 'needs_approval' | 'request_only'; reason: ReasonPolicy }

export interface Me extends User { team: Team | null; permissions: Permission[] }

export type ApprovalStatus = 'pending' | 'approved' | 'rejected' | 'cancelled';
export interface ApprovalRequest {
  /** Teams a request touches (transfers): both Team Leaders can see it. */
  teamIds?: string[];
  /** Team-level requests (e.g. a rep's deal value change over 10%) go to the rep's Team Leader. */
  routeTo?: 'team_leader';
  id: string; type: 'user.create' | 'user.change_role' | string; title: string; summary: string; effect: string;
  requestedBy: string; requestedByName: string; requestedAt: string; reason: string; status: ApprovalStatus;
  changes: { label: string; from: string | null; to: string }[];
  decidedBy?: string; decidedByName?: string; decidedAt?: string; decisionReason?: string;
}

export interface Notification { id: string; audience?: 'admins' | 'everyone'; /** Set for personal alerts (lead assigned, lead waiting). */ userId?: string; type: 'approval' | 'people' | 'system' | 'lead'; title: string; body: string; createdAt: string; readAt: string | null; link?: string }

export interface NotificationPref { type: string; label: string; description: string; required: boolean; email: boolean }

export interface FailedJob { id: string; event: string; title: string; causedBy: string; attempts: number; lastError: string; failedAt: string }

export interface HistoryEntry { id: string; kind: 'stage' | 'note' | 'assign' | 'alert'; title: string; actor: string; at: string; reason?: string }

export class ApiError extends Error {
  constructor(public status: number, public code: 'permission_denied' | 'out_of_scope' | 'reason_required' | 'conflict' | 'approval_requested' | 'unauthenticated' | string, message: string) { super(message); }
}

/* ---------- Phase 2 · Lead Pool ---------- */
export type LeadSource = 'Website' | 'Referral' | 'LinkedIn' | 'Event' | 'Cold outbound' | 'Inbound call' | 'Inbound email';
export const LEAD_SOURCES: LeadSource[] = ['Website', 'Referral', 'LinkedIn', 'Event', 'Cold outbound', 'Inbound call', 'Inbound email'];
export type AssignmentMethod = 'rule' | 'round_robin' | 'manual';
// 'awaiting_team_leader': the backend deliberately holds a rep's own lead for the Team Leader to
// pick up by hand for a short window, before the engine auto-assigns it — not a real "no match".
export type WaitingReason = 'no_rule_match' | 'all_reps_at_cap' | 'no_active_reps' | 'awaiting_team_leader';

/** A new-business deal in the Lead stage. `pooled` = waiting in the Pool, `open` = assigned to a rep. */
export interface Lead {
  id: string; company: string; website?: string; industry?: string; city?: string;
  contactName: string; contactRole?: string; phone?: string; email?: string;
  /** True when the deal's main contact is its decision maker; otherwise an influencer. */
  contactDecisionMaker?: boolean;
  source: LeadSource; nextStep: string; nextStepDue: string; firstContactDate?: string;
  generatedById: string; generatedByName: string; createdAt: string;
  status: 'pooled' | 'open' | 'won' | 'lost'; teamId: string | null; ownerId: string | null; ownerName?: string;
  pooledAt: string; waitingReason?: WaitingReason;
  /** A rep's own lead, waiting for their Team Leader's decision (give to creator/another rep,
   * or let round robin pick). `creatorAssignable` is true only when this viewer may give it
   * back to the creator; `autoAssignAt` is when it falls back to automatic round robin. */
  repHold?: boolean; creatorAssignable?: boolean; autoAssignAt?: string;
  assignedAt?: string; method?: AssignmentMethod; ruleName?: string; assignedByName?: string; overCap?: boolean;
  seenByOwner?: boolean;
  // Phase 3+ (pipeline). A lead is a new-business deal in the Lead stage.
  pipeline?: PipelineKey; stage?: string; stageEnteredAt?: string; round?: number;
  /** Annual contract value in paise, from the plan or final price. No value before Prospecting. */
  value?: number;
  criteria?: Record<string, CriterionState>;
  fields?: Record<string, string>;
  lastActivityAt?: string;
  /** Whose move the next step is. "theirs" = waiting on the customer. */
  lane?: 'mine' | 'theirs'; waitingSince?: string; snoozeCount?: number;
  lostReason?: string; lostNotes?: string; lostFromStage?: string; lostAt?: string; wonAt?: string;
  documents?: DealDocument[];
  contacts?: Contact[];
  /** Live mode: the company (account) this deal belongs to. */
  accountId?: string;
  /** Live mode: the backend's checklist for the current stage, with its own keys and readiness. */
  liveCriteria?: LiveCriterion[];
  /** Live mode: current values of the editable deal fields, keyed by their API names. */
  liveValues?: Record<string, string>;
  /** Live mode: the fit checklist items and whether each is ticked. */
  fit?: { id: string; label: string; ticked: boolean }[];
}
/** Live mode: a stage page as the backend defines it (its columns, exit criteria and each deal's cell values). */
export interface StagePageData { columns: { key: string; label: string }[]; criteria: { key: string; label: string }[]; extras: Record<string, Record<string, unknown>> }
/** Live mode: a pipeline version exactly as the backend stores it (GET /pipelines/{key}/versions/{n}). */
export interface PipelineVersionLive {
  version_no: number; status: string; notes: string | null; published_at: string | null; published_by_name?: string | null;
  stages: { key: string; name: string; probability_pct: number; is_entry: boolean; is_final: boolean; won_on_entry: boolean; is_conditional: boolean; display_group: string | null; entry_checks: Record<string, unknown>[] }[];
  transitions: { from_stage_key: string; to_stage_key: string; kind: string }[];
  criteria: { stage_key: string; key: string; label: string; help_text: string | null; proof_type: string; proof_config: Record<string, unknown> }[];
}
export interface PipelineSettingsLive { versions: Omit<PipelineVersionLive, 'stages' | 'transitions' | 'criteria'>[]; live: PipelineVersionLive }
export interface LiveCriterion { key: string; label: string; proofType: string; ready: boolean; missing: string[] }

export interface CriterionState { state: 'open' | 'ticked' | 'verified' | 'rejected' | 'overridden' | 'proof_missing'; proof?: string; by?: string; at?: string; note?: string }
export interface Contact { id: string; name: string; role?: string; phone?: string; email?: string; decisionMaker?: boolean }
export interface DealDocument { id: string; name: string; kind: 'agreement' | 'proposal' | 'invoice' | 'advance' | 'prerequisite' | 'technical' | 'other'; status?: string; at: string; by: string }

export interface Activity {
  id: string; dealId: string; kind: ActivityKind; twoWay: boolean; at: string; byId: string; byName: string;
  discussed: string; requirements?: string; objections?: string; decisions?: string; actions?: string;
  nextStep: string; followUp: string; responsibleName: string;
  meetingAt?: string; attendees?: string; editedAt?: string; createdAt?: string;
  /** Meetings only: booked, held, postponed, cancelled or no_show, and why (live mode). */
  meetingStatus?: 'booked' | 'held' | 'postponed' | 'cancelled' | 'no_show';
  statusReasonCode?: string; statusReason?: string; statusAt?: string; statusByName?: string;
  /** For a booking that replaced a postponed one: the id of the one it replaced. */
  rescheduledFrom?: string;
}
/** A booked meeting took place: who came, what was discussed, and what happens next. */
export interface MeetingHeldInput {
  occurredAt?: string; attendees?: string; discussed: string;
  requirements?: string; objections?: string; decisions?: string; actions?: string;
  nextStep: string; followUp: string; lane?: 'mine' | 'theirs';
}
export type ActivityInput = Omit<Activity, 'id' | 'byId' | 'byName' | 'responsibleName' | 'editedAt'> & { lane?: 'mine' | 'theirs' };

export interface TimelineEvent { id: string; dealId: string; kind: 'stage' | 'activity' | 'assign' | 'won' | 'lost' | 'criterion' | 'edit' | 'alert'; title: string; at: string; by: string; detail?: string; reason?: string; activityId?: string }

export interface DealDetail { deal: Lead; activities: Activity[]; events: TimelineEvent[] }

export interface TodayData { mine: Lead[]; theirs: Lead[]; stale: Lead[]; meetingsToday: Activity[]; newLeads: Lead[] }

export interface MyNumbers { revenueMonth: number; target: number; best: number; projected: number; rank: number; reps: number; behindNext: number | null; wonMonth: number }

/** What the creator of a lead sees once it has gone to someone else: status only, no deal details. */
export interface AddedLead { id: string; company: string; createdAt: string; state: 'waiting' | 'yours' | 'assigned'; ownerName?: string; teamName?: string; method?: AssignmentMethod; since: string }

export interface NewLeadInput {
  company: string; website?: string; industry?: string; city?: string;
  contactName: string; contactRole?: string; phone?: string; email?: string;
  /** Decision maker (true) or influencer (false, the default): who the contact is in the deal. */
  contactDecisionMaker?: boolean;
  source: LeadSource; nextStep: string; nextStepDue?: string; firstContactDate?: string;
}

export interface DuplicateMatch { kind: 'exact' | 'similar'; field: 'website' | 'phone' | 'email' | 'name'; company: string; ownerName: string | null; teamName: string | null; inScope: boolean; leadId?: string; accountId?: string; customerId?: string; isExistingCustomer?: boolean }

export interface CreateLeadResult { lead: Lead; outcome: { kind: 'assigned'; ownerName: string; method: AssignmentMethod; ruleName?: string; toYou: boolean } | { kind: 'waiting'; reason: WaitingReason; teamName: string } }

export interface RepLoad { id: string; name: string; teamId: string | null; active: boolean; cap: number; activeLeads: number; assignedThisWeek: number; lastAssignedAt: string | null }

export interface PoolSummary {
  total: number; waiting: number; oldestWaitingAt: string | null;
  byTeam: { teamId: string; teamName: string; color: string; count: number }[];
  byReason: Record<WaitingReason, number>;
  assignedToday: Record<AssignmentMethod, number>;
  duplicatesFlagged: number; repsAtCap: number; reps: number;
}

/* ---------- Team Leader views ---------- */
export interface VerifyItem { dealId: string; company: string; ownerId: string; ownerName: string; criterionKey: string; label: string; stage: string; stageLabel: string; proof?: string; at?: string; pastStage: boolean }
export interface RepSummary {
  id: string; name: string; email: string; active: boolean; cap: number; activeLeads: number; overdue: number; stale: number; dueToday: number;
  meetingsWeek: number; wonMonth: number; lostMonth: number; revenueMonth: number; target: number; lastActivityAt: string | null;
  byStage: { key: string; label: string; count: number }[]; generated: number; assigned: number;
}
export interface TeamOverview { reps: RepSummary[]; revenueMonth: number; target: number; openValue: number; weighted: number; overdue: number; stale: number; openDeals: number }
export interface NeedsYou { assign: Lead[]; verify: VerifyItem[]; approvals: ApprovalRequest[]; proofMissing: VerifyItem[]; transfers: ApprovalRequest[] }

/* ---------- Phase 6: leaderboard, targets, incentives, personal dashboard, reports, forecast ---------- */
export interface LeaderboardRow {
  userId: string; name: string; teamId: string | null; teamName: string | null; teamColour: string | null;
  activeDeals: number; wonDeals: number; revenueBestPaise: number; revenueCurrentPaise: number;
  revenueProjectedPaise: number; targetPaise: number | null; rank: number | null; isYou: boolean;
}
export interface Leaderboard { month: string; rows: LeaderboardRow[] }
export interface Incentive {
  dealId: string; userId: string; rateBp: number | null; wonValuePaise: number; receivedPaise: number;
  pendingAmountPaise: number; earnedAmountPaise: number; expectedTotalPaise: number | null;
  status: 'no_rate' | 'pending' | 'earned'; earnedAt: string | null;
}
/** One line of the Incentives page: a won deal (or a pledge) with its incentive, the Team Leader's request and, for an Admin, the approval waiting. */
export interface IncentiveRow {
  dealId: string; company: string; stageKey: string; won: boolean; rep: { id: string | null; name: string | null }; dealValuePaise: number; wonAt: string | null; onboardingDone: boolean;
  incentive: { rateBp: number | null; status: string; receivedPaise: number; pendingAmountPaise: number; earnedAmountPaise: number; expectedTotalPaise: number | null } | null;
  request: { id: string; type: string; status: string; rateBp: number; reason: string; requestedBy: string | null; decisionReason: string | null; createdAt: string; decidedAt: string | null } | null;
  approvalId: string | null;
}
export interface MissingRateRow { dealId: string; userId: string; wonValuePaise: number; wonAt: string }
export interface ForecastBucket { key: string; label: string; weightedPaise: number; dealCount: number }
export interface Forecast { groupBy: 'month' | 'team' | 'rep'; buckets: ForecastBucket[]; totalWeightedPaise: number }
export type ReportName = 'leads' | 'conversion' | 'won_lost' | 'revenue_billing' | 'renewals_churn' | 'rep_performance' | 'measures';
export interface RepProfile { summary: RepSummary; today: TodayData; lost: { company: string; reason: string; at: string }[]; recent: (Activity & { company: string })[] }

/* ---------- Admin and Super admin ---------- */
export interface AuditEntry { id: string; at: string; actor: string; role: Role | 'system'; area: 'Deals' | 'Assignment' | 'Pipeline' | 'Catalog' | 'Users' | 'Teams' | 'Exports'; action: string; target: string; from: string; to: string; reason: string; dealId?: string }
export interface TeamCompare { id: string; name: string; color: string; leader: string; reps: number; activeReps: number; revenueMonth: number; target: number; openDeals: number; openValue: number; weighted: number; overdue: number; stale: number; atCap: number; poolWaiting: number; wonMonth: number }
export interface CompanyOverview { teams: TeamCompare[]; reps: (RepSummary & { teamId: string | null })[]; failedJobs: number; pendingApprovals: number }
export interface RuleInput { id?: string; name: string; active: boolean; match: { source?: LeadSource; industry?: string }; target: { teamId?: string; groupId?: string; userId?: string } }
export interface AssignmentConfig { rules: (RuleInput & { id: string; priority: number })[]; groups: { id: string; name: string; teamId: string; memberIds: string[] }[]; defaultTeamId: string; caps: { userId: string; name: string; teamId: string | null; cap: number; active: boolean; activeLeads: number }[] }
export interface ProductPlan { name: string; minutes: number; price: number; floor: number }
export interface Product { id: string; name: string; model: string; description: string; plans: ProductPlan[]; terms: string[]; oneTimeFee: number; active: boolean }
export interface PipelineVersion { version: number; publishedAt: string; by: string; reason: string; snapshot: Record<PipelineKey, PipelineDef> }

/* ---------- Phase 5 · Accounts, customers, subscriptions, churn ---------- */
export interface Account { id: string; name: string; website: string | null; domain: string | null; industry: string | null; city: string | null; accountOwnerId: string | null }
export interface AccountDealRow { id: string; status: string; stageKey: string; leadSource: string; ownerName: string | null; teamName: string | null }
export interface AccountDetail extends Account { deals: AccountDealRow[]; lockedDeals: { label: string }[]; contacts: Contact[] }
export interface TimelineBand { dealId: string; stageKey: string; stageName: string; from: string; to: string | null; exitKind: string | null; round: number }
export interface TimelineRow { at: string; kind: string; title: string; detail: string | null; dealId: string; by: string | null; twoWay: boolean | null }
export interface AccountTimeline { accountId: string; now: string; bands: TimelineBand[]; events: TimelineRow[]; engagement: { week: string; count: number }[] }

export type CustomerStatus = 'active' | 'churned';
export interface Customer { id: string; accountId: string; accountName: string; accountOwnerId: string; accountOwnerName: string | null; status: CustomerStatus; voicePlatformCustomerId: string | null; createdAt: string }
export type AtRiskReason = 'cancellation_requested' | 'payment_overdue' | 'minutes_ran_out' | 'contract_ending_soon';
export interface AtRiskCustomer { customerId: string; accountId: string; accountName: string; accountOwnerId: string; accountOwnerName: string | null; reasons: AtRiskReason[] }

export type RenewalStatus = 'upcoming' | 'renewed' | 'not_renewing';
export type CancellationStatus = 'none' | 'requested' | 'cancelled';
export interface Subscription {
  id: string; customerId: string; sourceDealId: string | null; status: string; pricingModel: 'per_minute' | 'saas' | null;
  packPricePaise: number | null; packSizeMinutes: string | null; feePaise: number | null;
  billingFrequency: 'monthly' | 'quarterly' | 'yearly' | null; termType: 'plan' | 'contract' | null; contractTerm: string | null;
  startDate: string | null; renewalDate: string | null; contractEndDate: string | null; paidThroughDate: string | null;
  billingContactId: string | null; confirmedBy: string | null; confirmedAt: string | null; version: number;
  renewalStatus: RenewalStatus; cancellationStatus: CancellationStatus; cancellationRequestedAt: string | null;
  endedAt: string | null; endReason: string | null;
}
export interface SubscriptionChange { id: string; subscriptionId: string; sourceDealId: string | null; changeType: string; payload: Record<string, unknown>; effectiveDate: string | null; status: 'pending' | 'confirmed' | 'rejected'; confirmedBy: string | null; confirmedAt: string | null; createdAt: string }
/** The slice of a subscription returned by `/renewal` and `/cancellation` — NOT the full Subscription shape. */
export interface SubscriptionLifecycle { id: string; customerId: string; renewalStatus: RenewalStatus; cancellationStatus: CancellationStatus; cancellationRequestedAt: string | null; contractEndDate: string | null; renewalDate: string | null; endedAt: string | null; endReason: string | null }

export type RequestType = 'upsell_top_up' | 'renewal' | 'plan_change' | 'other_product' | 'increase' | 'decrease' | 'return';
export const REQUEST_TYPE_LABEL: Record<RequestType, string> = { upsell_top_up: 'Upsell / top-up', renewal: 'Renewal', plan_change: 'Plan change', other_product: 'Other product', increase: 'Increase', decrease: 'Decrease', return: 'Return (win-back)' };

export interface ChurnEvent { id: string; customerId: string; churnDate: string; reason: string; notes: string | null; monthlyValueLostPaise: number; subscriptionStart: string; timeAsCustomerDays: number; lastRenewalDate: string | null; recordedBy: string; recordedAt: string; returnedDealId: string | null; reversedAt: string | null; reversedBy: string | null }
export interface ChurnHistoryRow { id: string; customerId: string; accountId: string; accountName: string; accountOwnerName: string | null; churnDate: string; reason: string; notes: string | null; monthlyValueLostPaise: number; subscriptionStart: string; timeAsCustomerDays: number; lastRenewalDate: string | null; recordedBy: string; recordedByName: string | null; recordedAt: string; returnedDealId: string | null; reversedAt: string | null; returned: boolean }
export interface ChurnHistoryPage { total: number; page: number; pageSize: number; rows: ChurnHistoryRow[] }
export interface ChurnReasonCount { reason: string; count: number }
export interface ChurnRepBreakdown { ownerId: string | null; ownerName: string | null; count: number; valueLostPaise: number }
export interface ChurnMonthBreakdown { month: string; count: number; valueLostPaise: number }
export interface ChurnHistorySummary { churnedThisMonth: number; churnedThisQuarter: number; churnedAllTime: number; churnRateThisMonth: string | null; totalMonthlyValueLostPaise: number; topReasons: ChurnReasonCount[]; byReason: ChurnReasonCount[] | null; byRep: ChurnRepBreakdown[] | null; byMonth: ChurnMonthBreakdown[] | null; teamsCompareAvailable: boolean }
export interface ChurnReason { id: string; label: string; active: boolean; sortOrder: number }


/* ---------- Transfers of a lead between teams (a Team Leader asks, an Admin decides in Approvals) ---------- */
export interface Transfer { id: string; dealId: string; company?: string; direction: 'give' | 'take'; fromTeam?: string; toTeam?: string; proposedOwner?: string; requestedBy: string; reason: string; otherTlComment?: string; status: string; createdAt: string }
export interface TransferLookup { dealId: string; company: string; teamName?: string }
export interface TransferInput { direction: 'give' | 'take'; dealId: string; toTeamId?: string; ownerId?: string; reason: string }

/* ---------- Lead capture: website forms, and the review queue for leads that need a person ---------- */
export interface CaptureReview { id: string; company?: string; sourceKind: string; preview: Record<string, unknown>; matchedDealId?: string; createdAt: string }
export type CaptureAction = 'create_anyway' | 'add_to_existing' | 'discard';
export interface CaptureSource { id: string; kind: string; name: string; sourceKey?: string; allowedOrigins: string[]; defaultLeadSource?: string; defaultNextStep?: string; active: boolean }

/* ---------- CSV import of leads (Team Leaders and Admins) ---------- */
export interface CsvUpload { id: string; headers: string[]; rowCount: number }
/** How the rows of the file would land: ready to create, duplicates, needing a look, existing customers, or unusable. */
export interface CsvCounts { total: number; ready: number; duplicate: number; needs_review: number; existing_customer: number; invalid: number }
export interface CsvRunResult { status: string; processed: number; created: number }

/* ---------- Proposal: rounds with the customer, and the draft that is emailed ---------- */
export type ProposalFeedback = 'accepted' | 'changes_requested' | 'objections';
export interface ProposalRoundMeeting { id: string; occurredAt: string; attendees: string; discussed?: string }
export interface ProposalRound { id: string; roundNo: number; feedback: ProposalFeedback; changes?: string; recordedByName?: string; recordedAt: string; closedAt?: string; meeting?: ProposalRoundMeeting }
/** Where the proposal loop stands: the rounds so far, the meeting that counts for the current one. */
export interface ProposalRounds { rounds: ProposalRound[]; currentRoundNo: number; heldMeeting?: ProposalRoundMeeting; bookedMeeting?: ProposalRoundMeeting; accepted: boolean }
export interface ProposalItem { id: string; description: string; quantity: number; unitPricePaise: number; discountBp: number; lineTotalPaise: number }
export interface ProposalDraft { id: string; versionNo: number; status: string; bodyHtml: string; termsHtml: string; recipientName?: string; recipientEmail?: string; sentAt?: string; sentTo?: string; items: ProposalItem[]; totalText?: string }
export interface ProposalTerms {
  lines: { productId?: string; description: string; quantity: number; unitPricePaise: number }[];
  businessModel: string; billingFrequency: string; minCommittedMinutes?: number; concurrency: number; taxPercent: number;
  termsAndConditions: string; scopeOfWork: string; companyName: string; companyDetails?: string;
}
/** What the proposal form starts with; `lastTerms` is the latest drafted proposal's terms (a revision starts from them). */
export interface ProposalPrefill { companyName: string; businessModel?: string; minCommittedMinutes?: number; scopeOfWork?: string; lines: ProposalTerms['lines']; lastTerms?: ProposalTerms }
/** One visit of a deal to a stage, with the checklist it had and what was ticked. */
export interface StageVisit {
  entryId: string; stageKey: string; stageName: string; round: number; enteredAt: string; enteredBy?: string; exitedAt?: string; exitKind?: string; reason?: string; current: boolean;
  criteria: { key: string; label: string; ticked: boolean; status?: string; tickedBy?: string; tickedAt?: string; proofMissing: boolean; details: { label: string; value: string }[]; activity?: { type: string; at: string; status?: string; attendees: string[]; discussed?: string } }[];
}
export interface ProposalTemplate { id: string; name: string; body: string; termsHtml: string; version: number }
