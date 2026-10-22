import type { ApprovalRequest, Lead, Me, NeedsYou, RepProfile, RepSummary, TeamOverview, VerifyItem } from '../types';
import { ApiError } from '../types';
import { PIPELINES, nextStage, stageOf } from '@/lib/pipeline';
import { db } from './db';
import { leads } from './leads';
import * as D from './deals';

/**
 * Team Leader side of the engine (PRD 4.4 D/E/F, 7, 10): verify ticks, override a gate (never on own
 * deals), reopen Lost, reassign within the team, assign tasks on a rep's Today, and the team overview.
 */
const { seedDeal: deal, seedActivity: act, seedTick: tick, hoursAgo: h, daysFromNow: day } = D;

/* ---------- seed: the rest of Team North ---------- */
function seed() {
  const A = { ownerId: 'u-ankit', generatedById: 'u-rohit', generatedByName: 'Rohit Bansal' };
  const V = { ownerId: 'u-divya', generatedById: 'u-rohit', generatedByName: 'Rohit Bansal' };
  const k1 = deal({ ...A, company: 'Karim’s Kebab House', industry: 'Food & beverage', city: 'Delhi', contactName: 'Salman Karim', contactRole: 'Owner', phone: '+91 98110 33001', source: 'Referral', website: 'karimskebab.in', nextStep: 'Confirm budget with Salman', nextStepDue: day(-2) }, 'prospecting', 9);
  k1.fields = { ...k1.fields, pricingModel: 'Per-minute (managed)', plan: 'Growth 20,000 min', minutes: '15000', expectedClose: day(12), closeReason: 'Ramadan rush planning', budget: '90000', budgetSource: 'Salman on call 5 Oct', decisionMaker: 'Salman Karim' }; k1.value = D.computeValue(k1);
  k1.criteria = { budget: tick('Ankit Verma', '₹90,000 · Salman on call 5 Oct', 30), decision_maker: tick('Ankit Verma', 'Salman Karim', 30), pricing: tick('Ankit Verma', 'Growth 20,000 min · 15000', 50) };
  act(k1, { kind: 'call', discussed: 'Salman confirmed ₹90K/month budget.', nextStep: k1.nextStep, followUp: k1.nextStepDue }, 30);
  k1.lastActivityAt = h(24 * 8);
  const k2 = deal({ ...A, company: 'Paws & Claws Vet Clinics', industry: 'Healthcare', city: 'Gurugram', contactName: 'Dr. Ishaan Rao', contactRole: 'Owner', email: 'ishaan@pawsclaws.in', source: 'LinkedIn', nextStep: 'Run the discovery meeting', nextStepDue: day(0) }, 'qualification', 2);
  k2.criteria = { need: tick('Ankit Verma', 'Pain point written', 20) }; k2.fields = { ...k2.fields, painPoint: 'Missed after-hours emergency calls' };
  const d1 = deal({ ...V, company: 'Sagar Ratna Outlets', industry: 'Food & beverage', city: 'Noida', contactName: 'Rakesh Shetty', contactRole: 'Ops Head', phone: '+91 98101 22002', source: 'Event', nextStep: 'Collect countersigned agreement', nextStepDue: day(1), lane: 'theirs', waitingSince: h(30) }, 'closure', 3);
  d1.fields = { ...d1.fields, pricingModel: 'Per-minute (managed)', plan: 'Scale 50,000 min', finalPrice: '240000', agreementStatus: 'Sent' }; d1.value = D.computeValue(d1);
  // proof removed after the tick → "Proof missing" (PRD 10)
  d1.criteria = { ...d1.criteria, 'agreement_sent@revision': { state: 'proof_missing', proof: 'Agreement file deleted 7 Oct', by: 'Divya Nair', at: h(28), note: 'The agreement file was removed after the tick' } };
  const d2 = deal({ ...V, company: 'Urban Smiles Dental', industry: 'Healthcare', city: 'Delhi', contactName: 'Dr. Pooja Khanna', contactRole: 'Owner', phone: '+91 98990 55441', source: 'Website', nextStep: 'Walk Dr. Khanna through the proposal', nextStepDue: day(-1) }, 'proposal', 6);
  d2.fields = { ...d2.fields, pricingModel: 'Per-minute (managed)', plan: 'Starter 5,000 min', expectedClose: day(9), proposalVersion: 'v2', proposalSent: day(-6), feedback: 'Changes requested' }; d2.value = D.computeValue(d2);
  d2.criteria = { feedback: tick('Divya Nair', 'Changes requested', 25) };
  const w = deal({ ...V, company: 'Green Leaf Organics', industry: 'Retail', city: 'Delhi', contactName: 'Anu Bhalla', contactRole: 'Owner', phone: '+91 98111 90001', source: 'Referral', nextStep: 'Kickoff with store managers', nextStepDue: day(2) }, 'onboarding', 3);
  Object.assign(w, { status: 'won', wonAt: h(24 * 3) }); w.fields = { ...w.fields, plan: 'Starter 5,000 min', finalPrice: '35000' }; w.value = D.computeValue(w);
  const l1 = deal({ ...A, company: 'Fast Fold Laundry', industry: 'Other', city: 'Noida', contactName: 'Kunal Gupta', source: 'Cold outbound', nextStep: '—', nextStepDue: day(-5) }, 'qualification', 10);
  Object.assign(l1, { status: 'lost', lostReason: 'Customer stopped responding', lostNotes: '4 calls, 2 emails, no reply since 20 Sep.', lostFromStage: 'qualification', lostAt: h(24 * 4) });

  // The detailed deals above replace two placeholder leads each, so Ankit and Divya stay exactly at cap (20/20)
  ['u-ankit', 'u-divya'].forEach((uid) => { for (let n = 0; n < 2; n++) { const i = leads.findIndex((l) => l.ownerId === uid && l.status === 'open' && l.contactName && l.stage === 'lead' && !l.fields?.painPoint); if (i >= 0) leads.splice(i, 1); } });

  // The Team Leader's own deals (works them like a rep; cannot override them)
  const R = { ownerId: 'u-rohit', generatedById: 'u-rohit', generatedByName: 'Rohit Bansal' };
  const r1 = deal({ ...R, company: 'Metro Cash Wholesale', industry: 'Retail', city: 'Delhi', contactName: 'Vinod Arora', contactRole: 'CEO', phone: '+91 98100 20020', source: 'Referral', website: 'metrocash.in', nextStep: 'Walk Vinod through the proposal', nextStepDue: day(0) }, 'proposal', 2);
  r1.fields = { ...r1.fields, pricingModel: 'Per-minute (managed)', plan: 'Scale 50,000 min', expectedClose: day(14), proposalVersion: 'v1', proposalSent: day(-2) }; r1.value = D.computeValue(r1);
  deal({ ...R, company: 'Neelkanth Hospitals', industry: 'Healthcare', city: 'Gurugram', contactName: 'Dr. R. Sinha', contactRole: 'Ops Head', email: 'sinha@neelkanth.in', source: 'Event', nextStep: 'Intro call with the ops head', nextStepDue: day(1) }, 'lead', 1);

  // Team-level approvals routed to the Team Leader (PRD 4.5)
  const now = Date.now();
  db.approvals.push(
    { id: 'a6', type: 'deal.value_change', routeTo: 'team_leader', teamIds: ['t-north'], title: 'Ankit: raise Karim’s Kebab House value by 14%', summary: '₹10.8L → ₹12.3L · Prospecting', effect: 'The deal value changes to ₹12.3L. Over 10% needs your approval.', requestedBy: 'u-ankit', requestedByName: 'Ankit Verma', requestedAt: new Date(now - 4 * 3_600_000).toISOString(), reason: 'Salman added a second outlet; volume up to 17,000 minutes.', status: 'pending', changes: [{ label: 'Deal value', from: '₹10.8L', to: '₹12.3L' }, { label: 'Expected minutes', from: '15,000', to: '17,000' }] },
    { id: 'a7', type: 'proposal.discount', routeTo: 'team_leader', teamIds: ['t-north'], title: 'Priya: 8% discount for MediQuick Pharmacy', summary: 'Starter plan · ₹30,000 → ₹27,600 a month', effect: 'Priya can send the proposal at the discounted price.', requestedBy: 'u-priya', requestedByName: 'Priya Sharma', requestedAt: new Date(now - 2 * 3_600_000).toISOString(), reason: 'Competitor quoted ₹26K; Rohan will sign this week at ₹27.6K.', status: 'pending', changes: [{ label: 'Monthly price', from: '₹30,000', to: '₹27,600' }, { label: 'Discount', from: '0%', to: '8%' }] }
  );
}
seed();

