/* Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta. */
/**
 * The real backend returns snake_case shapes that don't line up 1:1 with the hand-written
 * camelCase domain types in ./types.ts (e.g. GET /teams has no memberIds, only member_count).
 * These adapters cover the Phase 1 shapes needed to sign in and route by role; other entities
 * (Lead, Deal, …) still need the same treatment as each feature is wired to the live API.
 */
import type {
  Account, AccountDealRow, AccountDetail, AccountTimeline, Activity, ActivityInput,
  ApprovalRequest, AtRiskCustomer,
  ChurnEvent, ChurnHistoryPage, ChurnHistoryRow, ChurnHistorySummary, ChurnReason, Contact,
  AuditEntry, CreateLeadResult, CriterionState, Customer, DealDocument, DuplicateMatch, ProposalDraft, ProposalFeedback, ProposalPrefill, ProposalRounds, ProposalTemplate, ProposalTerms, StageVisit, IncentiveRow, FailedJob, Lead, Me, NewLeadInput,
  Notification, NotificationPref, Permission, Subscription, SubscriptionChange,
  SubscriptionLifecycle, Team, TimelineBand, TimelineRow, TodayData, User
} from './types';
import type { ActivityKind } from '@/lib/pipeline';

export interface RawTeam { id: string; name: string; leader_id?: string | null; leader_name?: string | null; leaders?: string[]; member_count?: number }
export interface RawUser { id: string; email: string; name: string; role: User['role']; team_id: string | null; team_name?: string | null; is_active: boolean }
export interface RawMe extends RawUser { team: RawTeam | null; permissions: Permission[]; last_login_at: string | null }

// GET /teams returns {leader_id, leader_name}; the nested team on GET /me instead returns
// {leaders: string[]} (names only, no id) — handle both. Backend doesn't return team member ids
// from either shape; pages that need the roster must call a dedicated endpoint, not Team.memberIds.
export function mapTeam(t: RawTeam): Team {
  return { id: t.id, name: t.name, color: 'brand', teamLeaderId: t.leader_id ?? null, createdAt: '', memberIds: [] };
}

export function mapUser(u: RawUser): User {
  return { id: u.id, name: u.name, email: u.email, role: u.role, teamId: u.team_id, active: u.is_active, leadCap: null, lastSignInAt: null, createdAt: '' };
}

export function mapMe(m: RawMe): Me {
  return { ...mapUser(m), lastSignInAt: m.last_login_at, team: m.team ? mapTeam(m.team) : null, permissions: m.permissions };
}

/* ---------- Phase 5: accounts, customers, subscriptions, churn ---------- */
export interface RawAccount { id: string; name: string; website: string | null; domain: string | null; industry: string | null; city: string | null; account_owner_id: string | null }
export interface RawContact { id: string; account_id: string; name: string; role: string | null; phone: string | null; email: string | null; is_decision_maker: boolean }
export interface RawAccountDealRow { id: string; status: string; stage_key: string; lead_source: string; owner_name: string | null; team_name: string | null }
export interface RawAccountDetail extends RawAccount { deals: RawAccountDealRow[]; locked_deals: { label: string }[]; contacts: RawContact[] }
export interface RawTimelineBand { deal_id: string; stage_key: string; stage_name: string; from: string; to: string | null; exit_kind: string | null; round: number }
export interface RawTimelineEvent { at: string; kind: string; title: string; detail: string | null; deal_id: string; by: string | null; two_way: boolean | null }
export interface RawAccountTimeline { account_id: string; now: string; bands: RawTimelineBand[]; events: RawTimelineEvent[]; engagement: { week: string; count: number }[] }
export interface RawCustomer { id: string; account_id: string; account_name: string; account_owner_id: string; account_owner_name: string | null; status: string; voice_platform_customer_id: string | null; created_at: string }
export interface RawAtRiskCustomer { customer_id: string; account_id: string; account_name: string; account_owner_id: string; account_owner_name: string | null; reasons: string[] }
export interface RawSubscription {
  id: string; customer_id: string; source_deal_id: string | null; status: string; pricing_model: 'per_minute' | 'saas' | null;
  pack_price_paise: number | null; pack_size_minutes: string | null; fee_paise: number | null;
  billing_frequency: 'monthly' | 'quarterly' | 'yearly' | null; term_type: 'plan' | 'contract' | null; contract_term: string | null;
  start_date: string | null; renewal_date: string | null; contract_end_date: string | null; paid_through_date: string | null;
  billing_contact_id: string | null; confirmed_by: string | null; confirmed_at: string | null; version: number;
  renewal_status: Subscription['renewalStatus']; cancellation_status: Subscription['cancellationStatus'];
  cancellation_requested_at: string | null; ended_at: string | null; end_reason: string | null;
}
export interface RawSubscriptionChange { id: string; subscription_id: string; source_deal_id: string | null; change_type: string; payload: Record<string, unknown>; effective_date: string | null; status: SubscriptionChange['status']; confirmed_by: string | null; confirmed_at: string | null; created_at: string }
export interface RawSubscriptionLifecycle { id: string; customer_id: string; renewal_status: Subscription['renewalStatus']; cancellation_status: Subscription['cancellationStatus']; cancellation_requested_at: string | null; contract_end_date: string | null; renewal_date: string | null; ended_at: string | null; end_reason: string | null }
export interface RawChurnEvent { id: string; customer_id: string; churn_date: string; reason: string; notes: string | null; monthly_value_lost_paise: number; subscription_start: string; time_as_customer_days: number; last_renewal_date: string | null; recorded_by: string; recorded_at: string; returned_deal_id: string | null; reversed_at: string | null; reversed_by: string | null }
export interface RawChurnHistoryRow extends RawChurnEvent { account_id: string; account_name: string; account_owner_name: string | null; recorded_by_name: string | null; returned: boolean }
export interface RawChurnHistoryPage { total: number; page: number; page_size: number; rows: RawChurnHistoryRow[] }
export interface RawChurnHistorySummary { churned_this_month: number; churned_this_quarter: number; churned_all_time: number; churn_rate_this_month: string | null; total_monthly_value_lost_paise: number; top_reasons: { reason: string; count: number }[]; by_reason: { reason: string; count: number }[] | null; by_rep: { owner_id: string | null; owner_name: string | null; count: number; value_lost_paise: number }[] | null; by_month: { month: string; count: number; value_lost_paise: number }[] | null; teams_compare_available: boolean }
export interface RawChurnReason { id: string; label: string; active: boolean; sort_order: number }
export interface RawDuplicateMatch { match_type: 'domain' | 'phone' | 'email' | 'name'; company: string; owner_name: string | null; team_name?: string | null; in_scope: boolean; account_id?: string; deal_id?: string | null; similarity?: number | null; is_existing_customer?: boolean; customer_id?: string | null }

