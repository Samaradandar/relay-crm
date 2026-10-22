import { ENV } from '@/config/env';
import { permissionsFor } from '@/auth/permissions';
import { API_URL, ApprovalRequested, getToken, http, setToken, withDetails } from './client';
import {
  mapAccount, mapAccountDetail, mapAccountTimeline, mapActivity, mapApproval, mapAssignmentConfig,
  mapAtRiskCustomer, mapChurnEvent, mapChurnHistoryPage, mapChurnHistorySummary, mapChurnReason,
  mapAuditRow, mapCompanyOverview, mapCustomer, mapDeal, mapDocument, mapDuplicateMatch, mapPrefill, mapProposal, mapRounds, mapStageVisit, mapTemplate, mapIncentiveRow, termsBody, mapFailedJob, mapMe,
  mapNotification, mapNotificationPref, mapPoolRow, mapProduct, mapRepLoad, mapRepProfile,
  mapSubscription, mapSubscriptionChange, mapSubscriptionLifecycle, mapTeam, mapTeamOverview,
  mapToday, mapUser, toRawActivityIn, toRawAssignmentSettings,
  mapLeaderboard, mapIncentive, mapMissingRate, mapForecast, mapCreateLeadResult, toRawLeadIn,
  type RawAccount, type RawAccountDetail, type RawAccountTimeline, type RawActivity, type RawApproval,
  type RawAssignmentSettings, type RawAtRiskCustomer, type RawChurnEvent, type RawChurnHistoryPage,
  type RawAuditRow, type RawPrefill, type RawProposal, type RawRounds, type RawStageVisit, type RawIncentiveRow, type RawTemplate, type RawChurnHistorySummary, type RawChurnReason, type RawCustomer, type RawDeal, type RawDocument,
  type RawDistribution, type RawDuplicateMatch, type RawFailedJob, type RawMe,
  type RawMonitoring, type RawNotificationList, type RawNotificationPref, type RawPoolRow,
  type RawProduct, type RawProfile, type RawRule, type RawSubscription, type RawSubscriptionChange,
  type RawSubscriptionLifecycle, type RawTeam, type RawTeamCompareRow, type RawToday, type RawUser,
  type RawLeaderboard, type RawIncentive, type RawMissingRate, type RawDashboard, type RawForecast
} from './adapters';
import { db, MOCK_PASSWORD } from './mock/db';
import * as L from './mock/leads';
import * as D from './mock/deals';
import * as T from './mock/team';
import * as AD from './mock/admin';
import { ApiError } from './types';
import type {
  Account, AccountDetail, AccountTimeline, AssignmentConfig, AtRiskCustomer, AuditEntry,
  ChurnEvent, ChurnHistoryPage, ChurnHistorySummary, ChurnReason, CompanyOverview, Contact,
  Customer, Product, PipelineVersion, RequestType, RuleInput, NeedsYou, RepProfile,
  Subscription, SubscriptionChange, SubscriptionLifecycle, TeamOverview, Activity, ActivityInput, DealDetail, MyNumbers,
  TodayData, AddedLead, ApprovalRequest, VerifyItem, TimelineEvent, StagePageData, MeetingHeldInput, CsvCounts, CsvRunResult, CsvUpload, CaptureAction, CaptureReview, CaptureSource, Transfer, TransferInput, TransferLookup, ProposalDraft, ProposalFeedback, ProposalPrefill, ProposalRounds, ProposalTemplate, ProposalTerms, StageVisit, IncentiveRow, PipelineSettingsLive, PipelineVersionLive, CreateLeadResult, DuplicateMatch, FailedJob,
  HistoryEntry, Lead, Me, NewLeadInput, Notification, NotificationPref, PoolSummary, RepLoad,
  Team, User, Role,
  Leaderboard, MissingRateRow, Incentive, ReportName, Forecast
} from './types';

/** What a proof form collected. Mock mode uses activityId/fields/file; live mode saves the rest
 * (PATCH fields and fit checklist, decision-maker tag, catalog line, document) before ticking. */
export interface TickProof {
  /** The decision maker: an existing contact (updated) or a new one (added to the company). */
  decisionMaker?: { accountId?: string; contactId?: string; name: string; phone: string; email: string; role?: string };
  activityId?: string; fields?: Record<string, string>; file?: { name: string; status?: string };
  patch?: Record<string, unknown>; fit?: Record<string, boolean>; decisionMakerId?: string; productId?: string;
  document?: { kind: 'proposal' | 'agreement' | 'invoice' | 'advance' | 'prerequisite' | 'technical'; status: string; file?: File };
}

import { LOST_REASONS } from '@/lib/pipeline';
import type { CancelReason, PostponeReason } from '@/lib/meetings';

export type SettingsListKind = 'fit-checklist' | 'lost-reasons';

export interface NewUserInput { name: string; email: string; role: Role; teamId: string | null }

/** Every call the Phase 1 screens make. Later phases add methods here (leads, deals, billing…). */
export interface Api {
  signInWithPassword(email: string, password: string): Promise<Me>;
  signInAs(userId: string): Promise<Me>; // mock/dev only
  signOut(): Promise<void>;
  me(): Promise<Me>;
  users(): Promise<User[]>;
  createUser(input: NewUserInput, reason?: string): Promise<User | { approvalId: string }>;
  teams(): Promise<Team[]>;
  approvals(): Promise<ApprovalRequest[]>;
  decide(id: string, decision: 'approved' | 'rejected', reason?: string): Promise<ApprovalRequest>;
  notifications(): Promise<Notification[]>;
  markRead(ids: string[] | 'all'): Promise<void>;
  notificationPrefs(): Promise<NotificationPref[]>;
  saveNotificationPrefs(prefs: NotificationPref[]): Promise<void>;
  failedJobs(): Promise<FailedJob[]>;
  retryJob(id: string): Promise<void>;
  myHistory(): Promise<HistoryEntry[]>;
  // Local/dev login only (no Cognito configured): lists the seeded accounts you can sign in as.
  devUsers(): Promise<{ email: string; name: string; role: Role }[]>;
  // Phase 2 · Lead Pool
  myLeads(): Promise<{ owned: Lead[]; added: AddedLead[] }>;
  markLeadsSeen(ids: string[]): Promise<void>;
  checkDuplicates(input: Partial<NewLeadInput>): Promise<DuplicateMatch[]>;
  createLead(input: NewLeadInput, opts?: { reason?: string; createAnyway?: boolean }): Promise<CreateLeadResult>;
  /** Cross-team transfers. Live mode only. */
  transfers(): Promise<Transfer[]>;
  transferLookup(q: string): Promise<TransferLookup[]>;
  requestTransfer(input: TransferInput): Promise<void>;
  commentOnTransfer(id: string, comment: string): Promise<void>;
  /** Lead capture (website forms; ambiguous leads wait in a review queue). Live mode only. */
  captureReviews(): Promise<CaptureReview[]>;
  decideCaptureReview(id: string, action: CaptureAction, reason?: string): Promise<void>;
  captureSources(): Promise<CaptureSource[]>;
  createCaptureSource(input: { name: string; allowedOrigins: string[]; defaultLeadSource?: string; defaultNextStep?: string }, reason?: string): Promise<{ id: string; sourceKey: string }>;
  rotateCaptureKey(id: string, reason?: string): Promise<string>;
  testCaptureSource(id: string, payload: Record<string, string>): Promise<string>;
  /** Bulk lead import from a CSV file (Team Leaders and Admins). Live mode only. */
  uploadCsv(file: File, teamId?: string): Promise<CsvUpload>;
  setCsvMapping(importId: string, mapping: Record<string, string>, defaultSource?: string): Promise<CsvCounts>;
  runCsv(importId: string, reason?: string): Promise<CsvRunResult>;
  csvReport(importId: string): Promise<string>;
  /** The lists the team picks from (Admin settings). Live mode only; lostReasons also works in the demo. */
  settingsList(kind: SettingsListKind): Promise<{ id: string; label: string }[]>;
  saveSettingsList(kind: SettingsListKind, items: { id?: string; label: string }[], reason?: string): Promise<{ id: string; label: string }[]>;
  lostReasons(): Promise<string[]>;
  /** The proposal loop. Live mode only: rounds with the customer, the draft that is emailed, the template. */
  proposalRounds(dealId: string): Promise<ProposalRounds>;
  recordProposalFeedback(dealId: string, feedback: ProposalFeedback, changes?: string, nextMeetingAt?: string): Promise<ProposalRounds>;
  proposalDrafts(dealId: string): Promise<ProposalDraft[]>;
  proposalPrefill(dealId: string): Promise<ProposalPrefill>;
  /** Every stage the deal has been in, with its checklist and ticks (live mode). */
  stageHistory(dealId: string): Promise<StageVisit[]>;
  createProposalDraft(dealId: string, draft: { name: string; email: string; terms: ProposalTerms }): Promise<ProposalDraft>;
  saveProposalDraft(proposalId: string, content: { bodyHtml?: string; termsHtml?: string }): Promise<ProposalDraft>;
  sendProposal(proposalId: string, to: { email: string; name: string }): Promise<ProposalDraft>;
  proposalTemplate(): Promise<ProposalTemplate | null>;
  saveProposalTemplate(t: { name: string; body: string; termsHtml: string }, reason?: string): Promise<ProposalTemplate>;
  /** A booked meeting took place / moved to a new date / will not happen. Live mode only. */
  meetingHeld(activityId: string, input: MeetingHeldInput): Promise<void>;
  postponeMeeting(activityId: string, input: { newAt: string; reasonCode: PostponeReason; why: string; attendees?: string }): Promise<void>;
  cancelMeeting(activityId: string, input: { reasonCode: CancelReason; why: string }): Promise<void>;
  /** Decision maker or influencer: changeable at any stage of the deal. */
  setContactType(contactId: string, decisionMaker: boolean, reason?: string): Promise<void>;
  pool(): Promise<Lead[]>;
  teamLoad(): Promise<RepLoad[]>;
  recentAssignments(): Promise<Lead[]>;
  assignFromPool(leadId: string, userId: string, reason?: string): Promise<Lead>;
  /** The Team Leader's decision on a rep's own lead: give it back to the rep who created it,
   * give it to a chosen rep (which may be the Team Leader themselves), or let round robin pick
   * right now instead of waiting out the hold window. */
  decidePoolLead(leadId: string, action: 'give_creator' | 'give_rep' | 'auto', userId?: string, reason?: string): Promise<void>;
  poolSummary(): Promise<PoolSummary>;
  // Phase 3 · New business pipeline (and Phase 5 existing-customer deals, same calls)
  deals(): Promise<Lead[]>;
  deal(id: string): Promise<DealDetail>;
  today(): Promise<TodayData>;
  tickCriterion(dealId: string, key: string, proof: TickProof): Promise<Lead>;
  /** Live mode: a pipeline's versions and its published definition; null in mock mode. */
  pipelineSettings(key: string): Promise<PipelineSettingsLive | null>;
  /** Live mode: save the edited definition as a draft, then publish it. */
  publishPipelineLive(key: string, def: Pick<PipelineVersionLive, 'stages' | 'transitions' | 'criteria'>, reason?: string): Promise<void>;
  rollbackPipelineLive(key: string, versionNo: number, reason?: string): Promise<void>;
  /** Live mode: the backend's stage page (columns, criteria, cell values); null in mock mode. */
  stagePage(pipeline: string, key: string): Promise<StagePageData | null>;
  /** Live mode: add a proposal/agreement/invoice record (with an optional file) without ticking anything. */
  addDocument(dealId: string, doc: NonNullable<TickProof['document']>): Promise<Lead>;
  untickCriterion(dealId: string, key: string, reason: string): Promise<Lead>;
  moveDeal(dealId: string, opts: { to?: string; reason?: string; round2?: boolean }): Promise<Lead>;
  loseDeal(dealId: string, reason: string, notes: string): Promise<Lead>;
  setNextStep(dealId: string, step: { text: string; due: string; lane: 'mine' | 'theirs' }): Promise<Lead>;
  snooze(dealId: string): Promise<Lead>;
  nudge(dealId: string): Promise<Lead>;
  replied(dealId: string): Promise<Lead>;
  logActivity(dealId: string, input: ActivityInput): Promise<Activity>;
  myNumbers(): Promise<MyNumbers>;
  logExport(what: string, rows: number, reason?: string, filters?: Record<string, string>): Promise<void>;
  // Team Leader (team scope); Admins use the same calls across teams
  needsYou(): Promise<NeedsYou>;
  verifyTick(dealId: string, key: string, decision: 'verified' | 'rejected', reason?: string): Promise<Lead>;
  overrideMove(dealId: string, reason: string, to?: string): Promise<Lead>;
  reopenDeal(dealId: string, reason: string): Promise<Lead>;
  reassignDeal(dealId: string, userId: string, reason: string): Promise<Lead>;
  assignTask(dealId: string, step: { text: string; due: string }): Promise<Lead>;
  teamOverview(): Promise<TeamOverview>;
  repProfile(userId: string): Promise<RepProfile>;
  // Phase 6 · Performance: leaderboard, targets, incentives, personal dashboard, reports, forecast
  leaderboard(month?: string, teamId?: string): Promise<Leaderboard>;
  myIncentives(userId: string): Promise<Incentive[]>;
  setTarget(userId: string, month: string, amountPaise: number, reason?: string): Promise<void>;
  missingIncentiveRates(): Promise<MissingRateRow[]>;
  /** The Incentives page: own (rep), team (Team Leader) or all (Admin) deals with their incentive status. */
  incentives(): Promise<IncentiveRow[]>;
  /** Team Leader: ask for a rate on a won deal; an Admin or Super admin approves it. */
  requestIncentive(dealId: string, rateBp: number, reason?: string): Promise<void>;
  setIncentiveRate(dealId: string, rateBp: number, reason?: string): Promise<Incentive>;
  getIncentive(dealId: string): Promise<Incentive>;
  report(name: ReportName, filters?: { dateFrom?: string; dateTo?: string }): Promise<Record<string, unknown>>;
  forecast(groupBy?: 'month' | 'team' | 'rep'): Promise<Forecast>;
  // Admin and Super admin
  companyOverview(): Promise<CompanyOverview>;
  assignmentConfig(): Promise<AssignmentConfig>;
  saveRule(rule: RuleInput, reason?: string): Promise<void>;
  moveRule(id: string, dir: -1 | 1, reason?: string): Promise<void>;
  toggleRule(id: string, reason?: string): Promise<void>;
  setDefaultTeam(teamId: string, reason?: string): Promise<void>;
  setCap(userId: string, cap: number, reason?: string): Promise<{ placed: number }>;
  testRule(sample: { source: string; industry?: string }): Promise<{ ruleName: string; target: string; wouldGoTo: string | null; waiting: string | null }>;
  pipelineVersions(): Promise<PipelineVersion[]>;
  publishPipeline(draft: PipelineVersion['snapshot'], reason?: string): Promise<{ version: number; changes: string[] }>;
  rollbackPipeline(version: number, reason?: string): Promise<{ version: number }>;
  catalog(): Promise<Product[]>;
  saveProduct(p: Product, reason?: string): Promise<void>;
  auditLog(): Promise<AuditEntry[]>;
  deleteDeal(id: string, reason?: string): Promise<void>;
  deletedDeals(): Promise<{ deal: Lead; at: string; by: string; reason: string }[]>;
  restoreDeal(id: string, reason?: string): Promise<void>;
  updateUser(id: string, patch: { role?: Role; teamId?: string | null; active?: boolean }, reason?: string): Promise<User>;
  /** Team administration (Admin, Super admin). Live mode only. */
  createTeam(name: string, reason?: string): Promise<void>;
  resetPassword(userId: string, reason?: string): Promise<void>;
  renameTeam(id: string, name: string, reason?: string): Promise<void>;
  regenerateTeamColour(id: string, reason?: string): Promise<void>;
  moveRep(userId: string, teamId: string, openDeals: 'move' | 'return_to_pool', reason?: string): Promise<void>;
  changeTeamLeader(teamId: string, newLeaderId: string, oldLeaderId: string | null, reason?: string): Promise<void>;
  // Phase 5 · Accounts, customers, subscriptions, churn
  accounts(q?: string): Promise<Account[]>;
  account(id: string): Promise<AccountDetail>;
  accountTimeline(id: string): Promise<AccountTimeline>;
  createRequest(accountId: string, input: { requestType: RequestType; requirement: string; reason?: string }): Promise<{ id: string }>;
  customers(): Promise<Customer[]>;
  atRiskCustomers(): Promise<AtRiskCustomer[]>;
  customerSubscription(customerId: string): Promise<Subscription>;
  subscriptionChanges(subscriptionId: string): Promise<SubscriptionChange[]>;
  confirmSubscriptionChange(changeId: string, reason?: string): Promise<SubscriptionChange>;
  rejectSubscriptionChange(changeId: string, reason?: string): Promise<SubscriptionChange>;
  recordRenewal(subscriptionId: string, outcome: 'renewed' | 'not_renewing', reason?: string): Promise<SubscriptionLifecycle>;
  recordCancellation(subscriptionId: string, action: 'request' | 'cancel' | 'revert', endDate?: string, reason?: string): Promise<SubscriptionLifecycle>;
  recordChurn(customerId: string, churnDate: string, reasonCode: string, notes?: string): Promise<ChurnEvent>;
  reverseChurn(churnEventId: string, reason?: string): Promise<ChurnEvent>;
  churnHistory(filters: { dateFrom?: string; dateTo?: string; ownerId?: string; reason?: string; sort?: 'churn_date' | 'value_lost' | 'time_as_customer'; desc?: boolean; page?: number; pageSize?: number }): Promise<ChurnHistoryPage>;
  churnHistorySummary(breakdown?: 'reason' | 'rep' | 'month'): Promise<ChurnHistorySummary>;
  churnReasons(): Promise<ChurnReason[]>;
  saveChurnReasons(items: { id?: string; label: string; active: boolean; sortOrder: number }[], reason?: string): Promise<ChurnReason[]>;
}