/* ---------- helpers ---------- */
const DONE = ['ticked', 'verified', 'overridden'];
export function teamDeals(me: Me) { return leads.filter((d) => d.pipeline && d.ownerId && d.status !== 'pooled' && d.teamId === me.teamId); }
export function requireTeamDeal(me: Me, id: string) {
  const d = leads.find((x) => x.id === id);
  if (!d || (me.role === 'team_leader' && d.teamId !== me.teamId)) throw new ApiError(404, 'not_found', 'Deal not found, or it is outside your team.');
  if (me.role === 'user') throw new ApiError(403, 'permission_denied', 'Only Team Leaders and Admins can do this.');
  return d;
}
const label = (d: Lead, key: string) => { const [ck, sk] = key.split('@'); const st = stageOf(d.pipeline!, sk ?? d.stage!); return { label: st?.criteria.find((c) => c.key === ck)?.label ?? ck, stage: st?.key ?? d.stage!, stageLabel: st?.label ?? '' }; };

function items(me: Me, states: string[]): VerifyItem[] {
  return teamDeals(me).filter((d) => d.status !== 'lost' && d.ownerId !== me.id).flatMap((d) => Object.entries(d.criteria ?? {})
    .filter(([, s]) => states.includes(s.state) && s.by !== 'System')
    .map(([k, s]) => ({ dealId: d.id, company: d.company, ownerId: d.ownerId!, ownerName: d.ownerName!, criterionKey: k, ...label(d, k), proof: s.proof, at: s.at, pastStage: k.includes('@') })))
    .sort((a, b) => (b.at ?? '').localeCompare(a.at ?? ''));
}