export const mapContact = (c: RawContact): Contact => ({ id: c.id, name: c.name, role: c.role ?? undefined, phone: c.phone ?? undefined, email: c.email ?? undefined, decisionMaker: c.is_decision_maker });
export const mapAccount = (a: RawAccount): Account => ({ id: a.id, name: a.name, website: a.website, domain: a.domain, industry: a.industry, city: a.city, accountOwnerId: a.account_owner_id });
export const mapAccountDealRow = (d: RawAccountDealRow): AccountDealRow => ({ id: d.id, status: d.status, stageKey: d.stage_key, leadSource: d.lead_source, ownerName: d.owner_name, teamName: d.team_name });
export const mapAccountDetail = (a: RawAccountDetail): AccountDetail => ({ ...mapAccount(a), deals: a.deals.map(mapAccountDealRow), lockedDeals: a.locked_deals, contacts: a.contacts.map(mapContact) });
export const mapTimelineBand = (b: RawTimelineBand): TimelineBand => ({ dealId: b.deal_id, stageKey: b.stage_key, stageName: b.stage_name, from: b.from, to: b.to, exitKind: b.exit_kind, round: b.round });
export const mapTimelineEvent = (e: RawTimelineEvent): TimelineRow => ({ at: e.at, kind: e.kind, title: e.title, detail: e.detail, dealId: e.deal_id, by: e.by, twoWay: e.two_way });
export const mapAccountTimeline = (t: RawAccountTimeline): AccountTimeline => ({ accountId: t.account_id, now: t.now, bands: t.bands.map(mapTimelineBand), events: t.events.map(mapTimelineEvent), engagement: t.engagement });
export const mapCustomer = (c: RawCustomer): Customer => ({ id: c.id, accountId: c.account_id, accountName: c.account_name, accountOwnerId: c.account_owner_id, accountOwnerName: c.account_owner_name, status: c.status as Customer['status'], voicePlatformCustomerId: c.voice_platform_customer_id, createdAt: c.created_at });
export const mapAtRiskCustomer = (c: RawAtRiskCustomer): AtRiskCustomer => ({ customerId: c.customer_id, accountId: c.account_id, accountName: c.account_name, accountOwnerId: c.account_owner_id, accountOwnerName: c.account_owner_name, reasons: c.reasons as AtRiskCustomer['reasons'] });
export const mapSubscriptionLifecycle = (s: RawSubscriptionLifecycle): SubscriptionLifecycle => ({ id: s.id, customerId: s.customer_id, renewalStatus: s.renewal_status, cancellationStatus: s.cancellation_status, cancellationRequestedAt: s.cancellation_requested_at, contractEndDate: s.contract_end_date, renewalDate: s.renewal_date, endedAt: s.ended_at, endReason: s.end_reason });
export const mapSubscription = (s: RawSubscription): Subscription => ({ id: s.id, customerId: s.customer_id, sourceDealId: s.source_deal_id, status: s.status, pricingModel: s.pricing_model, packPricePaise: s.pack_price_paise, packSizeMinutes: s.pack_size_minutes, feePaise: s.fee_paise, billingFrequency: s.billing_frequency, termType: s.term_type, contractTerm: s.contract_term, startDate: s.start_date, renewalDate: s.renewal_date, contractEndDate: s.contract_end_date, paidThroughDate: s.paid_through_date, billingContactId: s.billing_contact_id, confirmedBy: s.confirmed_by, confirmedAt: s.confirmed_at, version: s.version, renewalStatus: s.renewal_status, cancellationStatus: s.cancellation_status, cancellationRequestedAt: s.cancellation_requested_at, endedAt: s.ended_at, endReason: s.end_reason });
export const mapSubscriptionChange = (c: RawSubscriptionChange): SubscriptionChange => ({ id: c.id, subscriptionId: c.subscription_id, sourceDealId: c.source_deal_id, changeType: c.change_type, payload: c.payload, effectiveDate: c.effective_date, status: c.status, confirmedBy: c.confirmed_by, confirmedAt: c.confirmed_at, createdAt: c.created_at });
export const mapChurnEvent = (e: RawChurnEvent): ChurnEvent => ({ id: e.id, customerId: e.customer_id, churnDate: e.churn_date, reason: e.reason, notes: e.notes, monthlyValueLostPaise: e.monthly_value_lost_paise, subscriptionStart: e.subscription_start, timeAsCustomerDays: e.time_as_customer_days, lastRenewalDate: e.last_renewal_date, recordedBy: e.recorded_by, recordedAt: e.recorded_at, returnedDealId: e.returned_deal_id, reversedAt: e.reversed_at, reversedBy: e.reversed_by });
export const mapChurnHistoryRow = (r: RawChurnHistoryRow): ChurnHistoryRow => ({ ...mapChurnEvent(r), accountId: r.account_id, accountName: r.account_name, accountOwnerName: r.account_owner_name, recordedByName: r.recorded_by_name, returned: r.returned });
export const mapChurnHistoryPage = (p: RawChurnHistoryPage): ChurnHistoryPage => ({ total: p.total, page: p.page, pageSize: p.page_size, rows: p.rows.map(mapChurnHistoryRow) });
export const mapChurnHistorySummary = (s: RawChurnHistorySummary): ChurnHistorySummary => ({ churnedThisMonth: s.churned_this_month, churnedThisQuarter: s.churned_this_quarter, churnedAllTime: s.churned_all_time, churnRateThisMonth: s.churn_rate_this_month, totalMonthlyValueLostPaise: s.total_monthly_value_lost_paise, topReasons: s.top_reasons, byReason: s.by_reason, byRep: s.by_rep?.map((r) => ({ ownerId: r.owner_id, ownerName: r.owner_name, count: r.count, valueLostPaise: r.value_lost_paise })) ?? null, byMonth: s.by_month?.map((m) => ({ month: m.month, count: m.count, valueLostPaise: m.value_lost_paise })) ?? null, teamsCompareAvailable: s.teams_compare_available });
export const mapChurnReason = (r: RawChurnReason): ChurnReason => ({ id: r.id, label: r.label, active: r.active, sortOrder: r.sort_order });
export const mapDuplicateMatch = (m: RawDuplicateMatch, kind: DuplicateMatch['kind'] = m.match_type === 'name' ? 'similar' : 'exact'): DuplicateMatch => ({ kind, field: m.match_type === 'domain' ? 'website' : m.match_type, company: m.company, ownerName: m.owner_name, teamName: m.team_name ?? null, inScope: m.in_scope, leadId: m.deal_id ?? undefined, accountId: m.account_id, customerId: m.customer_id ?? undefined, isExistingCustomer: m.is_existing_customer });