/* ---------- mock: in-browser data, same behaviour rules as the backend ---------- */
const wait = <T,>(v: T, ms = 250) => new Promise<T>((r) => setTimeout(() => r(v), ms));
let currentId: string | null = null;
try { currentId = sessionStorage.getItem('relay.mockUser'); } catch { /* ignore */ }

function toMe(u: User): Me {
  return { ...u, team: db.teams.find((t) => t.id === u.teamId) ?? null, permissions: permissionsFor(u.role) };
}
function requireMe(): Me {
  const u = db.users.find((x) => x.id === currentId);
  if (!u) throw new ApiError(401, 'unauthenticated', 'Please sign in again.');
  if (!u.active) throw new ApiError(403, 'permission_denied', 'This account has been deactivated. Ask an Admin.');
  return toMe(u);
}
function requireReason(me: Me, reason?: string) {
  if (me.role !== 'super_admin' && !reason?.trim()) throw new ApiError(422, 'reason_required', 'Add a reason before saving.');
}
function startSession(u: User): Me {
  if (!u.active) throw new ApiError(403, 'permission_denied', 'This account has been deactivated. Ask an Admin.');
  if (u.role === 'user' && !u.teamId) throw new ApiError(403, 'permission_denied', 'Your account is not in a team yet. Ask your Team Leader.');
  currentId = u.id; u.lastSignInAt = new Date().toISOString();
  try { sessionStorage.setItem('relay.mockUser', u.id); } catch { /* ignore */ }
  return toMe(u);
}

/** Live-only actions: the demo data has no such thing, so they say so instead of pretending. */
const unsupportedInDemo: (...args: never[]) => Promise<unknown> = async () => { throw new ApiError(400, 'unsupported', 'Not available in the demo.'); };