export function needsYou(me: Me, pool: Lead[]): NeedsYou {
  const approvals = db.approvals.filter((a) => a.status === 'pending' && a.routeTo === 'team_leader' && !!me.teamId && a.teamIds?.includes(me.teamId) && a.requestedBy !== me.id);
  const transfers = db.approvals.filter((a) => a.type === 'deal.team_transfer' && a.status === 'pending' && !!me.teamId && a.teamIds?.includes(me.teamId));
  return { assign: pool, verify: items(me, ['ticked']), approvals, proofMissing: items(me, ['proof_missing']), transfers };
}

/* ---------- actions ---------- */
export function verify(me: Me, d: Lead, key: string, decision: 'verified' | 'rejected', reason?: string) {
  if (d.ownerId === me.id) throw new ApiError(403, 'permission_denied', 'You cannot verify ticks on your own deals.');
  const s = d.criteria?.[key]; if (!s || s.state !== 'ticked') throw new ApiError(409, 'conflict', 'This tick has changed. Refresh and try again.');
  const { label: lab } = label(d, key);
  if (decision === 'rejected') {
    if (!reason?.trim()) throw new ApiError(422, 'reason_required', 'Say why you are rejecting this tick.');
    d.criteria = { ...d.criteria, [key]: { state: 'rejected', by: me.name, at: new Date().toISOString(), note: reason } };
    D.seedEvent(d.id, 'criterion', `Tick rejected: “${lab}”`, me.name, { reason, ...(key.includes('@') ? { detail: 'The deal has already moved past this stage and is flagged for review.' } : {}) });
    db.notifications.unshift({ id: 'n' + Date.now(), userId: d.ownerId!, type: 'lead', title: `${me.name} rejected “${lab}” on ${d.company}`, body: `Reason: ${reason}`, createdAt: new Date().toISOString(), readAt: null, link: `/app/deals/${d.id}` });
  } else {
    if (me.role === 'admin') { if (!reason?.trim()) throw new ApiError(422, 'reason_required', 'Admins add a reason.'); }
    d.criteria = { ...d.criteria, [key]: { ...s, state: 'verified', note: `Verified by ${me.name}` } };
    D.seedEvent(d.id, 'criterion', `Tick verified: “${lab}”`, me.name, reason ? { reason } : {});
  }
}