/* ---------- Phase 2-3: deals / pipeline ---------- */
// GET /deals and GET /deals/{id} return the SAME flat shape (no separate "detail" wrapper with
// activities/events — those are fetched from /deals/{id}/activities and /history/... instead).
export interface RawNextStep { id: string; text: string; due_date: string; responsible?: { id: string; name: string } | null; lane: 'your_move' | 'waiting_on_them' | string; lane_since?: string | null; snooze_count: number; overdue?: boolean }
export interface RawFitItem { id: string; label: string; ticked: boolean }
// This is the real "stage exit criteria" model (what /criteria/{key}/tick|untick|verify act on) —
// only present on GET /deals/{id}, not the list. `fit[]` is a separate, simpler ICP-fit checklist.
// `status` is the tick's verify status: 'pending' = ticked, waiting for the Team Leader's check.
export interface RawProgressCriterion { key: string; label: string; help_text?: string | null; proof_type?: string; ticked: boolean; status: 'pending' | 'verified' | 'rejected' | 'overridden' | null; ticked_by: string | null; ticked_at: string | null; verified_by: string | null; verify_reason: string | null; proof_missing: boolean; ready?: boolean | null; missing?: string[] }
export interface RawProgress { criteria: RawProgressCriterion[] }
export interface RawDealContact { id: string; name: string; role?: string | null; phone?: string | null; email?: string | null; is_decision_maker?: boolean }
export interface RawDeal {
  id: string; account_id?: string | null; company: string; website?: string | null; industry?: string | null; city?: string | null;
  contact?: { id: string; name: string; role?: string | null; phone?: string | null; email?: string | null; is_decision_maker?: boolean } | null;
  lead_source: string; status: string; stage_key?: string | null; stage_name?: string | null; pipeline?: string | null;
  owner?: { id: string; name: string } | null; team_id: string | null; team_name?: string | null;
  generated_by?: string | null; first_contact_date?: string | null;
  assigned_at?: string | null; assignment_method?: string | null; assigned_by?: string | null; assignment_rule?: string | null;
  next_step?: RawNextStep | null; waiting_reason?: string | null; created_at: string;
  value_paise?: number | null; revision_round?: number | null; stage_entered_at?: string | null;
  won_at?: string | null; lost_at?: string | null; lost_reason?: string | null; lost_notes?: string | null; lost_from_stage?: string | null;
  fit?: RawFitItem[]; custom?: Record<string, unknown>[]; contacts?: RawDealContact[]; progress?: RawProgress;
  data?: Record<string, unknown> | null; pain_point?: string | null; use_case?: string | null; plan?: string | null; pricing_model?: string | null;
  expected_minutes_per_month?: number | null; expected_close_date?: string | null; expected_close_reason?: string | null;
}
// Compact row from GET /today (one per next step, not a full deal).
export interface RawStepRow {
  id: string; deal_id: string; company: string; text: string; due_date: string; lane: RawNextStep['lane'];
  lane_since?: string | null; snooze_count: number; overdue?: boolean; stage_key?: string | null; stage_name?: string | null;
  value_paise?: number | null; owner?: { id: string; name: string } | null; assigned_by?: string | null;
}
export interface RawToday { user: { id: string; name: string }; your_move: RawStepRow[]; waiting_on_them: RawStepRow[]; stale: RawStepRow[]; new_leads: RawStepRow[]; counts: Record<string, number> }

function mapCriteria(progress?: RawProgress): Record<string, CriterionState> | undefined {
  if (!progress?.criteria?.length) return undefined;
  return Object.fromEntries(progress.criteria.map((c) => [c.key, {
    state: c.proof_missing && c.status !== 'overridden' ? 'proof_missing' : c.status === 'verified' || c.status === 'overridden' ? c.status : c.ticked ? 'ticked' : 'open',
    by: c.ticked_by ?? c.verified_by ?? undefined, at: c.ticked_at ?? undefined, note: c.verify_reason ?? c.label,
  } as CriterionState]));
}

// POST /leads takes snake_case LeadIn (`extra="forbid"`) and returns the deal directly (DealOut),
// not a {lead, outcome} wrapper — build that wrapper here instead.
export function toRawLeadIn(input: NewLeadInput, opts: { createAnyway?: boolean; reason?: string } = {}) {
  return {
    company: input.company, website: input.website ?? null, industry: input.industry ?? null, city: input.city ?? null,
    contact_name: input.contactName, contact_role: input.contactRole ?? null, phone: input.phone ?? null, email: input.email ?? null,
    contact_is_decision_maker: !!input.contactDecisionMaker,
    lead_source: input.source, next_step_text: input.nextStep || null, next_step_due_date: input.nextStepDue || null,
    first_contact_date: input.firstContactDate || null, create_anyway: !!opts.createAnyway, reason: opts.reason ?? null,
  };
}

export function mapCreateLeadResult(d: RawDeal & { warning?: string | null; awaiting_team_leader?: boolean }, meId: string): CreateLeadResult {
  const lead = mapDeal(d);
  // No owner yet can mean a real pool-engine wait (waiting_reason set) or the backend deliberately
  // holding the rep's own lead for the Team Leader to grab by hand (awaiting_team_leader, no
  // waiting_reason) — these need different messages, not the same "no rule matched" fallback.
  const outcome: CreateLeadResult['outcome'] = d.owner
    ? { kind: 'assigned', ownerName: d.owner.name, method: (d.assignment_method ?? 'rule') as Lead['method'] & string, ruleName: d.assignment_rule ?? undefined, toYou: d.owner.id === meId }
    : { kind: 'waiting', reason: (d.waiting_reason ?? (d.awaiting_team_leader ? 'awaiting_team_leader' : 'no_rule_match')) as Lead['waitingReason'] & string, teamName: d.team_name ?? '' };
  return { lead, outcome };
}