// Phase 3 methods are attached below with Object.assign, so the literal is completed there.
const mockApi = {
  async signInWithPassword(email, password) {
    const u = db.users.find((x) => x.email.toLowerCase() === email.trim().toLowerCase());
    await wait(null, 500);
    if (!u || password !== MOCK_PASSWORD) throw new ApiError(401, 'unauthenticated', 'Email or password is wrong.');
    return startSession(u);
  },
  async signInAs(userId) { const u = db.users.find((x) => x.id === userId); await wait(null, 400); if (!u) throw new ApiError(404, 'not_found', 'No such user.'); return startSession(u); },
  async signOut() { currentId = null; try { sessionStorage.removeItem('relay.mockUser'); } catch { /* ignore */ } },
  async me() { return wait(requireMe(), 120); },
  async users() { const me = requireMe(); return wait(me.role === 'team_leader' ? db.users.filter((u) => u.teamId === me.teamId) : me.role === 'user' ? db.users.filter((u) => u.id === me.id) : [...db.users]); },
  async createUser(input, reason) {
    const me = requireMe();
    if (me.role === 'user') throw new ApiError(403, 'permission_denied', 'Only Admins can add users.');
    if ((input.role === 'admin' || input.role === 'super_admin') && me.role !== 'super_admin') throw new ApiError(403, 'permission_denied', 'Only a Super admin can create Admins and Super admins.');
    if (input.role === 'user' && !input.teamId) throw new ApiError(422, 'invalid', 'A sales rep must be in a team.');
    if (me.role === 'team_leader') {
      requireReason(me, reason);
      const a: ApprovalRequest = { id: 'a' + Date.now(), type: 'user.create', title: `Add ${input.name}`, summary: input.email, effect: 'Creates the account.', requestedBy: me.id, requestedByName: me.name, requestedAt: new Date().toISOString(), reason: reason!, status: 'pending', changes: [{ label: 'Name', from: null, to: input.name }] };
      db.approvals.push(a); return wait({ approvalId: a.id });
    }
    requireReason(me, reason);
    if (db.users.some((u) => u.email === input.email)) throw new ApiError(409, 'conflict', 'Someone already uses this email.');
    const u: User = { id: 'u' + Date.now(), ...input, active: true, leadCap: input.role === 'user' ? 20 : null, lastSignInAt: null, createdAt: new Date().toISOString() };
    db.users.push(u); return wait(u);
  },
  async teams() { requireMe(); return wait([...db.teams]); },
  async approvals() { const me = requireMe(); return wait(db.approvals.filter((a) => me.role === 'admin' || me.role === 'super_admin' || a.requestedBy === me.id || (me.role === 'team_leader' && !!me.teamId && !!a.teamIds?.includes(me.teamId)))); },
  async decide(id, decision, reason) {
    const me = requireMe(); const a = db.approvals.find((x) => x.id === id);
    if (!a) throw new ApiError(404, 'not_found', 'Request not found.');
    if (a.status !== 'pending') throw new ApiError(409, 'conflict', 'Someone already decided this request.');
    if (a.requestedBy === me.id && me.role !== 'super_admin') throw new ApiError(403, 'permission_denied', 'You cannot approve your own request.');
    if (me.role === 'user') throw new ApiError(403, 'permission_denied', 'Sales reps cannot decide requests.');
    if (me.role === 'team_leader' && !(a.routeTo === 'team_leader' && !!me.teamId && a.teamIds?.includes(me.teamId))) throw new ApiError(403, 'permission_denied', 'This request goes to an Admin.');
    requireReason(me, reason);
    Object.assign(a, { status: decision, decidedBy: me.id, decidedByName: me.name, decidedAt: new Date().toISOString(), decisionReason: reason });
    return wait({ ...a });
  },
  async notifications() { const me = requireMe(); const admin = me.role === 'admin' || me.role === 'super_admin'; return wait(db.notifications.filter((n) => (n.userId ? n.userId === me.id : admin || n.audience !== 'admins')).sort((a, b) => b.createdAt.localeCompare(a.createdAt))); },
  async markRead(ids) { const now = new Date().toISOString(); db.notifications.forEach((n) => { if (ids === 'all' || ids.includes(n.id)) n.readAt = n.readAt ?? now; }); await wait(null, 80); },
  async notificationPrefs() { requireMe(); return wait(db.prefs.map((p) => ({ ...p }))); },
  async saveNotificationPrefs(prefs) { prefs.forEach((p) => { const t = db.prefs.find((x) => x.type === p.type); if (t && !t.required) t.email = p.email; }); await wait(null); },
  async failedJobs() { requireMe(); return wait([...db.failedJobs]); },
  async retryJob(id) { db.failedJobs = db.failedJobs.filter((j) => j.id !== id); await wait(null, 400); },
  async myHistory() { requireMe(); return wait([...db.history]); },
  async devUsers() { return wait(db.users.filter((u) => u.active).map((u) => ({ email: u.email, name: u.name, role: u.role }))); },

  /* Phase 2 */
  async myLeads() {
    const me = requireMe();
    const owned = L.leads.filter((l) => l.status === 'open' && l.ownerId === me.id).sort((a, b) => (b.assignedAt ?? '').localeCompare(a.assignedAt ?? ''));
    const added: AddedLead[] = L.leads.filter((l) => l.generatedById === me.id).sort((a, b) => b.createdAt.localeCompare(a.createdAt)).map((l) => ({
      id: l.id, company: l.company, createdAt: l.createdAt, since: l.status === 'pooled' ? l.pooledAt : l.assignedAt ?? l.createdAt,
      state: l.status === 'pooled' ? 'waiting' : l.ownerId === me.id ? 'yours' : 'assigned', ownerName: l.ownerId === me.id ? undefined : l.ownerName,
      teamName: db.teams.find((t) => t.id === l.teamId)?.name, method: l.method
    }));
    return wait({ owned, added });
  },
  async markLeadsSeen(ids) { const me = requireMe(); L.leads.forEach((l) => { if (ids.includes(l.id) && l.ownerId === me.id) l.seenByOwner = true; }); await wait(null, 60); },
  async checkDuplicates(input) { const me = requireMe(); return wait(L.findDuplicates(input, (l) => inScope(me, l)), 180); },
  createTeam: unsupportedInDemo,
  resetPassword: unsupportedInDemo,
  renameTeam: unsupportedInDemo,
  regenerateTeamColour: unsupportedInDemo,
  moveRep: unsupportedInDemo,
  changeTeamLeader: unsupportedInDemo,
  transfers: unsupportedInDemo,
  transferLookup: unsupportedInDemo,
  requestTransfer: unsupportedInDemo,
  commentOnTransfer: unsupportedInDemo,
  captureReviews: unsupportedInDemo,
  decideCaptureReview: unsupportedInDemo,
  captureSources: unsupportedInDemo,
  createCaptureSource: unsupportedInDemo,
  rotateCaptureKey: unsupportedInDemo,
  testCaptureSource: unsupportedInDemo,
  uploadCsv: unsupportedInDemo,
  setCsvMapping: unsupportedInDemo,
  runCsv: unsupportedInDemo,
  csvReport: unsupportedInDemo,
  settingsList: unsupportedInDemo,
  saveSettingsList: unsupportedInDemo,
  async lostReasons() { return [...LOST_REASONS]; },
  proposalRounds: unsupportedInDemo,
  recordProposalFeedback: unsupportedInDemo,
  proposalDrafts: unsupportedInDemo,
  proposalPrefill: unsupportedInDemo,
  stageHistory: unsupportedInDemo,
  createProposalDraft: unsupportedInDemo,
  saveProposalDraft: unsupportedInDemo,
  sendProposal: unsupportedInDemo,
  proposalTemplate: unsupportedInDemo,
  saveProposalTemplate: unsupportedInDemo,
  meetingHeld: unsupportedInDemo,
  postponeMeeting: unsupportedInDemo,
  cancelMeeting: unsupportedInDemo,
  async setContactType(contactId, decisionMaker) {
    for (const l of L.leads) for (const c of l.contacts ?? []) if (c.id === contactId) { c.decisionMaker = decisionMaker; if (l.contacts?.[0]?.id === contactId) l.contactDecisionMaker = decisionMaker; }
    await wait(null, 80);
  },
  async createLead(input, opts = {}) {
    const me = requireMe();
    if (!input.company.trim() || !input.contactName.trim() || !input.nextStep.trim()) throw new ApiError(422, 'invalid', 'Fill in every required field.');
    if (!input.phone?.trim() && !input.email?.trim()) throw new ApiError(422, 'invalid', 'Add a phone number or an email for the contact.');
    if (me.role === 'admin') requireReason(me, opts.reason);
    const dups = L.findDuplicates(input, (l) => inScope(me, l));
    const exact = dups.find((d) => d.kind === 'exact');
    if (exact && me.role !== 'super_admin') throw new ApiError(409, 'duplicate_blocked', exact.inScope ? `${exact.company} already exists. Open the existing lead instead.` : `Company already exists, owner: ${exact.ownerName ?? 'waiting in the Pool'}. Contact your Team Leader.`);
    if (dups.length && !opts.createAnyway) throw new ApiError(409, 'duplicate_warning', 'Similar companies exist. Check them, or create anyway with a reason.');
    if (dups.length) { if (!opts.reason?.trim() && me.role !== 'super_admin') throw new ApiError(422, 'reason_required', 'Add a reason for creating it anyway.');
      if (dups.some((d) => !d.inScope)) db.notifications.unshift({ id: 'n' + Date.now(), audience: 'admins', type: 'lead', title: `${me.name} created ${input.company} despite a similar company`, body: `Matched ${dups[0].company}${dups[0].teamName ? ' in ' + dups[0].teamName : ''}. Reason: ${opts.reason ?? '—'}`, createdAt: new Date().toISOString(), readAt: null }); }
    const lead = L.newLead(input, me); L.leads.push(lead);
    const r = L.runAssignment(lead);
    let result: CreateLeadResult;
    if (r.ok) {
      L.assign(lead, r.owner, r.method, { ruleName: r.ruleName, seenByOwner: r.owner.id === me.id ? false : false });
      if (r.owner.id !== me.id) db.notifications.unshift({ id: 'n' + Date.now(), userId: r.owner.id, type: 'lead', title: `New lead: ${lead.company}`, body: `Assigned to you by ${r.method === 'rule' ? 'rule' : 'round-robin'}.`, createdAt: new Date().toISOString(), readAt: null });
      result = { lead: { ...lead }, outcome: { kind: 'assigned', ownerName: r.owner.name, method: r.method, ruleName: r.ruleName, toYou: r.owner.id === me.id } };
    } else {
      Object.assign(lead, { teamId: r.teamId, waitingReason: r.reason });
      const tl = db.teams.find((t) => t.id === r.teamId)?.teamLeaderId;
      if (tl) db.notifications.unshift({ id: 'n' + Date.now(), userId: tl, type: 'lead', title: `${lead.company} is waiting in the Pool`, body: L.WAITING_LABEL[r.reason] + '.', createdAt: new Date().toISOString(), readAt: null });
      result = { lead: { ...lead }, outcome: { kind: 'waiting', reason: r.reason, teamName: db.teams.find((t) => t.id === r.teamId)?.name ?? 'No team' } };
    }
    return wait(result, 500);
  },
  async pool() {
    const me = requireMe();
    if (me.role === 'user') throw new ApiError(403, 'permission_denied', 'Sales reps do not see the Lead Pool.');
    return wait(L.leads.filter((l) => l.status === 'pooled' && (me.role !== 'team_leader' || l.teamId === me.teamId)).sort((a, b) => a.pooledAt.localeCompare(b.pooledAt)));
  },
  async teamLoad() {
    const me = requireMe();
    if (me.role === 'user') throw new ApiError(403, 'permission_denied', 'Only Team Leaders and Admins see team load.');
    const week = Date.now() - 7 * 86_400_000;
    return wait(db.users.filter((u) => (u.role === 'user' || (u.role === 'team_leader' && u.id === me.id)) && (me.role !== 'team_leader' || u.teamId === me.teamId)).map((u) => {
      const mine = L.leads.filter((l) => l.ownerId === u.id && l.assignedAt);
      const last = mine.map((l) => l.assignedAt!).sort().pop() ?? null;
      return { id: u.id, name: u.id === me.id ? `${u.name} (you)` : u.name, teamId: u.teamId, active: u.active, cap: L.capOf(u), activeLeads: L.activeLeads(u.id), assignedThisWeek: mine.filter((l) => Date.parse(l.assignedAt!) > week).length, lastAssignedAt: last };
    }));
  },
  async recentAssignments() {
    const me = requireMe();
    return wait(L.leads.filter((l) => l.status === 'open' && l.assignedAt && (me.role === 'admin' || me.role === 'super_admin' || (me.role === 'team_leader' ? l.teamId === me.teamId : l.ownerId === me.id))).sort((a, b) => b.assignedAt!.localeCompare(a.assignedAt!)).slice(0, 6));
  },
  async assignFromPool(leadId, userId, reason) {
    const me = requireMe(); const l = L.leads.find((x) => x.id === leadId); const rep = db.users.find((u) => u.id === userId);
    if (!l || !rep) throw new ApiError(404, 'not_found', 'Lead or rep not found.');
    if (l.status !== 'pooled') throw new ApiError(409, 'conflict', 'Someone already assigned this lead.');
    if (me.role === 'user') throw new ApiError(403, 'permission_denied', 'Ask your Team Leader to assign leads.');
    if (me.role === 'team_leader' && (l.teamId !== me.teamId || rep.teamId !== me.teamId)) throw new ApiError(403, 'out_of_scope', 'You can assign only to people in your own team.');
    if (!rep.active) throw new ApiError(422, 'invalid', `${rep.name} is deactivated.`);
    requireReason(me, reason);
    L.assign(l, rep, 'manual', { assignedByName: me.name, overCap: L.atCap(rep) });
    db.notifications.unshift({ id: 'n' + Date.now(), userId: rep.id, type: 'lead', title: `${me.name} assigned you ${l.company}`, body: 'Manual assignment from the Lead Pool.' + (reason ? ` Reason: ${reason}` : ''), createdAt: new Date().toISOString(), readAt: null });
    return wait({ ...l });
  },
  async decidePoolLead(leadId, action, userId, reason) {
    const me = requireMe(); const l = L.leads.find((x) => x.id === leadId);
    if (!l) throw new ApiError(404, 'not_found', 'Lead not found.');
    if (l.status !== 'pooled') throw new ApiError(409, 'conflict', 'This lead is no longer in the Pool.');
    requireReason(me, reason);
    if (action === 'auto') { Object.assign(l, { repHold: false }); return wait(undefined); }
    const targetId = action === 'give_creator' ? l.generatedById : userId;
    const rep = db.users.find((u) => u.id === targetId);
    if (!rep) throw new ApiError(404, 'not_found', 'Rep not found.');
    L.assign(l, rep, 'manual', { assignedByName: me.name, overCap: L.atCap(rep) });
    return wait(undefined);
  },
  async poolSummary() {
    const me = requireMe();
    if (me.role !== 'admin' && me.role !== 'super_admin') throw new ApiError(403, 'permission_denied', 'Admins only.');
    const pooled = L.leads.filter((l) => l.status === 'pooled'); const today = todayIST();
    const reps = db.users.filter((u) => u.role === 'user' && u.active);
    const assignedToday = { rule: 0, round_robin: 0, manual: 0 };
    L.leads.forEach((l) => { if (l.assignedAt && l.method && dateIST(l.assignedAt) === today) assignedToday[l.method]++; });
    const byReason = { no_rule_match: 0, all_reps_at_cap: 0, no_active_reps: 0, awaiting_team_leader: 0 };
    pooled.forEach((l) => { if (l.waitingReason) byReason[l.waitingReason]++; });
    return wait({
      total: pooled.length, waiting: pooled.filter((l) => l.waitingReason).length, oldestWaitingAt: pooled.map((l) => l.pooledAt).sort()[0] ?? null,
      byTeam: db.teams.map((t) => ({ teamId: t.id, teamName: t.name, color: t.color, count: pooled.filter((l) => l.teamId === t.id).length })),
      byReason, assignedToday, duplicatesFlagged: db.notifications.filter((n) => n.type === 'lead' && n.audience === 'admins').length,
      repsAtCap: reps.filter(L.atCap).length, reps: reps.length
    });
  }
} as Api;