export function override(me: Me, d: Lead, to: string | undefined, reason: string) {
  if (d.ownerId === me.id) throw new ApiError(403, 'permission_denied', 'You cannot override a gate on your own deal. Ask an Admin.');
  if (!reason.trim()) throw new ApiError(422, 'reason_required', 'An override needs a written reason.');
  if (d.status !== 'open') throw new ApiError(409, 'conflict', 'Only open deals can be moved.');
  const p = d.pipeline!; const target = to ? stageOf(p, to) : nextStage(p, d.stage!);
  if (!target) throw new ApiError(422, 'invalid', 'No stage to move to.');
  const st = stageOf(p, d.stage!)!;
  const next = { ...d.criteria };
  st.criteria.forEach((c) => { if (!DONE.includes(next[c.key]?.state ?? '') && c.proof.kind !== 'auto') next[c.key] = { state: 'overridden', by: me.name, at: new Date().toISOString(), note: `Overridden by ${me.name}`, proof: 'Override' }; });
  d.criteria = next;
  const from = st.label; const skipped = PIPELINES[p].stages.findIndex((s) => s.key === target.key) - PIPELINES[p].stages.findIndex((s) => s.key === d.stage) > 1;
  const kept = Object.fromEntries(Object.entries(d.criteria).map(([k, v]) => [k.includes('@') ? k : `${k}@${d.stage}`, v]));
  Object.assign(d, { criteria: kept, stage: target.key, stageEnteredAt: new Date().toISOString(), lastActivityAt: new Date().toISOString() });
  if (target.won) Object.assign(d, { status: 'won', wonAt: new Date().toISOString() });
  D.seedEvent(d.id, 'stage', `${skipped ? 'Skipped' : 'Override'}: ${from} → ${target.label}`, `${me.name} (override)`, { reason });
  db.notifications.unshift({ id: 'n' + Date.now(), userId: d.ownerId!, type: 'lead', title: `${me.name} moved ${d.company} to ${target.label}`, body: `Override. Reason: ${reason}`, createdAt: new Date().toISOString(), readAt: null, link: `/app/deals/${d.id}` });
}

export function reopen(me: Me, d: Lead, reason: string) {
  if (d.status !== 'lost') throw new ApiError(409, 'conflict', 'Only Lost deals can be reopened.');
  if (!reason.trim()) throw new ApiError(422, 'reason_required', 'Reopening needs a reason.');
  Object.assign(d, { status: 'open', stage: d.lostFromStage ?? 'lead', stageEnteredAt: new Date().toISOString(), lastActivityAt: new Date().toISOString(), lostReason: undefined, lostNotes: undefined });
  D.seedEvent(d.id, 'stage', `Reopened into ${stageOf(d.pipeline!, d.stage!)?.label}`, me.name, { reason });
}

export function reassign(me: Me, d: Lead, userId: string, reason: string) {
  const to = db.users.find((u) => u.id === userId);
  if (!to || !to.active) throw new ApiError(422, 'invalid', 'Pick an active person.');
  if (me.role === 'team_leader' && to.teamId !== me.teamId) throw new ApiError(403, 'out_of_scope', 'You can reassign only within your team. Use a transfer request for other teams.');
  if (!reason.trim() && me.role !== 'super_admin') throw new ApiError(422, 'reason_required', 'Reassignment always needs a reason.');
  const from = d.ownerName;
  Object.assign(d, { ownerId: to.id, ownerName: to.name, teamId: to.teamId, seenByOwner: false });
  D.seedEvent(d.id, 'assign', `Owner changed ${from} → ${to.name}`, me.name, { reason, detail: 'Open next steps moved to the new owner.' });
  [d.ownerId, db.users.find((u) => u.name === from)?.id].forEach((uid) => uid && db.notifications.unshift({ id: 'n' + Date.now() + uid, userId: uid, type: 'lead', title: `${d.company}: owner changed ${from} → ${to.name}`, body: `By ${me.name}. Reason: ${reason}`, createdAt: new Date().toISOString(), readAt: null, link: `/app/deals/${d.id}` }));
}