export function mapDeal(d: RawDeal): Lead {
  return {
    id: d.id, company: d.company, website: d.website ?? undefined, industry: d.industry ?? undefined, city: d.city ?? undefined,
    contactDecisionMaker: d.contact?.is_decision_maker ?? false, accountId: d.account_id ?? undefined,
    contactName: d.contact?.name ?? '', contactRole: d.contact?.role ?? undefined, phone: d.contact?.phone ?? undefined, email: d.contact?.email ?? undefined,
    source: d.lead_source as Lead['source'], nextStep: d.next_step?.text ?? '', nextStepDue: d.next_step?.due_date ?? '',
    firstContactDate: d.first_contact_date ?? undefined,
    generatedById: '', generatedByName: d.generated_by ?? '', createdAt: d.created_at,
    status: (['open', 'won', 'lost', 'pooled'].includes(d.status) ? d.status : 'open') as Lead['status'],
    teamId: d.team_id, ownerId: d.owner?.id ?? null, ownerName: d.owner?.name,
    pooledAt: d.created_at, waitingReason: d.waiting_reason as Lead['waitingReason'],
    assignedAt: d.assigned_at ?? undefined, method: d.assignment_method as Lead['method'], ruleName: d.assignment_rule ?? undefined, assignedByName: d.assigned_by ?? undefined,
    pipeline: d.pipeline as Lead['pipeline'], stage: d.stage_key ?? undefined, stageEnteredAt: d.stage_entered_at ?? undefined, round: d.revision_round ?? undefined,
    value: d.value_paise ?? undefined, criteria: mapCriteria(d.progress), fields: d.custom?.length ? Object.assign({}, ...d.custom) : undefined,
    // The backend's own checklist for the current stage (live mode only): the deal page renders
    // this list instead of the static one in lib/pipeline.ts, so keys always match the API.
    liveCriteria: d.progress?.criteria?.map((c) => ({ key: c.key, label: c.label, proofType: c.proof_type ?? 'fields', ready: !!c.ready, missing: c.missing ?? [] })),
    liveValues: d.progress ? liveValues(d) : undefined,
    fit: d.fit?.map((f) => ({ id: f.id, label: f.label, ticked: f.ticked })),
    lane: d.next_step?.lane === 'your_move' ? 'mine' : d.next_step?.lane === 'waiting_on_them' ? 'theirs' : undefined,
    waitingSince: d.next_step?.lane_since ?? undefined, snoozeCount: d.next_step?.snooze_count,
    lostReason: d.lost_reason ?? undefined, lostNotes: d.lost_notes ?? undefined, lostFromStage: d.lost_from_stage ?? undefined, lostAt: d.lost_at ?? undefined, wonAt: d.won_at ?? undefined,
    contacts: d.contacts?.map((c) => ({ id: c.id, name: c.name, role: c.role ?? undefined, phone: c.phone ?? undefined, email: c.email ?? undefined, decisionMaker: c.is_decision_maker }))
  };
}

/** Current values of the editable deal fields (PATCH /deals/{id} names), to prefill proof forms. */
function liveValues(d: RawDeal): Record<string, string> {
  const top: Record<string, unknown> = {
    pain_point: d.pain_point, use_case: d.use_case, plan: d.plan, pricing_model: d.pricing_model,
    expected_minutes_per_month: d.expected_minutes_per_month, expected_close_date: d.expected_close_date, expected_close_reason: d.expected_close_reason,
  };
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries({ ...(d.data ?? {}), ...top })) if (v !== null && v !== undefined && v !== '') out[k] = String(v);
  return out;
}

/* ---------- audit log (GET /audit) ---------- */
export interface RawAuditRow { seq?: number; id: string; at: string; actor: string; actor_role: string; action: string; entity_type: string; entity_id: string; target: string; old: Record<string, unknown> | null; new: Record<string, unknown> | null; reason: string | null; note: string | null }
const AUDIT_AREA: Record<string, AuditEntry['area']> = {
  deal: 'Deals', activity: 'Deals', next_step: 'Deals', document: 'Deals', deal_product: 'Deals', contact: 'Deals', account: 'Deals',
  assignment_settings: 'Assignment', pool: 'Assignment', transfer: 'Assignment',
  pipeline: 'Pipeline', pipeline_version: 'Pipeline', product: 'Catalog', catalog: 'Catalog',
  user: 'Users', team: 'Teams', export: 'Exports',
};
/** A changed value in a few words: "status: open", "3 fields". */
function auditSummary(v: Record<string, unknown> | null): string {
  if (!v) return '';
  const entries = Object.entries(v).filter(([, x]) => x !== null && x !== undefined && typeof x !== 'object');
  if (!entries.length) return Object.keys(v).length ? `${Object.keys(v).length} field${Object.keys(v).length > 1 ? 's' : ''}` : '';
  return entries.slice(0, 3).map(([k, x]) => `${k.replace(/_/g, ' ')}: ${String(x)}`).join(', ');
}
export const mapAuditRow = (r: RawAuditRow): AuditEntry => ({
  id: r.id, at: r.at, actor: r.actor, role: (r.actor_role || 'system') as AuditEntry['role'],
  area: AUDIT_AREA[r.entity_type] ?? (r.entity_type.includes('pipeline') || r.entity_type.includes('stage') ? 'Pipeline' : 'Deals'),
  action: r.action.replace(/[._]/g, ' '), target: r.target, from: auditSummary(r.old), to: auditSummary(r.new),
  reason: r.reason ?? r.note ?? '', dealId: r.entity_type === 'deal' ? r.entity_id : undefined,
});