/* ---------- Phase 3: pipeline (mock) ---------- */
function scopedDeals(me: Me) {
  return L.leads.filter((d) => d.pipeline && d.ownerId && d.status !== 'pooled' && !AD.isDeleted(d) && (me.role === 'admin' || me.role === 'super_admin' || (me.role === 'team_leader' ? d.teamId === me.teamId : d.ownerId === me.id)));
}
function seeDeal(me: Me, id: string) {
  const d = L.leads.find((x) => x.id === id);
  if (!d || !scopedDeals(me).includes(d)) throw new ApiError(404, 'not_found', 'Deal not found, or it is outside what you can see.');
  return d;
}
const snap = (d: Lead) => ({ ...d, criteria: { ...d.criteria }, fields: { ...d.fields } });
Object.assign(mockApi, {
  async deals() { return wait(scopedDeals(requireMe()).map(snap), 200); },
  async deal(id: string) {
    const me = requireMe(); const d = seeDeal(me, id);
    return wait({ deal: snap(d), activities: D.activities.filter((a) => a.dealId === id).sort((a, b) => b.at.localeCompare(a.at)), events: D.events.filter((e) => e.dealId === id).sort((a, b) => b.at.localeCompare(a.at)) }, 160);
  },
  async today() { return wait(todayFor(requireMe().id), 200); },
  async logExport(what: string, rows: number, reason?: string) { const me = requireMe(); requireReason(me, reason); db.history.unshift({ id: 'hx' + Date.now(), kind: 'note', title: `Exported ${rows} rows · ${what}`, actor: 'You', at: new Date().toISOString(), reason }); await wait(null, 100); },
  async needsYou() { const me = requireMe(); if (me.role === 'user') throw new ApiError(403, 'permission_denied', 'Team Leaders only.'); return wait(T.needsYou(me, L.leads.filter((l) => l.status === 'pooled' && l.teamId === me.teamId))); },
  async verifyTick(dealId: string, key: string, decision: 'verified' | 'rejected', reason?: string) { const me = requireMe(); const d = T.requireTeamDeal(me, dealId); T.verify(me, d, key, decision, reason); return wait(snap(d)); },
  async overrideMove(dealId: string, reason: string, to?: string) { const me = requireMe(); const d = T.requireTeamDeal(me, dealId); T.override(me, d, to, reason); return wait(snap(d), 300); },
  async reopenDeal(dealId: string, reason: string) { const me = requireMe(); const d = T.requireTeamDeal(me, dealId); T.reopen(me, d, reason); return wait(snap(d)); },
  async reassignDeal(dealId: string, userId: string, reason: string) { const me = requireMe(); const d = T.requireTeamDeal(me, dealId); T.reassign(me, d, userId, reason); return wait(snap(d)); },
  async assignTask(dealId: string, step: { text: string; due: string }) { const me = requireMe(); const d = T.requireTeamDeal(me, dealId); T.assignTask(me, d, step); return wait(snap(d)); },
  async teamOverview() { const me = requireMe(); if (me.role !== 'team_leader') throw new ApiError(403, 'permission_denied', 'Team Leaders only.'); return wait(T.overview(me), 200); },
  async repProfile(userId: string) {
    const me = requireMe(); const u = db.users.find((x) => x.id === userId);
    if (!u || (me.role === 'team_leader' && u.teamId !== me.teamId) || (me.role === 'user' && u.id !== me.id)) throw new ApiError(404, 'not_found', 'This person is outside your team.');
    return wait({ ...T.profile(userId), today: todayFor(userId) }, 200);
  },
  async tickCriterion(dealId: string, key: string, proof: Parameters<Api['tickCriterion']>[2]) { const me = requireMe(); const d = D.requireOwn(me, seeDeal(me, dealId)); D.doTick(me, d, key, proof); return wait(snap(d)); },
  async stagePage() { return null; },
  async pipelineSettings() { return null; },
  async publishPipelineLive() { throw new ApiError(400, 'unsupported', 'Not available in the demo.'); },
  async rollbackPipelineLive() { throw new ApiError(400, 'unsupported', 'Not available in the demo.'); },
  // The mock deals keep documents only as proof on a tick; a standalone document is a no-op.
  async addDocument(dealId: string) { const me = requireMe(); return wait(snap(D.requireOwn(me, seeDeal(me, dealId)))); },
  async untickCriterion(dealId: string, key: string, reason: string) { const me = requireMe(); const d = D.requireOwn(me, seeDeal(me, dealId)); D.doUntick(me, d, key, reason); return wait(snap(d)); },
  async moveDeal(dealId: string, opts: Parameters<Api['moveDeal']>[1]) { const me = requireMe(); const d = D.requireOwn(me, seeDeal(me, dealId)); D.doMove(me, d, opts); return wait(snap(d), 300); },
  async loseDeal(dealId: string, reason: string, notes: string) { const me = requireMe(); const d = D.requireOwn(me, seeDeal(me, dealId)); D.doLose(me, d, reason, notes); return wait(snap(d)); },
  async setNextStep(dealId: string, step: Parameters<Api['setNextStep']>[1]) { const me = requireMe(); const d = D.requireOwn(me, seeDeal(me, dealId)); D.doNextStep(me, d, step); return wait(snap(d)); },
  async snooze(dealId: string) { const me = requireMe(); const d = D.requireOwn(me, seeDeal(me, dealId)); D.doSnooze(me, d); return wait(snap(d)); },
  async nudge(dealId: string) { const me = requireMe(); const d = D.requireOwn(me, seeDeal(me, dealId)); D.doNudge(me, d); return wait(snap(d)); },
  async replied(dealId: string) { const me = requireMe(); const d = D.requireOwn(me, seeDeal(me, dealId)); D.doReplied(me, d); return wait(snap(d)); },
  async logActivity(dealId: string, input: ActivityInput) { const me = requireMe(); const d = D.requireOwn(me, seeDeal(me, dealId)); return wait(D.doLog(me, d, input), 300); },
  async myNumbers() { const me = requireMe(); return wait(D.numbersFor(me.id)); },
  async companyOverview() { const me = requireMe(); admin(me); return wait(AD.overview(), 220); },
  async leaderboard(month?: string) {
    const me = requireMe();
    const rows = db.users.filter((u) => u.role === 'user').map((u, i) => ({
      userId: u.id, name: u.name, teamId: u.teamId, teamName: null, teamColour: null,
      activeDeals: 0, wonDeals: 0, revenueBestPaise: 0, revenueCurrentPaise: 0, revenueProjectedPaise: 0,
      targetPaise: null, rank: i + 1, isYou: u.id === me.id,
    }));
    return wait({ month: month ?? new Date().toISOString().slice(0, 7), rows });
  },
  async myIncentives() { return wait([]); },
  async setTarget() { await wait(null); },
  async missingIncentiveRates() { return wait([]); },
  incentives: unsupportedInDemo,
  requestIncentive: unsupportedInDemo,
  async setIncentiveRate(dealId: string, rateBp: number) {
    return wait({ dealId, userId: requireMe().id, rateBp, wonValuePaise: 0, receivedPaise: 0, pendingAmountPaise: 0, earnedAmountPaise: 0, expectedTotalPaise: 0, status: 'pending' as const, earnedAt: null });
  },
  async getIncentive(dealId: string) {
    return wait({ dealId, userId: requireMe().id, rateBp: null, wonValuePaise: 0, receivedPaise: 0, pendingAmountPaise: 0, earnedAmountPaise: 0, expectedTotalPaise: null, status: 'no_rate' as const, earnedAt: null });
  },
  async report() { return wait({}); },
  async forecast(groupBy?: 'month' | 'team' | 'rep') { return wait({ groupBy: groupBy ?? 'month', buckets: [], totalWeightedPaise: 0 }); },
  async assignmentConfig() { admin(requireMe()); return wait(AD.assignmentConfig()); },
  async saveRule(rule: RuleInput, reason?: string) { AD.saveRule(requireMe(), rule, reason); await wait(null); },
  async moveRule(id: string, dir: -1 | 1, reason?: string) { AD.moveRule(requireMe(), id, dir, reason); await wait(null, 80); },
  async toggleRule(id: string, reason?: string) { AD.toggleRule(requireMe(), id, reason); await wait(null, 80); },
  async setDefaultTeam(teamId: string, reason?: string) { AD.setDefaultTeam(requireMe(), teamId, reason); await wait(null); },
  async setCap(userId: string, cap: number, reason?: string) { return wait(AD.setCap(requireMe(), userId, cap, reason)); },
  async testRule(sample: { source: string; industry?: string }) { admin(requireMe()); return wait(AD.testRule(sample), 120); },
  async pipelineVersions() { admin(requireMe()); return wait(JSON.parse(JSON.stringify(AD.versions))); },
  async publishPipeline(draft: PipelineVersion['snapshot'], reason?: string) { return wait(AD.publishPipeline(requireMe(), draft, reason), 400); },
  async rollbackPipeline(version: number, reason?: string) { return wait(AD.rollbackPipeline(requireMe(), version, reason), 400); },
  async catalog() { requireMe(); return wait(JSON.parse(JSON.stringify(AD.catalog))); },
  async saveProduct(p: Product, reason?: string) { AD.saveProduct(requireMe(), p, reason); await wait(null); },
  async auditLog() { const me = requireMe(); admin(me); return wait(AD.auditLog(), 220); },
  async deleteDeal(id: string, reason?: string) { AD.deleteDeal(requireMe(), id, reason); await wait(null); },
  async deletedDeals() { const me = requireMe(); if (me.role === 'user') throw new ApiError(403, 'permission_denied', 'Not for sales reps.'); return wait(AD.deleted.map((x) => ({ ...x, deal: snap(L.leads.find((l) => l.id === x.dealId)!) })).filter((x) => me.role !== 'team_leader' || x.deal.teamId === me.teamId)); },
  async restoreDeal(id: string, reason?: string) { AD.restoreDeal(requireMe(), id, reason); await wait(null); },
  async updateUser(id: string, patch: { role?: Role; teamId?: string | null; active?: boolean }, reason?: string) { return wait(AD.updateUser(requireMe(), id, patch, reason)); }
});
function admin(me: Me) { if (me.role !== 'admin' && me.role !== 'super_admin') throw new ApiError(403, 'permission_denied', 'Admins and Super admins only.'); }