export function assignTask(me: Me, d: Lead, step: { text: string; due: string }) {
  if (!step.text.trim() || !step.due) throw new ApiError(422, 'invalid', 'Give the task and a due date.');
  if (step.due < D.todayIST()) throw new ApiError(422, 'invalid', 'The due date cannot be in the past.');
  const prev = d.nextStep;
  Object.assign(d, { nextStep: step.text, nextStepDue: step.due, lane: 'mine', waitingSince: undefined });
  D.seedEvent(d.id, 'edit', `Task assigned by ${me.name}`, me.name, { detail: `${step.text} · due ${new Date(step.due + 'T00:00:00Z').toLocaleDateString('en-IN', { day: 'numeric', month: 'short', timeZone: 'UTC' })} (was: ${prev})` });
  db.notifications.unshift({ id: 'n' + Date.now(), userId: d.ownerId!, type: 'lead', title: `${me.name} gave you a task on ${d.company}`, body: `${step.text} · due ${new Date(step.due + 'T00:00:00Z').toLocaleDateString('en-IN', { day: 'numeric', month: 'short', timeZone: 'UTC' })}`, createdAt: new Date().toISOString(), readAt: null, link: `/app/deals/${d.id}` });
}

/* ---------- overview and profiles (PRD 5, 7) ---------- */
export function summary(uid: string): RepSummary {
  const u = db.users.find((x) => x.id === uid)!; const month = D.todayIST().slice(0, 7); const today = D.todayIST();
  const mine = leads.filter((d) => d.ownerId === uid && d.pipeline);
  const open = mine.filter((d) => d.status === 'open');
  const weekEnd = Date.now() + 7 * 86_400_000;
  const n = D.numbersFor(uid);
  const stages = PIPELINES.new_business.stages.filter((s) => !s.mergedInto && !s.won);
  return {
    id: uid, name: u.name, email: u.email, active: u.active, cap: u.leadCap ?? 0, activeLeads: open.length,
    overdue: open.filter((d) => d.nextStepDue < today).length, dueToday: open.filter((d) => d.nextStepDue === today).length, stale: open.filter(D.isStale).length,
    meetingsWeek: D.activities.filter((a) => a.kind === 'meeting' && a.meetingAt && Date.parse(a.meetingAt) > Date.now() - 86_400_000 && Date.parse(a.meetingAt) < weekEnd && mine.some((d) => d.id === a.dealId)).length,
    wonMonth: mine.filter((d) => d.status === 'won' && d.wonAt?.slice(0, 7) === month).length, lostMonth: mine.filter((d) => d.status === 'lost' && d.lostAt?.slice(0, 7) === month).length,
    revenueMonth: n.revenueMonth, target: n.target, lastActivityAt: open.map((d) => d.lastActivityAt ?? '').sort().pop() || null,
    byStage: stages.map((s) => ({ key: s.key, label: s.short ?? s.label, count: open.filter((d) => d.pipeline === 'new_business' && (d.stage === s.key || (s.key === 'revision' && d.stage === 'revision_2'))).length })),
    generated: leads.filter((d) => d.generatedById === uid).length, assigned: mine.filter((d) => d.assignedAt).length
  };
}

export function overview(me: Me): TeamOverview {
  const reps = db.users.filter((u) => u.role === 'user' && u.teamId === me.teamId).map((u) => summary(u.id));
  const open = teamDeals(me).filter((d) => d.status === 'open');
  return {
    reps, revenueMonth: reps.reduce((s, r) => s + r.revenueMonth, 0), target: reps.reduce((s, r) => s + r.target, 0),
    openValue: open.reduce((s, d) => s + (d.value ?? 0), 0), weighted: open.reduce((s, d) => s + (d.value ?? 0) * (stageOf(d.pipeline!, d.stage!)!.probability / 100), 0),
    overdue: reps.reduce((s, r) => s + r.overdue, 0), stale: reps.reduce((s, r) => s + r.stale, 0), openDeals: open.length
  };
}

export function profile(uid: string): Omit<RepProfile, 'today'> {
  const mine = leads.filter((d) => d.ownerId === uid && d.pipeline);
  return {
    summary: summary(uid),
    lost: mine.filter((d) => d.status === 'lost').map((d) => ({ company: d.company, reason: d.lostReason ?? '', at: d.lostAt ?? '' })),
    recent: D.activities.filter((a) => mine.some((d) => d.id === a.dealId)).sort((a, b) => b.at.localeCompare(a.at)).slice(0, 8).map((a) => ({ ...a, company: mine.find((d) => d.id === a.dealId)!.company }))
  };
}
export type { ApprovalRequest };