/* ---------- proposal rounds and drafts ---------- */
interface RawRoundMeeting { id: string; occurred_at: string; attendees: unknown[]; discussed: string | null }
export interface RawRounds { rounds: { id: string; round_no: number; feedback: string; changes: string | null; recorded_by_name: string | null; recorded_at: string; closed_at: string | null; meeting: RawRoundMeeting | null }[]; current_round_no: number; held_meeting: RawRoundMeeting | null; booked_meeting: RawRoundMeeting | null; accepted: boolean }
const mapRoundMeeting = (m: RawRoundMeeting | null) => m ? { id: m.id, occurredAt: m.occurred_at, attendees: m.attendees.join(', '), discussed: m.discussed ?? undefined } : undefined;
export const mapRounds = (r: RawRounds): ProposalRounds => ({
  rounds: r.rounds.map((x) => ({ id: x.id, roundNo: x.round_no, feedback: x.feedback as ProposalFeedback, changes: x.changes ?? undefined, recordedByName: x.recorded_by_name ?? undefined, recordedAt: x.recorded_at, closedAt: x.closed_at ?? undefined, meeting: mapRoundMeeting(x.meeting) })),
  currentRoundNo: r.current_round_no, heldMeeting: mapRoundMeeting(r.held_meeting), bookedMeeting: mapRoundMeeting(r.booked_meeting), accepted: r.accepted,
});
export interface RawProposal { id: string; version_no: number; status: string; content: Record<string, unknown>; sent_at: string | null; sent_to: string | null; items: { id: string; description: string; quantity: number; unit_price_paise: number; discount_bp: number; line_total_paise: number }[] }
export const mapProposal = (p: RawProposal): ProposalDraft => ({
  id: p.id, versionNo: p.version_no, status: p.status,
  bodyHtml: String(p.content.body_html ?? ''), termsHtml: String(p.content.terms_html ?? ''),
  recipientName: (p.content.recipient_name as string) || undefined, recipientEmail: (p.content.recipient_email as string) || undefined,
  sentAt: p.sent_at ?? undefined, sentTo: p.sent_to ?? undefined, totalText: (p.content.total_inr as string) || undefined,
  items: p.items.map((i) => ({ id: i.id, description: i.description, quantity: i.quantity, unitPricePaise: i.unit_price_paise, discountBp: i.discount_bp, lineTotalPaise: i.line_total_paise })),
});
type RawTerms = { lines: { product_id?: string | null; description: string; quantity: number; unit_price_paise: number }[]; business_model: string; billing_frequency: string; min_committed_minutes?: number | null; concurrency: number; tax_percent: number; terms_and_conditions: string; scope_of_work: string; company_name: string; company_details?: string | null };
export const mapTerms = (t: RawTerms): ProposalTerms => ({
  lines: t.lines.map((l) => ({ productId: l.product_id ?? undefined, description: l.description, quantity: l.quantity, unitPricePaise: l.unit_price_paise })),
  businessModel: t.business_model, billingFrequency: t.billing_frequency, minCommittedMinutes: t.min_committed_minutes ?? undefined, concurrency: t.concurrency, taxPercent: t.tax_percent,
  termsAndConditions: t.terms_and_conditions, scopeOfWork: t.scope_of_work, companyName: t.company_name, companyDetails: t.company_details ?? undefined,
});
export const termsBody = (t: ProposalTerms) => ({
  lines: t.lines.map((l) => ({ product_id: l.productId ?? null, description: l.description, quantity: l.quantity, unit_price_paise: l.unitPricePaise })),
  business_model: t.businessModel, billing_frequency: t.billingFrequency, min_committed_minutes: t.minCommittedMinutes ?? null, concurrency: t.concurrency, tax_percent: t.taxPercent,
  terms_and_conditions: t.termsAndConditions, scope_of_work: t.scopeOfWork, company_name: t.companyName, company_details: t.companyDetails ?? null,
});
export interface RawPrefill { company_name: string; business_model: string | null; min_committed_minutes: number | null; scope_of_work: string | null; lines: { product_id: string; description: string; quantity: number; unit_price_paise: number }[]; last_terms: RawTerms | null }
export const mapPrefill = (r: RawPrefill): ProposalPrefill => ({
  companyName: r.company_name, businessModel: r.business_model ?? undefined, minCommittedMinutes: r.min_committed_minutes ?? undefined, scopeOfWork: r.scope_of_work ?? undefined,
  lines: r.lines.map((l) => ({ productId: l.product_id, description: l.description, quantity: l.quantity, unitPricePaise: l.unit_price_paise })),
  lastTerms: r.last_terms ? mapTerms(r.last_terms) : undefined,
});
export interface RawStageVisit {
  entry_id: string; stage_key: string; stage_name: string; round: number; entered_at: string; entered_by: string | null; exited_at: string | null; exit_kind: string | null; reason: string | null; current: boolean;
  criteria: { key: string; label: string; ticked: boolean; status: string | null; ticked_by: string | null; ticked_at: string | null; proof_missing: boolean; details: { label: string; value: string }[]; activity: { type: string; at: string; status: string | null; attendees: string[]; discussed: string | null } | null }[];
}
export const mapStageVisit = (v: RawStageVisit): StageVisit => ({
  entryId: v.entry_id, stageKey: v.stage_key, stageName: v.stage_name, round: v.round, enteredAt: v.entered_at, enteredBy: v.entered_by ?? undefined, exitedAt: v.exited_at ?? undefined,
  exitKind: v.exit_kind ?? undefined, reason: v.reason ?? undefined, current: v.current,
  criteria: v.criteria.map((c) => ({ key: c.key, label: c.label, ticked: c.ticked, status: c.status ?? undefined, tickedBy: c.ticked_by ?? undefined, tickedAt: c.ticked_at ?? undefined, proofMissing: c.proof_missing, details: c.details,
    activity: c.activity ? { type: c.activity.type, at: c.activity.at, status: c.activity.status ?? undefined, attendees: c.activity.attendees, discussed: c.activity.discussed ?? undefined } : undefined })),
});
export interface RawTemplate { id: string; name: string; body: string; terms_html: string; version: number }
export const mapTemplate = (t: RawTemplate): ProposalTemplate => ({ id: t.id, name: t.name, body: t.body, termsHtml: t.terms_html, version: t.version });

export interface RawDocument { id: string; kind: string; version_no: number; status: string; file_name: string | null; number: string | null; created_by_name: string | null; created_at: string }
const cap = (x: string) => x.charAt(0).toUpperCase() + x.slice(1);
export const mapDocument = (r: RawDocument): DealDocument => ({
  id: r.id, kind: (['agreement', 'proposal', 'invoice', 'advance', 'prerequisite', 'technical'].includes(r.kind) ? r.kind : 'other') as DealDocument['kind'],
  name: `${cap(r.kind)}${r.number ? ` ${r.number}` : ''} · v${r.version_no}${r.file_name ? ` · ${r.file_name}` : ''}`,
  status: cap(r.status), at: r.created_at, by: r.created_by_name ?? '',
});