/* ---------- Phase 5: accounts, customers, subscriptions, churn (mock) ---------- */
interface MockAccount extends Account { contacts: Contact[] }
const wonDeals = () => L.leads.filter((d) => d.status === 'won' && d.pipeline === 'new_business');
const mockAccounts: MockAccount[] = [];
function accountFor(d: Lead): MockAccount {
  let a = mockAccounts.find((x) => x.id === `acc-${d.id}`);
  if (!a) { a = { id: `acc-${d.id}`, name: d.company, website: d.website ?? null, domain: null, industry: d.industry ?? null, city: d.city ?? null, accountOwnerId: d.ownerId, contacts: (d.contacts ?? []).map((c) => ({ ...c })) }; mockAccounts.push(a); }
  return a;
}
interface MockCustomer { id: string; accountId: string; status: 'active' | 'churned' }
const mockCustomers: MockCustomer[] = [];
function customerFor(d: Lead): MockCustomer {
  const a = accountFor(d);
  let c = mockCustomers.find((x) => x.accountId === a.id);
  if (!c) { c = { id: `cust-${a.id}`, accountId: a.id, status: 'active' }; mockCustomers.push(c); }
  return c;
}
interface MockSub extends Omit<Subscription, 'customerId'> { customerId: string }
const mockSubs = new Map<string, MockSub>();
function subFor(c: MockCustomer): MockSub {
  let s = mockSubs.get(c.id);
  if (!s) {
    const end = new Date(Date.now() + 1000 * 3600 * 24 * 40).toISOString().slice(0, 10);
    s = { id: `sub-${c.id}`, customerId: c.id, sourceDealId: null, status: 'active', pricingModel: 'saas', packPricePaise: null, packSizeMinutes: null, feePaise: 5_000_00, billingFrequency: 'monthly', termType: 'contract', contractTerm: '1_year', startDate: '2026-01-15', renewalDate: end, contractEndDate: end, paidThroughDate: null, billingContactId: null, confirmedBy: null, confirmedAt: null, version: 1, renewalStatus: 'upcoming', cancellationStatus: 'none', cancellationRequestedAt: null, endedAt: null, endReason: null };
    mockSubs.set(c.id, s);
  }
  return s;
}
const mockChanges = new Map<string, SubscriptionChange[]>();
const mockChurnEvents: ChurnEvent[] = [];
const mockChurnReasons: ChurnReason[] = [
  { id: 'cr-price', label: 'Price', active: true, sortOrder: 0 }, { id: 'cr-comp', label: 'Competitor', active: true, sortOrder: 1 },
  { id: 'cr-unused', label: 'Not using the product', active: true, sortOrder: 2 }, { id: 'cr-results', label: 'Results not as expected', active: true, sortOrder: 3 },
  { id: 'cr-budget', label: 'Budget cuts', active: true, sortOrder: 4 }, { id: 'cr-closed', label: 'Business closed', active: true, sortOrder: 5 },
  { id: 'cr-support', label: 'Support issues', active: true, sortOrder: 6 }, { id: 'cr-other', label: 'Other', active: true, sortOrder: 7 }
];
function requireAccountScope(me: Me, a: MockAccount) {
  if (me.role === 'admin' || me.role === 'super_admin') return;
  const owner = db.users.find((u) => u.id === a.accountOwnerId);
  if (me.role === 'team_leader' && owner?.teamId === me.teamId) return;
  if (a.accountOwnerId === me.id) return;
  throw new ApiError(404, 'not_found', 'Account not found, or it is outside what you can see.');
}
Object.assign(mockApi, {
  async accounts(q?: string) {
    const me = requireMe(); wonDeals().forEach(accountFor);
    const scoped = mockAccounts.filter((a) => me.role === 'admin' || me.role === 'super_admin' || a.accountOwnerId === me.id || (me.role === 'team_leader' && db.users.find((u) => u.id === a.accountOwnerId)?.teamId === me.teamId));
    return wait(q ? scoped.filter((a) => a.name.toLowerCase().includes(q.toLowerCase())) : scoped);
  },
  async account(id: string): Promise<AccountDetail> {
    const me = requireMe(); const a = mockAccounts.find((x) => x.id === id);
    if (!a) throw new ApiError(404, 'not_found', 'Account not found.');
    requireAccountScope(me, a);
    const deals = L.leads.filter((d) => accountFor(d).id === id || `acc-${d.id}` === id);
    return wait({ ...a, deals: deals.map((d) => ({ id: d.id, status: d.status, stageKey: d.stage ?? '', leadSource: d.source, ownerName: d.ownerName ?? null, teamName: db.teams.find((t) => t.id === d.teamId)?.name ?? null })), lockedDeals: [] });
  },
  async accountTimeline(id: string): Promise<AccountTimeline> {
    requireMe();
    return wait({ accountId: id, now: new Date().toISOString(), bands: [], events: [], engagement: [] });
  },
  async createRequest(accountId: string, input: { requestType: RequestType; requirement: string; reason?: string }) {
    const me = requireMe(); const a = mockAccounts.find((x) => x.id === accountId);
    if (!a) throw new ApiError(404, 'not_found', 'Account not found.');
    requireAccountScope(me, a);
    const id = `ec-${Date.now()}`;
    const deal: Lead = { id, company: a.name, website: a.website ?? undefined, industry: a.industry ?? undefined, city: a.city ?? undefined, contactName: a.contacts[0]?.name ?? '', source: 'Referral', nextStep: input.requirement, nextStepDue: todayIST(), generatedById: me.id, generatedByName: me.name, createdAt: new Date().toISOString(), status: 'open', teamId: me.teamId, ownerId: a.accountOwnerId, pooledAt: new Date().toISOString(), pipeline: 'existing_customer', stage: 'request', round: 1 };
    L.leads.push(deal);
    return wait({ id });
  },
  async customers() {
    const me = requireMe(); wonDeals().forEach((d) => customerFor(d));
    const scoped = mockCustomers.filter((c) => { const a = mockAccounts.find((x) => x.id === c.accountId)!; return me.role === 'admin' || me.role === 'super_admin' || a.accountOwnerId === me.id || (me.role === 'team_leader' && db.users.find((u) => u.id === a.accountOwnerId)?.teamId === me.teamId); });
    return wait(scoped.map((c) => { const a = mockAccounts.find((x) => x.id === c.accountId)!; const owner = db.users.find((u) => u.id === a.accountOwnerId); return { id: c.id, accountId: a.id, accountName: a.name, accountOwnerId: a.accountOwnerId ?? '', accountOwnerName: owner?.name ?? null, status: c.status, voicePlatformCustomerId: null, createdAt: new Date().toISOString() }; }));
  },
  async atRiskCustomers() {
    requireMe();
    return wait([]);
  },
  async customerSubscription(customerId: string): Promise<Subscription> {
    requireMe(); const c = mockCustomers.find((x) => x.id === customerId);
    if (!c) throw new ApiError(404, 'not_found', 'No subscription for this customer.');
    return wait({ ...subFor(c) });
  },
  async subscriptionChanges(subscriptionId: string) { requireMe(); return wait(mockChanges.get(subscriptionId) ?? []); },
  async confirmSubscriptionChange(changeId: string, reason?: string) {
    const me = requireMe(); requireReason(me, reason);
    for (const list of mockChanges.values()) { const c = list.find((x) => x.id === changeId); if (c) { c.status = 'confirmed'; c.confirmedBy = me.id; c.confirmedAt = new Date().toISOString(); return wait({ ...c }); } }
    throw new ApiError(404, 'not_found', 'Change not found.');
  },
  async rejectSubscriptionChange(changeId: string, reason?: string) {
    const me = requireMe(); requireReason(me, reason);
    for (const list of mockChanges.values()) { const c = list.find((x) => x.id === changeId); if (c) { c.status = 'rejected'; return wait({ ...c }); } }
    throw new ApiError(404, 'not_found', 'Change not found.');
  },
  async recordRenewal(subscriptionId: string, outcome: 'renewed' | 'not_renewing', reason?: string) {
    const me = requireMe(); requireReason(me, reason);
    const c = mockCustomers.find((x) => `sub-${x.id}` === subscriptionId); if (!c) throw new ApiError(404, 'not_found', 'Subscription not found.');
    const s = subFor(c); s.renewalStatus = outcome;
    return wait({ ...s });
  },
  async recordCancellation(subscriptionId: string, action: 'request' | 'cancel' | 'revert', _endDate?: string, reason?: string) {
    const me = requireMe(); requireReason(me, reason);
    const c = mockCustomers.find((x) => `sub-${x.id}` === subscriptionId); if (!c) throw new ApiError(404, 'not_found', 'Subscription not found.');
    const s = subFor(c);
    if (action === 'request') { s.cancellationStatus = 'requested'; s.cancellationRequestedAt = new Date().toISOString(); }
    else if (action === 'cancel') { s.cancellationStatus = 'cancelled'; s.endedAt = new Date().toISOString(); s.endReason = reason ?? null; }
    else { s.cancellationStatus = 'none'; s.cancellationRequestedAt = null; }
    return wait({ ...s });
  },
  async recordChurn(customerId: string, churnDate: string, reasonCode: string, notes?: string) {
    const me = requireMe(); requireReason(me, notes);
    const c = mockCustomers.find((x) => x.id === customerId); if (!c) throw new ApiError(404, 'not_found', 'Customer not found.');
    c.status = 'churned'; const s = subFor(c); s.endedAt = new Date().toISOString(); s.cancellationStatus = 'cancelled';
    const ev: ChurnEvent = { id: `chev-${Date.now()}`, customerId, churnDate, reason: reasonCode, notes: notes ?? null, monthlyValueLostPaise: s.feePaise ?? 0, subscriptionStart: s.startDate ?? churnDate, timeAsCustomerDays: 90, lastRenewalDate: null, recordedBy: me.id, recordedAt: new Date().toISOString(), returnedDealId: null, reversedAt: null, reversedBy: null };
    mockChurnEvents.unshift(ev);
    return wait({ ...ev });
  },
  async reverseChurn(churnEventId: string, reason?: string) {
    const me = requireMe(); requireReason(me, reason);
    if (me.role !== 'admin' && me.role !== 'super_admin') throw new ApiError(403, 'permission_denied', 'Admins and Super admins only.');
    const ev = mockChurnEvents.find((x) => x.id === churnEventId); if (!ev) throw new ApiError(404, 'not_found', 'Churn record not found.');
    ev.reversedAt = new Date().toISOString(); ev.reversedBy = me.id;
    const c = mockCustomers.find((x) => x.id === ev.customerId); if (c) { c.status = 'active'; const s = subFor(c); s.endedAt = null; s.cancellationStatus = 'none'; }
    return wait({ ...ev });
  },
  async churnHistory(f: { dateFrom?: string; dateTo?: string; ownerId?: string; reason?: string; sort?: string; desc?: boolean; page?: number; pageSize?: number }) {
    requireMe();
    let rows = mockChurnEvents.map((e) => { const a = mockAccounts.find((x) => x.id === mockCustomers.find((c) => c.id === e.customerId)?.accountId); return { ...e, accountId: a?.id ?? '', accountName: a?.name ?? '', accountOwnerName: db.users.find((u) => u.id === a?.accountOwnerId)?.name ?? null, recordedByName: db.users.find((u) => u.id === e.recordedBy)?.name ?? null, returned: false }; });
    if (f.reason) rows = rows.filter((r) => r.reason === f.reason);
    const page = f.page ?? 1; const pageSize = f.pageSize ?? 25;
    return wait({ total: rows.length, page, pageSize, rows: rows.slice((page - 1) * pageSize, page * pageSize) });
  },
  async churnHistorySummary() {
    requireMe();
    return wait({ churnedThisMonth: mockChurnEvents.length, churnedThisQuarter: mockChurnEvents.length, churnedAllTime: mockChurnEvents.length, churnRateThisMonth: null, totalMonthlyValueLostPaise: mockChurnEvents.reduce((s, e) => s + e.monthlyValueLostPaise, 0), topReasons: [], byReason: null, byRep: null, byMonth: null, teamsCompareAvailable: false });
  },
  async churnReasons() { requireMe(); return wait([...mockChurnReasons]); },
  async saveChurnReasons(items: { id?: string; label: string; active: boolean; sortOrder: number }[], reason?: string) {
    const me = requireMe(); if (me.role !== 'admin' && me.role !== 'super_admin') throw new ApiError(403, 'permission_denied', 'Admins and Super admins only.'); requireReason(me, reason);
    for (const it of items) {
      const existing = it.id ? mockChurnReasons.find((r) => r.id === it.id) : undefined;
      if (existing) Object.assign(existing, { label: it.label, active: it.active, sortOrder: it.sortOrder });
      else mockChurnReasons.push({ id: `cr-${Date.now()}-${mockChurnReasons.length}`, label: it.label, active: it.active, sortOrder: it.sortOrder });
    }
    return wait([...mockChurnReasons]);
  }
});

function todayFor(uid: string): TodayData {
  const mine = L.leads.filter((d) => d.pipeline && d.ownerId === uid && d.status !== 'lost' && d.status !== 'pooled');
  const open = mine.filter((d) => d.status === 'open' || (d.status === 'won' && d.stage === 'onboarding'));
  const today = todayIST();
  return {
    mine: open.filter((d) => d.lane !== 'theirs').sort((a, b) => a.nextStepDue.localeCompare(b.nextStepDue)).map(snap),
    theirs: open.filter((d) => d.lane === 'theirs').sort((a, b) => (a.waitingSince ?? '').localeCompare(b.waitingSince ?? '')).map(snap),
    stale: open.filter(D.isStale).map(snap),
    meetingsToday: D.activities.filter((a) => a.kind === 'meeting' && a.meetingAt && dateIST(a.meetingAt) === today && mine.some((d) => d.id === a.dealId)).sort((a, b) => a.meetingAt!.localeCompare(b.meetingAt!)),
    newLeads: open.filter((d) => !d.seenByOwner).map(snap)
  };
}

const dateIST = (iso: string) => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(new Date(iso));
const todayIST = () => dateIST(new Date().toISOString());
function inScope(me: Me, l: Lead) {
  if (me.role === 'admin' || me.role === 'super_admin') return true;
  if (me.role === 'team_leader') return l.teamId === me.teamId;
  return l.ownerId === me.id;
}

/**
 * `http()` turns an HTTP 202 reason-gated response into an `ApprovalRequested` marker instead of
 * throwing, matching the backend contract (see api/app/platform/approvals). Everywhere the typed
 * `Api` interface promises a plain resource, `call()` re-throws that marker as an `ApiError` with
 * code 'approval_requested' so callers can branch on `err.code` the same way they do for any other
 * API error, without every one of these ~50 call sites needing its own `instanceof` check.
 */
async function call<T>(p: Promise<T | ApprovalRequested>): Promise<T> {
  const r = await p;
  if (r instanceof ApprovalRequested) {
    throw new ApiError(202, 'approval_requested', 'This change needs an Admin to approve it first.');
  }
  return r;
}

/**
 * Next-step actions (snooze/nudge/replied) are keyed by the step's own id on the backend
 * (/next-steps/{step_id}/…), not the deal id, so fetch the deal first to find its current step.
 */
async function nextStepAction(dealId: string, action: 'snooze' | 'nudge' | 'replied'): Promise<Lead> {
  const deal = await call(http<RawDeal>(`/deals/${dealId}`));
  if (!deal.next_step) throw new ApiError(409, 'conflict', 'This deal has no next step to act on.');
  await call(http(`/next-steps/${deal.next_step.id}/${action}`, { method: 'POST' }));
  return mapDeal(await call(http<RawDeal>(`/deals/${dealId}`)));
}

/**
 * Saves the parts of a proof that live elsewhere before a tick. A file goes through the signed
 * upload flow (POST /files/upload-url, PUT to storage, POST /files/{id}/complete) first; that needs
 * S3/MinIO, so in the no-Docker local mode it fails with the storage error.
 */
async function saveProof(dealId: string, proof: TickProof): Promise<void> {
  const patch: Record<string, unknown> = { ...(proof.patch ?? {}) };
  if (proof.fit) patch.fit_checklist = proof.fit;
  if (Object.keys(patch).length) await call(http(`/deals/${dealId}`, { method: 'PATCH', body: JSON.stringify(patch) }));
  if (proof.decisionMaker) {
    const dm = proof.decisionMaker; const body = { name: dm.name.trim(), phone: dm.phone.trim(), email: dm.email.trim(), role: dm.role?.trim() || undefined, is_decision_maker: true };
    if (dm.contactId) await call(http(`/contacts/${dm.contactId}`, { method: 'PATCH', body: JSON.stringify(body) }));
    else await call(http('/contacts', { method: 'POST', body: JSON.stringify({ ...body, account_id: dm.accountId }) }));
  }
  if (proof.decisionMakerId) await call(http(`/contacts/${proof.decisionMakerId}`, { method: 'PATCH', body: JSON.stringify({ is_decision_maker: true }) }));
  if (proof.productId) await call(http(`/deals/${dealId}/lines`, { method: 'POST', body: JSON.stringify({ product_id: proof.productId, quantity: 1 }) }));
  if (proof.document) {
    const { kind, status, file } = proof.document;
    let file_id: string | null = null;
    if (file) {
      const up = await call(http<{ file_id: string; upload_url: string; headers: Record<string, string> }>('/files/upload-url', { method: 'POST', body: JSON.stringify({ entity_type: 'deal', entity_id: dealId, filename: file.name, content_type: file.type || 'application/octet-stream', size: file.size }) }));
      const put = await fetch(up.upload_url, { method: 'PUT', headers: up.headers, body: file }).catch(() => null);
      if (!put?.ok) throw new ApiError(503, 'storage_unavailable', 'The file could not be uploaded. File storage (MinIO/S3) is not running.');
      await call(http(`/files/${up.file_id}/complete`, { method: 'POST' }));
      file_id = up.file_id;
    }
    await call(http(`/deals/${dealId}/documents`, { method: 'POST', body: JSON.stringify({ kind, status, file_id }) }));
  }
}

/**
 * Runs a deal mutation and re-fetches the deal afterward instead of trusting the mutation's own
 * response — several of these routes return 204 No Content rather than the updated deal.
 */
async function dealAction(dealId: string, path: string, opts: { body?: unknown; reason?: string } = {}): Promise<Lead> {
  await call(http(path, { method: 'POST', body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined, reason: opts.reason }));
  return mapDeal(await call(http<RawDeal>(`/deals/${dealId}`)));
}