/** A /today row only carries the next step, not the whole deal — good enough for the list views. */
export function mapStepRow(r: RawStepRow): Lead {
  return {
    id: r.deal_id, company: r.company, contactName: '', source: 'Website', nextStep: r.text, nextStepDue: r.due_date,
    generatedById: '', generatedByName: '', createdAt: r.lane_since ?? '', status: 'open', teamId: null,
    ownerId: r.owner?.id ?? null, ownerName: r.owner?.name, pooledAt: r.lane_since ?? '',
    assignedByName: r.assigned_by ?? undefined, stage: r.stage_key ?? undefined, value: r.value_paise ?? undefined,
    lane: r.lane === 'your_move' ? 'mine' : r.lane === 'waiting_on_them' ? 'theirs' : undefined,
    waitingSince: r.lane_since ?? undefined, snoozeCount: r.snooze_count,
    // Carry the next-step id so action calls (snooze/nudge/…) can hit /next-steps/{id}/… without a refetch.
    fields: { __stepId: r.id }
  };
}

/* ---------- Phase 2: pool ---------- */
export interface RawPoolRow { deal_id: string; company: string; contact_name: string; lead_source: string; generated_by: string; entered_at: string; routed_team_id: string | null; routed_team_name: string | null; waiting_reason: string | null; rep_hold?: boolean; creator_id?: string | null; creator_assignable?: boolean; auto_assign_at?: string | null }
export interface RawDistributionRep { user_id: string; name: string; team_id: string | null; team_name: string | null; active_leads: number; cap: number; assigned_in_period: number }
export interface RawDistribution { pool_size: number; reps: RawDistributionRep[] }

export const mapPoolRow = (p: RawPoolRow): Lead => ({
  id: p.deal_id, company: p.company, contactName: p.contact_name, source: p.lead_source as Lead['source'],
  nextStep: '', nextStepDue: '', generatedById: p.creator_id ?? '', generatedByName: p.generated_by, createdAt: p.entered_at,
  status: 'pooled', teamId: p.routed_team_id, ownerId: null, pooledAt: p.entered_at,
  waitingReason: p.waiting_reason as Lead['waitingReason'],
  repHold: p.rep_hold ?? false, creatorAssignable: p.creator_assignable ?? false, autoAssignAt: p.auto_assign_at ?? undefined,
});

/* ---------- Phase 3: activities ---------- */
export interface RawActivity {
  id: string; deal_id: string; type: string; occurred_at: string; two_way: boolean;
  attendees: string[]; discussed: string | null; requirements: string | null;
  objections: string | null; decisions: string | null; actions: string | null;
  created_by: string; created_by_name: string | null; attributed_to_name: string | null;
  created_at: string; edited: boolean; deleted?: boolean;
  meeting_status?: string | null; status_reason_code?: string | null; status_reason?: string | null;
  status_at?: string | null; status_by_name?: string | null; rescheduled_from?: string | null;
}

export const mapActivity = (r: RawActivity): Activity => ({
  id: r.id, dealId: r.deal_id, kind: r.type as ActivityKind, twoWay: r.two_way, at: r.occurred_at,
  byId: r.created_by, byName: r.created_by_name ?? '',
  discussed: r.discussed ?? '', requirements: r.requirements ?? undefined,
  objections: r.objections ?? undefined, decisions: r.decisions ?? undefined,
  actions: r.actions ?? undefined,
  // The next step lives on the deal, not the activity -- GET /deals/{id}/activities doesn't
  // return one; callers that need the current next step read it off the Deal instead.
  nextStep: '', followUp: '', responsibleName: r.attributed_to_name ?? r.created_by_name ?? '',
  meetingAt: r.type === 'meeting' ? r.occurred_at : undefined,
  attendees: r.attendees.length ? r.attendees.join(', ') : undefined,
  editedAt: r.edited ? r.created_at : undefined, createdAt: r.created_at,
  meetingStatus: (r.meeting_status ?? undefined) as Activity['meetingStatus'],
  statusReasonCode: r.status_reason_code ?? undefined, statusReason: r.status_reason ?? undefined,
  statusAt: r.status_at ?? undefined, statusByName: r.status_by_name ?? undefined, rescheduledFrom: r.rescheduled_from ?? undefined,
});

export const toRawActivityIn = (input: ActivityInput) => ({
  type: input.kind,
  occurred_at: input.kind === 'meeting' && input.meetingAt ? input.meetingAt : input.at,
  two_way: input.twoWay,
  attendees: input.attendees ? input.attendees.split(',').map((a) => a.trim()).filter(Boolean) : [],
  discussed: input.discussed,
  requirements: input.requirements || undefined,
  objections: input.objections || undefined,
  decisions: input.decisions || undefined,
  actions: input.actions || undefined,
  next_step_text: input.nextStep,
  next_step_due_date: input.followUp,
  next_step_lane: input.lane === 'theirs' ? 'waiting_on_them' : 'your_move',
});

export const mapRepLoad = (r: RawDistributionRep) => ({
  id: r.user_id, name: r.name, teamId: r.team_id, active: true, cap: r.cap, activeLeads: r.active_leads,
  assignedThisWeek: r.assigned_in_period, lastAssignedAt: null as string | null,
});

export const mapToday = (t: RawToday): TodayData => ({
  mine: t.your_move.map(mapStepRow), theirs: t.waiting_on_them.map(mapStepRow), stale: t.stale.map(mapStepRow),
  meetingsToday: [], newLeads: t.new_leads.map(mapStepRow)
});

/* ---------- Phase 1: notifications, approvals, failed jobs ---------- */
// GET /notifications returns {unread, items}, not a bare array.
export interface RawNotification { id: string; type: string; title: string; body: string; link: string | null; read_at: string | null; created_at: string }
export interface RawNotificationList { unread: number; items: RawNotification[] }
export interface RawNotificationPref { type: string; label: string; email_enabled: boolean; required: boolean }
export interface RawApproval { id: string; type: string; status: ApprovalRequest['status']; summary: string | null; payload: Record<string, unknown>; requested_by: string; requested_by_name: string | null; request_reason: string; routed_to: 'team_leader' | 'admin_pool'; decided_by: string | null; decided_by_name: string | null; decision_reason: string | null; created_at: string; decided_at: string | null }
export interface RawFailedJob { id: number; type: string; attempts: number; last_error: string | null; created_at: string }

// Backend notification types are dotted (approval.requested, user.invited, churn.risk, lead.assigned, …);
// the UI only groups them into 4 coarse categories, so bucket by prefix instead of casting blindly.
function bucketNotificationType(t: string): Notification['type'] {
  if (t.startsWith('approval.')) return 'approval';
  if (t.startsWith('lead.') || t.startsWith('pool.') || t.startsWith('transfer.')) return 'lead';
  if (t.startsWith('user.') || t.startsWith('team.')) return 'people';
  return 'system';
}
export const mapNotification = (n: RawNotification): Notification => ({ id: n.id, type: bucketNotificationType(n.type), title: n.title, body: n.body, createdAt: n.created_at, readAt: n.read_at, link: n.link ?? undefined });
export const mapNotificationList = (l: RawNotificationList) => ({ unread: l.unread, items: l.items.map(mapNotification) });
export const mapNotificationPref = (p: RawNotificationPref): NotificationPref => ({ type: p.type, label: p.label, description: '', required: p.required, email: p.email_enabled });
export const mapApproval = (a: RawApproval): ApprovalRequest => ({
  id: a.id, type: a.type, title: a.type, summary: a.summary ?? '', effect: '',
  requestedBy: a.requested_by, requestedByName: a.requested_by_name ?? '', requestedAt: a.created_at,
  reason: a.request_reason, status: a.status, changes: [],
  decidedBy: a.decided_by ?? undefined, decidedByName: a.decided_by_name ?? undefined,
  decidedAt: a.decided_at ?? undefined, decisionReason: a.decision_reason ?? undefined,
  routeTo: a.routed_to === 'team_leader' ? 'team_leader' : undefined,
});
export const mapFailedJob = (j: RawFailedJob): FailedJob => ({ id: String(j.id), event: j.type, title: j.type, causedBy: '', attempts: j.attempts, lastError: j.last_error ?? '', failedAt: j.created_at });

/* ---------- Team Leader / Admin: team and rep summaries ---------- */
// Phase 6 added revenue_month_paise / target_paise (per rep) and + weighted_paise (per team) to
// these Phase 1-3 endpoints, read from rep_month_stats -- never computed client-side from deals.
export interface RawMonitoringRep { user_id: string; name: string; team_id: string | null; team_name: string | null; is_active: boolean; assigned: number; active_leads: number; cap: number; by_stage: Record<string, number>; last_interaction: string | null; next_action: string | null; won: number; lost: number; revenue_month_paise: number; target_paise: number | null }
export interface RawMonitoring { period: { from: string; to: string }; reps: RawMonitoringRep[] }
export interface RawTeamCompareRow { team_id: string; name: string; reps: number; active_leads: number; assigned: number; won: number; lost: number; win_rate_pct: number | null; won_value_paise: number; activities: number; revenue_month_paise: number; target_paise: number; weighted_paise: number }
export interface RawProfile {
  user: { id: string; name: string; email: string; role: User['role']; team_name: string | null; is_active: boolean; cap: number };
  workload: { leads_assigned: number; leads_generated: number; active_leads: number; cap: number; pipeline_by_stage: { key: string; name: string; open: number }[]; follow_ups_due_today: number; follow_ups_overdue: number; revenue_month_paise: number; target_paise: number | null };
  outcomes: { won: number; lost: number; lost_reasons: Record<string, number>; win_rate_pct: number | null };
  activities: { total: number; recent: unknown[] };
}

export function mapMonitoringRep(r: RawMonitoringRep) {
  return {
    id: r.user_id, name: r.name, email: '', active: r.is_active, cap: r.cap, activeLeads: r.active_leads,
    overdue: 0, stale: 0, dueToday: 0, meetingsWeek: 0, wonMonth: r.won, lostMonth: r.lost,
    revenueMonth: r.revenue_month_paise, target: r.target_paise ?? 0, lastActivityAt: r.last_interaction,
    byStage: Object.entries(r.by_stage).map(([key, count]) => ({ key, label: key, count })),
    generated: 0, assigned: r.assigned,
  };
}

export function mapTeamOverview(m: RawMonitoring) {
  const reps = m.reps.map(mapMonitoringRep);
  return {
    reps,
    revenueMonth: reps.reduce((s, r) => s + r.revenueMonth, 0),
    target: reps.reduce((s, r) => s + r.target, 0),
    openValue: 0, weighted: 0, overdue: 0, stale: 0,
    openDeals: reps.reduce((s, r) => s + r.activeLeads, 0),
  };
}

export function mapTeamCompare(t: RawTeamCompareRow) {
  return { id: t.team_id, name: t.name, color: 'brand', leader: '', reps: t.reps, activeReps: t.reps, revenueMonth: t.revenue_month_paise, target: t.target_paise, openDeals: t.active_leads, openValue: t.won_value_paise, weighted: t.weighted_paise, overdue: 0, stale: 0, atCap: 0, poolWaiting: 0, wonMonth: t.won };
}

export function mapCompanyOverview(teams: RawTeamCompareRow[], failedJobs: number, pendingApprovals: number) {
  return { teams: teams.map(mapTeamCompare), reps: [], failedJobs, pendingApprovals };
}

/* ---------- Admin: assignment settings ---------- */
// PUT /settings/assignment is `extra="forbid"` — the body must be exactly AssignmentSettingsIn
// (no target_name, caps as a {userId: cap} map not a list), so every granular UI action
// (toggle a rule, move it, change one cap) round-trips the whole settings object.
export interface RawRule { id: string; name: string; priority: number; active: boolean; match: Record<string, string>; target_type: 'team' | 'group' | 'user'; target_id: string; target_name?: string | null }
export interface RawCapRow { user_id: string; name: string; team_id: string | null; cap: number; is_default: boolean; active_leads: number }
export interface RawAssignmentSettings { default_team_id: string | null; default_cap: number; rep_lead_hold_minutes: number; rules: RawRule[]; groups: { id: string; name: string; team_id: string | null; member_ids: string[] }[]; caps: RawCapRow[] }

export function mapAssignmentConfig(s: RawAssignmentSettings) {
  return {
    rules: s.rules.map((r) => ({ id: r.id, name: r.name, priority: r.priority, active: r.active, match: { source: r.match.lead_source as Lead['source'] | undefined, industry: r.match.industry }, target: { teamId: r.target_type === 'team' ? r.target_id : undefined, groupId: r.target_type === 'group' ? r.target_id : undefined, userId: r.target_type === 'user' ? r.target_id : undefined } })),
    groups: s.groups.map((g) => ({ id: g.id, name: g.name, teamId: g.team_id ?? '', memberIds: g.member_ids })),
    defaultTeamId: s.default_team_id ?? '',
    caps: s.caps.map((c) => ({ userId: c.user_id, name: c.name, teamId: c.team_id, cap: c.cap, active: true, activeLeads: c.active_leads })),
  };
}