/* ---------- live: FastAPI backend (Phase 1 endpoints) ---------- */
/** Read a whole list that the API serves in pages, so nothing is silently cut off at the page size. */
async function readAll<T>(path: (offset: number, limit: number) => string, pageSize = 500, maxPages = 40): Promise<T[]> {
  const out: T[] = [];
  for (let i = 0; i < maxPages; i++) {
    const rows = await call(http<T[]>(path(i * pageSize, pageSize)));
    out.push(...rows);
    if (rows.length < pageSize) break;
  }
  return out;
}

const liveApi: Api = {
  async signInWithPassword() {
    // The real backend has no password login: production auth is Cognito (signInWithGoogle);
    // local/dev auth is signInAs(email) via /auth/dev/login. See api/app/core/security.py.
    throw new ApiError(400, 'unsupported', 'Password sign-in is not available. Use Google sign-in or a local development account.');
  },
  async signInAs(email) {
    const { token } = await call(http<{ token: string }>('/auth/dev/login', { method: 'POST', body: JSON.stringify({ email }) }));
    setToken(token);
    return mapMe(await call(http<RawMe>('/me')));
  },
  async signOut() { setToken(null); },
  me: async () => mapMe(await call(http<RawMe>('/me'))),
  users: async () => (await call(http<RawUser[]>('/users'))).map(mapUser),
  async createUser(input, reason) {
    const r = await http<RawUser>('/users', { method: 'POST', body: JSON.stringify({ name: input.name, email: input.email, role: input.role, team_id: input.teamId }), reason });
    return r instanceof ApprovalRequested ? { approvalId: r.approvalId } : mapUser(r);
  },
  teams: async () => (await call(http<RawTeam[]>('/teams'))).map(mapTeam),
  approvals: async () => (await readAll<RawApproval>((o, l) => `/approvals?limit=${l}&offset=${o}`)).map(mapApproval),
  decide: async (id, decision, reason) => mapApproval(await call(http<RawApproval>(`/approvals/${id}/decide`, { method: 'POST', body: JSON.stringify({ decision: decision === 'approved' ? 'approve' : 'reject' }), reason }))),
  notifications: async () => (await call(http<RawNotificationList>('/notifications'))).items.map(mapNotification),
  markRead: (ids) => call(http('/notifications', { method: 'PATCH', body: JSON.stringify({ ids: ids === 'all' ? null : ids }) })),
  notificationPrefs: async () => (await call(http<{ items: RawNotificationPref[] }>('/me/notification-preferences'))).items.map(mapNotificationPref),
  saveNotificationPrefs: (prefs) => call(http('/me/notification-preferences', { method: 'PUT', body: JSON.stringify({ preferences: prefs.map((p) => ({ type: p.type, email_enabled: p.email })) }) })),
  failedJobs: async () => (await call(http<RawFailedJob[]>('/admin/failed-jobs'))).map(mapFailedJob),
  retryJob: (id) => call(http(`/admin/failed-jobs/${id}/retry`, { method: 'POST' })),
  // /history needs an (entity_type, entity_id) pair, not a single "me" feed; no per-user history endpoint exists yet.
  myHistory: async () => [],
  devUsers: () => call(http<{ email: string; name: string; role: Role }[]>('/auth/dev/users')),
  // The backend has no single "my leads" endpoint; compose it from /deals (owned) + /leads/waiting (added by me).
  async myLeads() {
    const me = await this.me();
    const [owned, waiting] = await Promise.all([
      readAll<RawDeal>((o, l) => `/deals?limit=${l}&offset=${o}`),
      call(http<{ id: string; company: string; created_at: string; team_name: string | null; waiting_reason: string | null }[]>('/leads/waiting')),
    ]);
    return {
      owned: owned.filter((d) => d.owner?.id === me.id).map(mapDeal),
      added: waiting.map((w) => ({ id: w.id, company: w.company, createdAt: w.created_at, state: 'waiting' as const, teamName: w.team_name ?? undefined, since: w.created_at })),
    };
  },
  // No dedicated "mark seen" endpoint on the backend; new-lead highlighting is client-side only in live mode.
  markLeadsSeen: async () => {},
  async checkDuplicates(input) {
    const r = await call(http<{ block: RawDuplicateMatch[]; warn: RawDuplicateMatch[] }>('/duplicates/check', { method: 'POST', body: JSON.stringify(input) }));
    return [...r.block.map((m) => mapDuplicateMatch(m, 'exact')), ...r.warn.map((m) => mapDuplicateMatch(m, 'similar'))];
  },
  transfers: async () => (await call(http<{ id: string; deal_id: string; company: string | null; direction: 'give' | 'take'; from_team: string | null; to_team: string | null; proposed_owner: string | null; requested_by: string; reason: string; other_tl_comment: string | null; status: string; created_at: string }[]>('/transfers'))).map((t) => ({ id: t.id, dealId: t.deal_id, company: t.company ?? undefined, direction: t.direction, fromTeam: t.from_team ?? undefined, toTeam: t.to_team ?? undefined, proposedOwner: t.proposed_owner ?? undefined, requestedBy: t.requested_by, reason: t.reason, otherTlComment: t.other_tl_comment ?? undefined, status: t.status, createdAt: t.created_at })),
  transferLookup: async (q) => (await call(http<{ deal_id: string; company: string; team_name: string | null }[]>(`/transfers/lookup?q=${encodeURIComponent(q)}`))).map((r) => ({ dealId: r.deal_id, company: r.company, teamName: r.team_name ?? undefined })),
  // a request answers 202: it goes to an Admin to decide, so that is success here, not an error
  requestTransfer: async (i) => { await http('/transfers', { method: 'POST', body: JSON.stringify({ direction: i.direction, deal_id: i.dealId, to_team_id: i.toTeamId ?? null, owner_id: i.ownerId ?? null }), reason: i.reason }); },
  commentOnTransfer: async (id, comment) => { await call(http(`/transfers/${id}/comment`, { method: 'POST', body: JSON.stringify({ comment }) })); },
  captureReviews: async () => (await call(http<{ id: string; company: string | null; source_kind: string; payload_preview: Record<string, unknown>; matched_deal_id: string | null; created_at: string }[]>('/capture/reviews'))).map((r) => ({ id: r.id, company: r.company ?? undefined, sourceKind: r.source_kind, preview: r.payload_preview, matchedDealId: r.matched_deal_id ?? undefined, createdAt: r.created_at })),
  decideCaptureReview: async (id, action, reason) => { await call(http(`/capture/reviews/${id}/decide`, { method: 'POST', body: JSON.stringify({ action }), reason })); },
  captureSources: async () => (await call(http<{ id: string; kind: string; name: string; source_key: string | null; allowed_origins: string[]; default_lead_source: string | null; default_next_step: string | null; active: boolean }[]>('/settings/capture/sources'))).map((s) => ({ id: s.id, kind: s.kind, name: s.name, sourceKey: s.source_key ?? undefined, allowedOrigins: s.allowed_origins, defaultLeadSource: s.default_lead_source ?? undefined, defaultNextStep: s.default_next_step ?? undefined, active: s.active })),
  createCaptureSource: async (i, reason) => { const r = await call(http<{ id: string; source_key: string }>('/settings/capture/sources', { method: 'POST', body: JSON.stringify({ kind: 'website', name: i.name, allowed_origins: i.allowedOrigins, default_lead_source: i.defaultLeadSource || null, default_next_step: i.defaultNextStep || null }), reason })); return { id: r.id, sourceKey: r.source_key }; },
  rotateCaptureKey: async (id, reason) => (await call(http<{ source_key: string }>(`/settings/capture/sources/${id}/rotate-key${reason ? `?reason=${encodeURIComponent(reason)}` : ''}`, { method: 'POST' }))).source_key,
  testCaptureSource: async (id, payload) => (await call(http<{ outcome: string }>(`/settings/capture/sources/${id}/test`, { method: 'POST', body: JSON.stringify(payload) }))).outcome,
  async uploadCsv(file, teamId) {
    // a file goes as a multipart form, not JSON, so it cannot use the shared JSON helper
    const form = new FormData(); form.append('file', file); if (teamId) form.append('default_team_id', teamId);
    const res = await fetch(`${API_URL}/imports`, { method: 'POST', headers: { Authorization: `Bearer ${getToken() ?? ''}` }, body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new ApiError(res.status, data.code ?? 'error', withDetails(data.message ?? res.statusText, data.details));
    return { id: data.id, headers: data.headers, rowCount: data.row_count };
  },
  setCsvMapping: async (id, mapping, defaultSource) => (await call(http<{ counts: CsvCounts }>(`/imports/${id}/mapping`, { method: 'PUT', body: JSON.stringify({ mapping, default_lead_source: defaultSource || null }) }))).counts,
  runCsv: (id, reason) => call(http<CsvRunResult>(`/imports/${id}/run`, { method: 'POST', body: JSON.stringify({}), reason })),
  async csvReport(id) {
    const res = await fetch(`${API_URL}/imports/${id}/report.csv`, { headers: { Authorization: `Bearer ${getToken() ?? ''}` } });
    if (!res.ok) throw new ApiError(res.status, 'error', 'The report could not be downloaded.');
    return res.text();
  },
  settingsList: async (kind) => (await call(http<{ id: string; label: string; active: boolean }[]>(`/settings/${kind}`))).filter((i) => i.active),
  saveSettingsList: async (kind, items, reason) => (await call(http<{ id: string; label: string; active: boolean }[]>(`/settings/${kind}`, { method: 'PUT', body: JSON.stringify({ items }), reason }))).filter((i) => i.active),
  lostReasons: async () => (await call(http<{ label: string; active: boolean }[]>('/settings/lost-reasons')).catch(() => null))?.filter((i) => i.active).map((i) => i.label) ?? [...LOST_REASONS],
  proposalRounds: async (dealId) => mapRounds(await call(http<RawRounds>(`/deals/${dealId}/proposal-rounds`))),
  recordProposalFeedback: async (dealId, feedback, changes, nextMeetingAt) => mapRounds(await call(http<RawRounds>(`/deals/${dealId}/proposal-rounds`, { method: 'POST', body: JSON.stringify({ feedback, changes: changes?.trim() || null, next_meeting_at: nextMeetingAt || null }) }))),
  proposalDrafts: async (dealId) => (await call(http<RawProposal[]>(`/deals/${dealId}/proposals`))).map(mapProposal),
  stageHistory: async (dealId) => (await call(http<RawStageVisit[]>(`/deals/${dealId}/stage-history`))).map(mapStageVisit),
  proposalPrefill: async (dealId) => mapPrefill(await call(http<RawPrefill>(`/deals/${dealId}/proposal-prefill`))),
  createProposalDraft: async (dealId, r) => mapProposal(await call(http<RawProposal>(`/deals/${dealId}/proposals?${new URLSearchParams({ recipient_name: r.name, recipient_email: r.email })}`, { method: 'POST', body: JSON.stringify(termsBody(r.terms)) }))),
  saveProposalDraft: async (id, c) => mapProposal(await call(http<RawProposal>(`/proposals/${id}`, { method: 'PATCH', body: JSON.stringify({ content: { ...(c.bodyHtml !== undefined ? { body_html: c.bodyHtml } : {}), ...(c.termsHtml !== undefined ? { terms_html: c.termsHtml } : {}) } }) }))),
  sendProposal: async (id, to) => mapProposal(await call(http<RawProposal>(`/proposals/${id}/send`, { method: 'POST', body: JSON.stringify({ to: to.email, to_name: to.name }) }))),
  proposalTemplate: async () => { const t = await call(http<RawTemplate | null>('/settings/proposal-template')); return t ? mapTemplate(t) : null; },
  saveProposalTemplate: async (t, reason) => mapTemplate(await call(http<RawTemplate>('/settings/proposal-template', { method: 'PUT', body: JSON.stringify({ name: t.name, body: t.body, terms_html: t.termsHtml }), reason }))),
  meetingHeld: async (id, i) => { await call(http(`/activities/${id}/meeting/held`, { method: 'POST', body: JSON.stringify({
    occurred_at: i.occurredAt ?? null, attendees: i.attendees ? i.attendees.split(',').map((a) => a.trim()).filter(Boolean) : null, discussed: i.discussed,
    requirements: i.requirements || null, objections: i.objections || null, decisions: i.decisions || null, actions: i.actions || null,
    next_step_text: i.nextStep, next_step_due_date: i.followUp, next_step_lane: i.lane === 'theirs' ? 'waiting_on_them' : 'your_move' }) })); },
  postponeMeeting: async (id, i) => { await call(http(`/activities/${id}/meeting/postpone`, { method: 'POST', body: JSON.stringify({
    new_at: i.newAt, reason_code: i.reasonCode, why: i.why, attendees: i.attendees ? i.attendees.split(',').map((a) => a.trim()).filter(Boolean) : null }) })); },
  cancelMeeting: async (id, i) => { await call(http(`/activities/${id}/meeting/cancel`, { method: 'POST', body: JSON.stringify({ reason_code: i.reasonCode, why: i.why }) })); },
  setContactType: async (contactId, decisionMaker, reason) => { await call(http(`/contacts/${contactId}`, { method: 'PATCH', body: JSON.stringify({ is_decision_maker: decisionMaker }), reason })); },
  async createLead(input, opts = {}) {
    const me = await this.me();
    const raw = await call(http<RawDeal & { warning?: string | null; awaiting_team_leader?: boolean }>('/leads', { method: 'POST', body: JSON.stringify(toRawLeadIn(input, opts)), reason: opts.reason }));
    return mapCreateLeadResult(raw, me.id);
  },
  pool: async () => (await call(http<RawPoolRow[]>('/pool'))).map(mapPoolRow),
  teamLoad: async () => (await call(http<RawDistribution>('/pool/distribution'))).reps.map(mapRepLoad),
  // No dedicated "recent assignments" endpoint; derive it from /deals (assigned_at desc, in scope).
  async recentAssignments() {
    const deals = await readAll<RawDeal>((o, l) => `/deals?limit=${l}&offset=${o}`);
    return deals.filter((d) => d.assigned_at).sort((a, b) => (b.assigned_at ?? '').localeCompare(a.assigned_at ?? '')).slice(0, 6).map(mapDeal);
  },
  assignFromPool: async (leadId, userId, reason) => mapDeal(await call(http<RawDeal>(`/pool/${leadId}/assign`, { method: 'POST', body: JSON.stringify({ user_id: userId }), reason }))),
  decidePoolLead: async (leadId, action, userId, reason) => { await call(http(`/pool/${leadId}/decide`, { method: 'POST', body: JSON.stringify({ action, user_id: userId ?? null }), reason })); },
  // No dedicated "pool summary" endpoint; compose it from /pool (rows) + /pool/distribution (rep caps).
  async poolSummary() {
    const [rows, dist, deals] = await Promise.all([call(http<RawPoolRow[]>('/pool')), call(http<RawDistribution>('/pool/distribution')), readAll<RawDeal>((o, l) => `/deals?limit=${l}&offset=${o}`)]);
    // assigned today (IST), by how: from the in-scope deals' own assignment record
    const today = todayIST(); const assignedToday = { rule: 0, round_robin: 0, manual: 0 };
    for (const d of deals) if (d.assigned_at && dateIST(d.assigned_at) === today && d.assignment_method && d.assignment_method in assignedToday) assignedToday[d.assignment_method as keyof typeof assignedToday]++;
    const byTeam = new Map<string, { teamId: string; teamName: string; color: string; count: number }>();
    const byReason: PoolSummary['byReason'] = { no_rule_match: 0, all_reps_at_cap: 0, no_active_reps: 0, awaiting_team_leader: 0 };
    for (const r of rows) {
      if (r.routed_team_id) { const t = byTeam.get(r.routed_team_id) ?? { teamId: r.routed_team_id, teamName: r.routed_team_name ?? '', color: 'brand', count: 0 }; t.count++; byTeam.set(r.routed_team_id, t); }
      if (r.waiting_reason && r.waiting_reason in byReason) byReason[r.waiting_reason as keyof typeof byReason]++;
    }
    return {
      total: dist.pool_size, waiting: rows.filter((r) => r.waiting_reason).length, oldestWaitingAt: rows.map((r) => r.entered_at).sort()[0] ?? null,
      byTeam: [...byTeam.values()], byReason, assignedToday, duplicatesFlagged: 0,
      repsAtCap: dist.reps.filter((r) => r.active_leads >= r.cap).length, reps: dist.reps.length,
    };
  },
  deals: async () => (await readAll<RawDeal>((o, l) => `/deals?limit=${l}&offset=${o}`)).map(mapDeal),
  async deal(id) {
    const [deal, activities, documents] = await Promise.all([
      call(http<RawDeal>(`/deals/${id}`)),
      call(http<RawActivity[]>(`/deals/${id}/activities`)).catch(() => [] as RawActivity[]),
      call(http<RawDocument[]>(`/deals/${id}/documents`)).catch(() => [] as RawDocument[]),
    ]);
    // No per-deal event feed is wired up yet; the activities themselves become the timeline so the
    // "All" and "Activities" tabs are not empty.
    const acts = activities.map(mapActivity);
    const events: TimelineEvent[] = acts.map((a) => ({ id: 'act-' + a.id, dealId: a.dealId, kind: 'activity', title: a.kind, at: a.at, by: a.byName, activityId: a.id }));
    return { deal: { ...mapDeal(deal), documents: documents.map(mapDocument) }, activities: acts, events };
  },
  today: async () => mapToday(await call(http<RawToday>('/today'))),
  // Proof first: save whatever the proof form collected (deal fields, fit checklist, decision-maker
  // tag, a catalog line, a document), then tick. The backend re-checks the proof on the tick itself.
  async tickCriterion(dealId, key, proof) {
    await saveProof(dealId, proof);
    return dealAction(dealId, `/deals/${dealId}/criteria/${encodeURIComponent(key)}/tick`, { body: { proof_ref: proof.activityId ? { activity_id: proof.activityId } : null } });
  },
  async pipelineSettings(key) {
    type Summary = PipelineSettingsLive['versions'][number];
    const versions = await call(http<Summary[]>(`/pipelines/${key}/versions`));
    const published = versions.filter((v) => v.status === 'published').sort((a, b) => b.version_no - a.version_no)[0];
    if (!published) return null;
    return { versions, live: await call(http<PipelineVersionLive>(`/pipelines/${key}/versions/${published.version_no}`)) };
  },
  async publishPipelineLive(key, def, reason) {
    const draft = await call(http<{ version_no: number }>(`/pipelines/${key}/versions`, { method: 'POST', body: JSON.stringify({
      stages: def.stages.map(({ key: k, name, probability_pct, is_entry, is_final, won_on_entry, is_conditional, display_group, entry_checks }) => ({ key: k, name, probability_pct, is_entry, is_final, won_on_entry, is_conditional, display_group, entry_checks })),
      transitions: def.transitions.map(({ from_stage_key, to_stage_key, kind }) => ({ from_stage_key, to_stage_key, kind })),
      criteria: def.criteria.map(({ stage_key, key: k, label, help_text, proof_type, proof_config }) => ({ stage_key, key: k, label, help_text, proof_type, proof_config })),
    }), reason }));
    await call(http(`/pipelines/${key}/versions/${draft.version_no}/publish`, { method: 'POST', body: JSON.stringify({}), reason }));
  },
  async rollbackPipelineLive(key, versionNo, reason) {
    await call(http(`/pipelines/${key}/rollback/${versionNo}`, { method: 'POST', body: JSON.stringify({}), reason }));
  },
  async stagePage(pipeline, key) {
    type Page = { total: number; columns: StagePageData['columns']; criteria?: StagePageData['criteria']; rows: { id: string; extra: Record<string, unknown> }[] };
    const extras: StagePageData['extras'] = {}; let first: Page | null = null;
    for (let page = 1; page <= 20; page++) {
      const r = await call(http<Page>(`/stages/${encodeURIComponent(key)}/deals?pipeline=${pipeline}&page=${page}&page_size=50`));
      first ??= r; r.rows.forEach((x) => { extras[x.id] = x.extra; });
      if (page * 50 >= r.total) break;
    }
    return { columns: first!.columns, criteria: first!.criteria ?? [], extras };
  },
  async addDocument(dealId, doc) {
    await saveProof(dealId, { document: doc });
    return mapDeal(await call(http<RawDeal>(`/deals/${dealId}`)));
  },
  untickCriterion: (dealId, key, reason) => dealAction(dealId, `/deals/${dealId}/criteria/${key}/untick`, { reason }),
  moveDeal: (dealId, opts) => dealAction(dealId, `/deals/${dealId}/move`, { body: { to_stage: opts.to }, reason: opts.reason }),
  loseDeal: (dealId, reason, notes) => dealAction(dealId, `/deals/${dealId}/lose`, { body: { lost_reason: reason, notes }, reason }),
  setNextStep: (dealId, step) => dealAction(dealId, `/deals/${dealId}/next-step`, { body: { text: step.text, due_date: step.due, lane: step.lane === 'theirs' ? 'waiting_on_them' : 'your_move' } }),
  // The backend keys next-step actions by the step's own id, not the deal id (see /next-steps/{step_id}/…).
  snooze: (dealId) => nextStepAction(dealId, 'snooze'),
  nudge: (dealId) => nextStepAction(dealId, 'nudge'),
  replied: (dealId) => nextStepAction(dealId, 'replied'),
  logActivity: async (dealId, input) => mapActivity(await call(http<RawActivity>(`/deals/${dealId}/activities`, { method: 'POST', body: JSON.stringify(toRawActivityIn(input)) }))),
  // Phase 6: GET /me/numbers. 404 for a Team Leader/Admin/Super admin (not a sales rep) -- fall
  // back to the zero strip rather than throwing, same as before this endpoint existed.
  async myNumbers() {
    return call(http<MyNumbers>('/me/numbers')).catch(() => (
      { revenueMonth: 0, target: 0, best: 0, projected: 0, rank: 0, reps: 0, behindNext: null, wonMonth: 0 }
    ));
  },
  logExport: async (what, _rows, reason, filters) => { await call(http('/exports', { method: 'POST', body: JSON.stringify({ entity: /churn/i.test(what) ? 'churn_history' : 'deals', filters: filters ?? {} }), reason })); },
  // No dedicated "needs you" endpoint: compose it. Assign = the Pool in scope; Verify / Proof missing
  // = ticks on in-scope deals someone else owns (criteria come only with GET /deals/{id});
  // Approve = pending approval requests this person did not raise.
  async needsYou() {
    const me = mapMe(await call(http<RawMe>('/me')));
    const [pool, deals, approvals] = await Promise.all([
      call(http<RawPoolRow[]>('/pool')).catch(() => [] as RawPoolRow[]),
      readAll<RawDeal>((o, l) => `/deals?limit=${l}&offset=${o}`),
      call(http<RawApproval[]>('/approvals')).catch(() => [] as RawApproval[]),
    ]);
    const watch = deals.filter((d) => (d.status === 'open' || d.status === 'won') && d.owner && d.owner.id !== me.id).slice(0, 60);
    const details = await Promise.all(watch.map((d) => call(http<RawDeal>(`/deals/${d.id}`)).catch(() => null)));
    const verify: VerifyItem[] = []; const proofMissing: VerifyItem[] = [];
    for (const d of details) {
      for (const c of d?.progress?.criteria ?? []) {
        if (!d || !c.ticked || c.status === 'verified' || c.status === 'overridden') continue;
        const item: VerifyItem = { dealId: d.id, company: d.company, ownerId: d.owner?.id ?? '', ownerName: d.owner?.name ?? '', criterionKey: c.key, label: c.label, stage: d.stage_key ?? '', stageLabel: d.stage_name ?? d.stage_key ?? '', proof: c.ticked_by ? `Ticked by ${c.ticked_by}` : undefined, at: c.ticked_at ?? undefined, pastStage: false };
        (c.proof_missing ? proofMissing : verify).push(item);
      }
    }
    return {
      assign: pool.map(mapPoolRow), verify, proofMissing, transfers: [],
      approvals: approvals.filter((a) => a.status === 'pending' && a.requested_by !== me.id).map(mapApproval),
    };
  },
  verifyTick: (dealId, key, decision, reason) => dealAction(dealId, `/deals/${dealId}/criteria/${encodeURIComponent(key)}/verify`, { body: { decision: decision === 'verified' ? 'verify' : 'reject' }, reason }),
  // No dedicated "override" endpoint; a Team Leader/Admin override is just a forced /move.
  overrideMove: (dealId, reason, to) => dealAction(dealId, `/deals/${dealId}/move`, { body: { to_stage: to, override: true }, reason }),
  reopenDeal: (dealId, reason) => dealAction(dealId, `/deals/${dealId}/reopen`, { reason }),
  reassignDeal: (dealId, userId, reason) => dealAction(dealId, `/deals/${dealId}/reassign`, { body: { user_id: userId }, reason }),
  // No separate "assign task" endpoint; a Team Leader setting a rep's next step uses the same call as setNextStep.
  assignTask: (dealId, step) => dealAction(dealId, `/deals/${dealId}/next-step`, { body: { text: step.text, due_date: step.due } }),
  teamOverview: async () => mapTeamOverview(await call(http<RawMonitoring>('/monitoring'))),
  repProfile: async (userId) => mapRepProfile(await call(http<RawProfile>(`/profiles/${userId}`))),
  async companyOverview() {
    const [teams, jobs, approvalsList] = await Promise.all([
      call(http<RawTeamCompareRow[]>('/teams-compare')),
      call(http<RawFailedJob[]>('/admin/failed-jobs')),
      call(http<RawApproval[]>('/approvals')),
    ]);
    return mapCompanyOverview(teams, jobs.length, approvalsList.filter((a) => a.status === 'pending').length);
  },
  async leaderboard(month, teamId) {
    const p = new URLSearchParams();
    if (month) p.set('month', month);
    if (teamId) p.set('team_id', teamId);
    return mapLeaderboard(await call(http<RawLeaderboard>(`/leaderboard?${p}`)));
  },
  async myIncentives(userId) {
    const d = await call(http<RawDashboard>(`/dashboard/${userId}`));
    return d.incentives.map(mapIncentive);
  },
  setTarget: async (userId, month, amountPaise, reason) => {
    await call(http(`/targets/${userId}/${month}`, { method: 'PUT', body: JSON.stringify({ amount_paise: amountPaise }), reason }));
  },
  incentives: async () => (await call(http<RawIncentiveRow[]>('/incentives'))).map(mapIncentiveRow),
  requestIncentive: async (dealId, rateBp, reason) => { await call(http(`/deals/${dealId}/incentive/request`, { method: 'POST', body: JSON.stringify({ rate_bp: rateBp, reason }), reason })); },
  missingIncentiveRates: async () => (await call(http<RawMissingRate[]>('/incentives/missing-rates'))).map(mapMissingRate),
  setIncentiveRate: async (dealId, rateBp, reason) => mapIncentive(await call(http<RawIncentive>(`/deals/${dealId}/incentive`, { method: 'PUT', body: JSON.stringify({ rate_bp: rateBp }), reason }))),
  getIncentive: async (dealId) => mapIncentive(await call(http<RawIncentive>(`/deals/${dealId}/incentive`))),
  async report(name, filters) {
    const p = new URLSearchParams();
    if (filters?.dateFrom) p.set('date_from', filters.dateFrom);
    if (filters?.dateTo) p.set('date_to', filters.dateTo);
    return call(http<Record<string, unknown>>(`/reports/${name}?${p}`));
  },
  forecast: async (groupBy) => mapForecast(await call(http<RawForecast>(`/forecast${groupBy ? `?group_by=${groupBy}` : ''}`))),
  assignmentConfig: async () => mapAssignmentConfig(await call(http<RawAssignmentSettings>('/settings/assignment'))),
  async saveRule(rule, reason) {
    const s = await call(http<RawAssignmentSettings>('/settings/assignment'));
    const target_type = rule.target.teamId ? 'team' : rule.target.groupId ? 'group' : 'user';
    const target_id = rule.target.teamId ?? rule.target.groupId ?? rule.target.userId ?? '';
    const match: Record<string, string> = {};
    if (rule.match.source) match.lead_source = rule.match.source;
    if (rule.match.industry) match.industry = rule.match.industry;
    const next: RawRule = { id: rule.id ?? crypto.randomUUID(), name: rule.name, priority: rule.id ? s.rules.find((r) => r.id === rule.id)?.priority ?? s.rules.length + 1 : s.rules.length + 1, active: rule.active, match, target_type, target_id };
    s.rules = rule.id ? s.rules.map((r) => (r.id === rule.id ? next : r)) : [...s.rules, next];
    await call(http('/settings/assignment', { method: 'PUT', body: JSON.stringify(toRawAssignmentSettings(s, reason)) }));
  },
  async moveRule(id, dir, reason) {
    const s = await call(http<RawAssignmentSettings>('/settings/assignment'));
    const sorted = [...s.rules].sort((a, b) => a.priority - b.priority);
    const i = sorted.findIndex((r) => r.id === id); const j = i + dir;
    if (i >= 0 && j >= 0 && j < sorted.length) { [sorted[i], sorted[j]] = [sorted[j], sorted[i]]; sorted.forEach((r, idx) => (r.priority = idx + 1)); }
    s.rules = sorted;
    await call(http('/settings/assignment', { method: 'PUT', body: JSON.stringify(toRawAssignmentSettings(s, reason)) }));
  },
  async toggleRule(id, reason) {
    const s = await call(http<RawAssignmentSettings>('/settings/assignment'));
    s.rules = s.rules.map((r) => (r.id === id ? { ...r, active: !r.active } : r));
    await call(http('/settings/assignment', { method: 'PUT', body: JSON.stringify(toRawAssignmentSettings(s, reason)) }));
  },
  async setDefaultTeam(teamId, reason) {
    const s = await call(http<RawAssignmentSettings>('/settings/assignment'));
    await call(http('/settings/assignment', { method: 'PUT', body: JSON.stringify(toRawAssignmentSettings({ ...s, default_team_id: teamId }, reason)) }));
  },
  async setCap(userId, cap, reason) {
    const s = await call(http<RawAssignmentSettings>('/settings/assignment'));
    s.caps = s.caps.map((c) => (c.user_id === userId ? { ...c, cap } : c));
    await call(http('/settings/assignment', { method: 'PUT', body: JSON.stringify(toRawAssignmentSettings(s, reason)) }));
    return { placed: 0 };
  },
  testRule: (sample) => call(http('/settings/assignment/test', { method: 'POST', body: JSON.stringify({ lead_source: sample.source, industry: sample.industry }) })),
  // Real backend versions pipelines per-key (/pipelines/{key}/versions) with a different shape
  // (no flattened snapshot of all pipelines at once) — not wired up yet; fail soft, not hard.
  pipelineVersions: () => call(http<PipelineVersion[]>('/settings/pipelines/versions')).catch(() => []),
  publishPipeline: (draft, reason) => call(http('/settings/pipelines/publish', { method: 'POST', body: JSON.stringify(draft), reason })),
  rollbackPipeline: (version, reason) => call(http(`/settings/pipelines/versions/${version}/rollback`, { method: 'POST', reason })),
  catalog: async () => (await call(http<RawProduct[]>('/products'))).map(mapProduct),
  // PUT /products takes the whole catalog (`extra="forbid"`); round-trip the full list for one edit.
  async saveProduct(p, reason) {
    const current = await call(http<RawProduct[]>('/products'));
    const plan = p.plans[0];
    const next: RawProduct & { pricing_model?: string } = {
      id: p.id, name: p.name, description: p.description || null, unit: p.model,
      list_price_paise: Math.round((plan?.price ?? 0) * 100), floor_price_paise: Math.round((plan?.floor ?? 0) * 100),
      active: p.active, allowed_contract_terms: p.terms,
    };
    const products = current.some((c) => c.id === p.id) ? current.map((c) => (c.id === p.id ? next : c)) : [...current, next];
    await call(http('/products', { method: 'PUT', body: JSON.stringify({ products }), reason }));
  },
  // GET /audit: the org-wide log (Admin, Super admin), newest first.
  auditLog: async () => {
    // newest first, 500 at a time; each page continues from the last row's sequence number
    const rows: RawAuditRow[] = [];
    for (let i = 0; i < 20; i++) {
      const last = rows[rows.length - 1]?.seq;
      const page = await call(http<RawAuditRow[]>(`/audit?limit=500${last !== undefined ? `&before_seq=${last}` : ''}`));
      rows.push(...page);
      if (page.length < 500 || page[page.length - 1]?.seq === undefined) break;
    }
    return rows.map(mapAuditRow);
  },
  deleteDeal: (id, reason) => call(http(`/deals/${id}/delete`, { method: 'POST', reason })),
  deletedDeals: async () => (await call(http<(RawDeal & { deleted_at?: string | null; deleted_reason?: string | null })[]>('/deleted/deals'))).map((d) => ({ deal: mapDeal(d), at: d.deleted_at ?? '', by: '', reason: d.deleted_reason ?? '' })),
  restoreDeal: (id, reason) => call(http(`/deals/${id}/restore`, { method: 'POST', reason })),
  updateUser: async (id, patch, reason) => mapUser(await call(http<RawUser>(`/users/${id}`, { method: 'PATCH', body: JSON.stringify({ role: patch.role, team_id: patch.teamId, is_active: patch.active }), reason }))),
  resetPassword: async (userId, reason) => { await call(http(`/users/${userId}/password-reset`, { method: 'POST', body: JSON.stringify({}), reason })); },
  createTeam: async (name, reason) => { await call(http('/teams', { method: 'POST', body: JSON.stringify({ name }), reason })); },
  renameTeam: async (id, name, reason) => { await call(http(`/teams/${id}`, { method: 'PATCH', body: JSON.stringify({ name }), reason })); },
  regenerateTeamColour: async (id, reason) => { await call(http(`/teams/${id}/colour/regenerate`, { method: 'POST', body: JSON.stringify({}), reason })); },
  moveRep: async (userId, teamId, openDeals, reason) => { await call(http(`/users/${userId}`, { method: 'PATCH', body: JSON.stringify({ team_id: teamId, open_deals: openDeals }), reason })); },
  async changeTeamLeader(teamId, newLeaderId, oldLeaderId, reason) {
    // a team has exactly one Team Leader: the old one steps down to rep first, then the new one is appointed
    if (oldLeaderId) await call(http(`/users/${oldLeaderId}`, { method: 'PATCH', body: JSON.stringify({ role: 'user' }), reason }));
    await call(http(`/users/${newLeaderId}`, { method: 'PATCH', body: JSON.stringify({ role: 'team_leader', team_id: teamId }), reason }));
  },
  // Phase 5
  accounts: async (q) => (await call(http<RawAccount[]>(`/accounts${q ? `?q=${encodeURIComponent(q)}` : ''}`))).map(mapAccount),
  account: async (id) => mapAccountDetail(await call(http<RawAccountDetail>(`/accounts/${id}`))),
  accountTimeline: async (id) => mapAccountTimeline(await call(http<RawAccountTimeline>(`/accounts/${id}/timeline`))),
  createRequest: (accountId, input) => call(http<{ id: string }>(`/accounts/${accountId}/requests`, { method: 'POST', body: JSON.stringify({ request_type: input.requestType, requirement: input.requirement }), reason: input.reason })),
  customers: async () => (await call(http<RawCustomer[]>('/customers'))).map(mapCustomer),
  atRiskCustomers: async () => (await call(http<RawAtRiskCustomer[]>('/customers/at-risk'))).map(mapAtRiskCustomer),
  customerSubscription: async (customerId) => mapSubscription(await call(http<RawSubscription>(`/customers/${customerId}/subscription`))),
  subscriptionChanges: async (subscriptionId) => (await call(http<RawSubscriptionChange[]>(`/subscriptions/${subscriptionId}/changes`))).map(mapSubscriptionChange),
  confirmSubscriptionChange: async (changeId, reason) => mapSubscriptionChange(await call(http<RawSubscriptionChange>(`/subscription-changes/${changeId}/confirm`, { method: 'POST', body: JSON.stringify({ reason }), reason }))),
  rejectSubscriptionChange: async (changeId, reason) => mapSubscriptionChange(await call(http<RawSubscriptionChange>(`/subscription-changes/${changeId}/reject`, { method: 'POST', body: JSON.stringify({ reason }), reason }))),
  recordRenewal: async (subscriptionId, outcome, reason) => mapSubscriptionLifecycle(await call(http<RawSubscriptionLifecycle>(`/subscriptions/${subscriptionId}/renewal`, { method: 'POST', body: JSON.stringify({ outcome, reason }), reason }))),
  recordCancellation: async (subscriptionId, action, endDate, reason) => mapSubscriptionLifecycle(await call(http<RawSubscriptionLifecycle>(`/subscriptions/${subscriptionId}/cancellation`, { method: 'POST', body: JSON.stringify({ action, end_date: endDate, reason }), reason }))),
  recordChurn: async (customerId, churnDate, reasonCode, notes) => mapChurnEvent(await call(http<RawChurnEvent>(`/customers/${customerId}/churn`, { method: 'POST', body: JSON.stringify({ churn_date: churnDate, reason_code: reasonCode, notes }), reason: notes }))),
  reverseChurn: async (churnEventId, reason) => mapChurnEvent(await call(http<RawChurnEvent>(`/churn/${churnEventId}/reverse`, { method: 'POST', body: JSON.stringify({ reason }), reason }))),
  async churnHistory(f) {
    const p = new URLSearchParams();
    if (f.dateFrom) p.set('date_from', f.dateFrom);
    if (f.dateTo) p.set('date_to', f.dateTo);
    if (f.ownerId) p.set('owner_id', f.ownerId);
    if (f.reason) p.set('reason', f.reason);
    if (f.sort) p.set('sort', f.sort);
    if (f.desc != null) p.set('desc', String(f.desc));
    if (f.page) p.set('page', String(f.page));
    if (f.pageSize) p.set('page_size', String(f.pageSize));
    return mapChurnHistoryPage(await call(http<RawChurnHistoryPage>(`/churn-history?${p}`)));
  },
  churnHistorySummary: async (breakdown) => mapChurnHistorySummary(await call(http<RawChurnHistorySummary>(`/churn-history/summary${breakdown ? `?breakdown=${breakdown}` : ''}`))),
  churnReasons: async () => (await call(http<RawChurnReason[]>('/settings/churn-reasons'))).map(mapChurnReason),
  saveChurnReasons: async (items, reason) => (await call(http<RawChurnReason[]>('/settings/churn-reasons', { method: 'PUT', body: JSON.stringify({ items: items.map((i) => ({ id: i.id, label: i.label, active: i.active, sort_order: i.sortOrder })), reason }), reason }))).map(mapChurnReason)
};

export const api: Api = ENV.apiMode === 'live' ? liveApi : mockApi;
export const mockUsers = () => db.users.filter((u) => u.active);