/** Builds the exactly-shaped PUT body from the last GET, so a single-field edit round-trips cleanly. */
export function toRawAssignmentSettings(s: RawAssignmentSettings, reason?: string): Record<string, unknown> {
  return {
    default_team_id: s.default_team_id, default_cap: s.default_cap, rep_lead_hold_minutes: s.rep_lead_hold_minutes,
    rules: s.rules.map((r) => ({ id: r.id, name: r.name, priority: r.priority, active: r.active, match: r.match, target_type: r.target_type, target_id: r.target_id })),
    groups: s.groups, caps: Object.fromEntries(s.caps.map((c) => [c.user_id, c.cap])), reason,
  };
}

export function mapRepProfile(p: RawProfile) {
  const emptyToday: TodayData = { mine: [], theirs: [], stale: [], meetingsToday: [], newLeads: [] };
  return {
    summary: {
      id: p.user.id, name: p.user.name, email: p.user.email, active: p.user.is_active, cap: p.user.cap,
      activeLeads: p.workload.active_leads, overdue: p.workload.follow_ups_overdue, stale: 0, dueToday: p.workload.follow_ups_due_today,
      meetingsWeek: 0, wonMonth: p.outcomes.won, lostMonth: p.outcomes.lost,
      revenueMonth: p.workload.revenue_month_paise, target: p.workload.target_paise ?? 0, lastActivityAt: null,
      byStage: p.workload.pipeline_by_stage.map((s) => ({ key: s.key, label: s.name, count: s.open })),
      generated: p.workload.leads_generated, assigned: p.workload.leads_assigned,
    },
    today: emptyToday, lost: [], recent: [],
  };
}

/* ---------- Admin: product catalog ---------- */
// The backend prices each product as one list/floor price, not a list of named plans — synthesize
// a single plan row so CatalogPage's table (which maps over `plans`) renders instead of crashing.
export interface RawProduct { id: string; name: string; description: string | null; unit: string; list_price_paise: number; floor_price_paise: number; active: boolean; allowed_contract_terms: string[] }
export const mapProduct = (p: RawProduct) => ({
  id: p.id, name: p.name, model: p.unit, description: p.description ?? '',
  plans: [{ name: p.unit, minutes: 0, price: p.list_price_paise / 100, floor: p.floor_price_paise / 100 }],
  terms: p.allowed_contract_terms, oneTimeFee: 0, active: p.active,
});

/* ---------- Phase 6: leaderboard, targets, incentives, personal dashboard, reports, forecast ---------- */
export interface RawLeaderboardRow { user_id: string; name: string; team_id: string | null; team_name: string | null; team_colour: string | null; active_deals: number; won_deals: number; revenue_best_paise: number; revenue_current_paise: number; revenue_projected_paise: number; target_paise: number | null; rank: number | null; is_you: boolean }
export interface RawLeaderboard { month: string; rows: RawLeaderboardRow[] }
export const mapLeaderboard = (l: RawLeaderboard) => ({
  month: l.month,
  rows: l.rows.map((r) => ({
    userId: r.user_id, name: r.name, teamId: r.team_id, teamName: r.team_name, teamColour: r.team_colour,
    activeDeals: r.active_deals, wonDeals: r.won_deals, revenueBestPaise: r.revenue_best_paise,
    revenueCurrentPaise: r.revenue_current_paise, revenueProjectedPaise: r.revenue_projected_paise,
    targetPaise: r.target_paise, rank: r.rank, isYou: r.is_you,
  })),
});

export interface RawIncentive { deal_id: string; user_id: string; rate_bp: number | null; won_value_paise: number; received_paise: number; pending_amount_paise: number; earned_amount_paise: number; expected_total_paise: number | null; status: 'no_rate' | 'pending' | 'earned'; earned_at: string | null }
export const mapIncentive = (i: RawIncentive) => ({
  dealId: i.deal_id, userId: i.user_id, rateBp: i.rate_bp, wonValuePaise: i.won_value_paise,
  receivedPaise: i.received_paise, pendingAmountPaise: i.pending_amount_paise,
  earnedAmountPaise: i.earned_amount_paise, expectedTotalPaise: i.expected_total_paise,
  status: i.status, earnedAt: i.earned_at,
});

export interface RawIncentiveRow {
  deal_id: string; company: string; stage_key: string; won: boolean; rep: { id: string | null; name: string | null }; deal_value_paise: number; won_at: string | null; onboarding_done: boolean;
  incentive: { rate_bp: number | null; status: string; received_paise: number; pending_amount_paise: number; earned_amount_paise: number; expected_total_paise: number | null } | null;
  request: { id: string; type: string; status: string; rate_bp: number; reason: string; requested_by: string | null; decision_reason: string | null; created_at: string; decided_at: string | null } | null;
  approval_id: string | null;
}
export const mapIncentiveRow = (r: RawIncentiveRow): IncentiveRow => ({
  dealId: r.deal_id, company: r.company, stageKey: r.stage_key, won: r.won, rep: r.rep, dealValuePaise: r.deal_value_paise, wonAt: r.won_at, onboardingDone: r.onboarding_done,
  incentive: r.incentive ? { rateBp: r.incentive.rate_bp, status: r.incentive.status, receivedPaise: r.incentive.received_paise, pendingAmountPaise: r.incentive.pending_amount_paise, earnedAmountPaise: r.incentive.earned_amount_paise, expectedTotalPaise: r.incentive.expected_total_paise } : null,
  request: r.request ? { id: r.request.id, type: r.request.type, status: r.request.status, rateBp: r.request.rate_bp, reason: r.request.reason, requestedBy: r.request.requested_by, decisionReason: r.request.decision_reason, createdAt: r.request.created_at, decidedAt: r.request.decided_at } : null,
  approvalId: r.approval_id,
});

export interface RawMissingRate { deal_id: string; user_id: string; won_value_paise: number; won_at: string }
export const mapMissingRate = (m: RawMissingRate) => ({ dealId: m.deal_id, userId: m.user_id, wonValuePaise: m.won_value_paise, wonAt: m.won_at });

export interface RawDashboard { incentives: RawIncentive[] }

export interface RawForecastBucket { key: string; label: string; weighted_paise: number; deal_count: number }
export interface RawForecast { group_by: 'month' | 'team' | 'rep'; buckets: RawForecastBucket[]; total_weighted_paise: number }
export const mapForecast = (f: RawForecast) => ({
  groupBy: f.group_by,
  buckets: f.buckets.map((b) => ({ key: b.key, label: b.label, weightedPaise: b.weighted_paise, dealCount: b.deal_count })),
  totalWeightedPaise: f.total_weighted_paise,
});
