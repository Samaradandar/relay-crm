import type { AuditEntry, CompanyOverview, Lead, Me, Product, PipelineVersion, RuleInput, Role, User } from '../types';
import { ApiError } from '../types';
import { PIPELINES, stageOf, type PipelineDef, type PipelineKey } from '@/lib/pipeline';
import { db } from './db';
import { leads, rules, groups, settings, runAssignment, newLead, type Rule } from './leads';
import * as D from './deals';
import * as T from './team';

/**
 * Admin and Super admin side (PRD 4.4 K/L, 9 config, 12 audit). Admins give a reason on every change;
 * a Super admin's reason is optional. Every change lands in the audit log with old → new values.
 */
const now = () => new Date().toISOString();
const h = D.hoursAgo;
function needReason(me: Me, reason?: string) {
  if (me.role !== 'admin' && me.role !== 'super_admin') throw new ApiError(403, 'permission_denied', 'Admins and Super admins only.');
  if (me.role === 'admin' && !reason?.trim()) throw new ApiError(422, 'reason_required', 'Admins add a reason to every change.');
}

/* ---------- audit (settings changes; deal events are merged in on read) ---------- */
export const audit: AuditEntry[] = [
  { id: 'au1', at: h(75), actor: 'Arjun Rao', role: 'admin', area: 'Assignment', action: 'Lead cap changed', target: 'Priya Sharma', from: '20', to: '25', reason: 'Covering Ankit’s territory' },
  { id: 'au2', at: h(120), actor: 'Vikram Singh', role: 'super_admin', area: 'Pipeline', action: 'Published pipeline v2', target: 'New business', from: 'v1', to: 'v2', reason: 'Prospecting probability 15% → 20%' },
  { id: 'au3', at: h(200), actor: 'Vikram Singh', role: 'super_admin', area: 'Catalog', action: 'Floor price set', target: 'Growth 20,000 min', from: '—', to: '₹95,000', reason: '' },
  { id: 'au4', at: h(27), actor: 'Vikram Singh', role: 'super_admin', area: 'Users', action: 'Moved team', target: 'Divya Nair', from: 'SMB', to: 'Team North', reason: 'SMB accounts consolidated' }
];
function log(me: Me, area: AuditEntry['area'], action: string, target: string, from: string, to: string, reason?: string) {
  audit.unshift({ id: 'au' + Date.now() + Math.random().toString(36).slice(2, 5), at: now(), actor: me.name, role: me.role, area, action, target, from, to, reason: reason ?? '' });
}
export function auditLog(): AuditEntry[] {
  const dealRows: AuditEntry[] = D.events.filter((e) => e.kind !== 'activity').map((e) => { const d = leads.find((l) => l.id === e.dealId); const who = e.by.replace(/ \(override\)$/, ''); return { id: e.id, at: e.at, actor: e.by, role: e.by === 'System' ? 'system' : (db.users.find((u) => u.name === who)?.role ?? 'user'), area: 'Deals', action: e.title, target: d?.company ?? e.dealId, from: '', to: e.detail ?? '', reason: e.reason ?? '', dealId: e.dealId }; });
  return [...audit, ...dealRows].sort((a, b) => b.at.localeCompare(a.at));
}

/* ---------- company overview (PRD 7: Admins see all teams and can compare) ---------- */
export function overview(): CompanyOverview {
  const teams = db.teams.map((t) => {
    const reps = db.users.filter((u) => u.role === 'user' && u.teamId === t.id).map((u) => T.summary(u.id));
    const open = leads.filter((d) => d.pipeline && d.status === 'open' && d.teamId === t.id);
    const tl = db.users.find((u) => u.id === t.teamLeaderId);
    return { id: t.id, name: t.name, color: t.color, leader: tl?.name ?? '—', reps: reps.length, activeReps: reps.filter((r) => r.active).length, revenueMonth: reps.reduce((s, r) => s + r.revenueMonth, 0), target: reps.reduce((s, r) => s + r.target, 0),
      openDeals: open.length, openValue: open.reduce((s, d) => s + (d.value ?? 0), 0), weighted: open.reduce((s, d) => s + (d.value ?? 0) * (stageOf(d.pipeline!, d.stage!)!.probability / 100), 0),
      overdue: reps.reduce((s, r) => s + r.overdue, 0), stale: reps.reduce((s, r) => s + r.stale, 0), atCap: reps.filter((r) => r.active && r.activeLeads >= r.cap).length,
      poolWaiting: leads.filter((l) => l.status === 'pooled' && l.teamId === t.id).length, wonMonth: reps.reduce((s, r) => s + r.wonMonth, 0) };
  });
  const reps = db.users.filter((u) => u.role === 'user').map((u) => ({ ...T.summary(u.id), teamId: u.teamId }));
  return { teams, reps, failedJobs: db.failedJobs.length, pendingApprovals: db.approvals.filter((a) => a.status === 'pending' && a.routeTo !== 'team_leader').length };
}

/* ---------- assignment settings (P2-08) ---------- */
export function assignmentConfig() {
  return {
    rules: [...rules].sort((a, b) => a.priority - b.priority).map((r) => ({ ...r })), groups: groups.map((g) => ({ ...g })), defaultTeamId: settings.defaultTeamId,
    caps: db.users.filter((u) => u.role === 'user').map((u) => ({ userId: u.id, name: u.name, teamId: u.teamId, cap: u.leadCap ?? 0, active: u.active, activeLeads: leads.filter((l) => l.ownerId === u.id && l.status === 'open').length }))
  };
}
const describe = (r: Pick<Rule, 'match' | 'target'>) => `${Object.entries(r.match).map(([k, v]) => `${k}=${v}`).join(' & ') || 'anything'} → ${r.target.groupId ? groups.find((g) => g.id === r.target.groupId)?.name : r.target.userId ? db.users.find((u) => u.id === r.target.userId)?.name : db.teams.find((t) => t.id === r.target.teamId)?.name}`;
export function saveRule(me: Me, input: RuleInput, reason?: string) {
  needReason(me, reason);
  if (!input.name.trim()) throw new ApiError(422, 'invalid', 'Give the rule a name.');
  if (!input.target.teamId && !input.target.groupId && !input.target.userId) throw new ApiError(422, 'invalid', 'Pick where matching leads go.');
  const match = Object.fromEntries(Object.entries(input.match).filter(([, v]) => v)) as Rule['match'];
  if (input.id) { const r = rules.find((x) => x.id === input.id)!; const before = describe(r); Object.assign(r, { name: input.name, match, target: input.target, active: input.active }); log(me, 'Assignment', 'Rule edited', r.name, before, describe(r), reason); return { ...r }; }
  const r: Rule = { id: 'r' + Date.now(), name: input.name, priority: Math.max(0, ...rules.map((x) => x.priority)) + 1, active: input.active, match, target: input.target };
  rules.push(r); log(me, 'Assignment', 'Rule created', r.name, '—', describe(r), reason); retryWaiting(); return { ...r };
}
export function moveRule(me: Me, id: string, dir: -1 | 1, reason?: string) {
  needReason(me, reason);
  const sorted = [...rules].sort((a, b) => a.priority - b.priority); const i = sorted.findIndex((r) => r.id === id); const j = i + dir;
  if (j < 0 || j >= sorted.length) return;
  const a = sorted[i], b = sorted[j]; [a.priority, b.priority] = [b.priority, a.priority];
  log(me, 'Assignment', 'Rule priority changed', a.name, `#${i + 1}`, `#${j + 1}`, reason);
}
export function toggleRule(me: Me, id: string, reason?: string) { needReason(me, reason); const r = rules.find((x) => x.id === id)!; r.active = !r.active; log(me, 'Assignment', r.active ? 'Rule turned on' : 'Rule turned off', r.name, r.active ? 'Off' : 'On', r.active ? 'On' : 'Off', reason); retryWaiting(); }
export function setDefaultTeam(me: Me, teamId: string, reason?: string) { needReason(me, reason); const from = db.teams.find((t) => t.id === settings.defaultTeamId)?.name ?? '—'; settings.defaultTeamId = teamId; log(me, 'Assignment', 'Default team changed', 'Assignment', from, db.teams.find((t) => t.id === teamId)?.name ?? '—', reason); retryWaiting(); }
export function setCap(me: Me, userId: string, cap: number, reason?: string) {
  needReason(me, reason); if (!Number.isFinite(cap) || cap < 0 || cap > 500) throw new ApiError(422, 'invalid', 'A cap is a number from 0 to 500.');
  const u = db.users.find((x) => x.id === userId)!; const from = String(u.leadCap ?? 0); u.leadCap = Math.round(cap); log(me, 'Assignment', 'Lead cap changed', u.name, from, String(u.leadCap), reason); return retryWaiting();
}
/** Read-only: which rule and target a sample lead would hit. Not logged. */
export function testRule(sample: { source: string; industry?: string }) {
  const probe = newLead({ company: 'Test', contactName: 'Test', phone: '0000000000', source: sample.source as Lead['source'], industry: sample.industry, nextStep: 'x', nextStepDue: '2099-01-01' }, db.users[0]);
  const rule = [...rules].filter((r) => r.active).sort((a, b) => a.priority - b.priority).find((r) => Object.entries(r.match).every(([k, v]) => (probe as unknown as Record<string, unknown>)[k] === v));
  const r = runAssignment(probe, true); // dry run: the round-robin pointer does not move
  return { ruleName: rule?.name ?? 'No rule matched → default team', target: rule ? describe(rule).split('→ ')[1] : db.teams.find((t) => t.id === settings.defaultTeamId)?.name ?? '—', wouldGoTo: r.ok ? r.owner.name : null, waiting: r.ok ? null : r.reason };
}
/** A settings change re-runs the waiting leads, oldest first (P2-05 step 6). */
function retryWaiting() {
  let placed = 0;
  leads.filter((l) => l.status === 'pooled').sort((a, b) => a.pooledAt.localeCompare(b.pooledAt)).forEach((l) => { const r = runAssignment(l); if (r.ok) { Object.assign(l, { status: 'open', ownerId: r.owner.id, ownerName: r.owner.name, teamId: r.owner.teamId, assignedAt: now(), method: r.method, ruleName: r.ruleName, waitingReason: undefined, seenByOwner: false, pipeline: 'new_business', stage: 'lead', stageEnteredAt: now(), lane: 'mine', lastActivityAt: now(), criteria: {}, fields: {} }); placed++; } else Object.assign(l, { teamId: r.teamId, waitingReason: r.reason }); });
  return { placed };
}

/* ---------- pipeline configuration (PRD 9): draft → validate → publish a version; rollback ---------- */
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));
export const versions: PipelineVersion[] = [
  { version: 1, publishedAt: h(24 * 30), by: 'Vikram Singh', reason: 'First version from PRD v3.6', snapshot: clone({ new_business: PIPELINES.new_business, existing_customer: PIPELINES.existing_customer }) },
  { version: 2, publishedAt: h(120), by: 'Vikram Singh', reason: 'Prospecting probability 15% → 20%', snapshot: clone({ new_business: PIPELINES.new_business, existing_customer: PIPELINES.existing_customer }) }
];
export function publishPipeline(me: Me, draft: Record<PipelineKey, PipelineDef>, reason?: string) {
  needReason(me, reason);
  for (const p of Object.values(draft)) {
    for (const s of p.stages) {
      if (!s.label.trim()) throw new ApiError(422, 'invalid', 'Every stage needs a name.');
      if (s.probability < 0 || s.probability > 100) throw new ApiError(422, 'invalid', `${s.label}: probability must be 0–100%.`);
      if (!s.won && s.criteria.length === 0) throw new ApiError(422, 'invalid', `${s.label} needs at least one exit criterion.`);
    }
  }
  const changes: string[] = [];
  (Object.keys(draft) as PipelineKey[]).forEach((k) => draft[k].stages.forEach((s) => { const cur = stageOf(k, s.key); if (cur && cur.probability !== s.probability) changes.push(`${s.label} ${cur.probability}% → ${s.probability}%`); if (cur && cur.criteria.length !== s.criteria.length) changes.push(`${s.label}: ${cur.criteria.length} → ${s.criteria.length} criteria`); }));
  (Object.keys(draft) as PipelineKey[]).forEach((k) => { PIPELINES[k].stages.splice(0, PIPELINES[k].stages.length, ...clone(draft[k]).stages); });
  const v = { version: versions.length + 1, publishedAt: now(), by: me.name, reason: reason || changes.join('; ') || 'No changes', snapshot: clone(draft) };
  versions.push(v); log(me, 'Pipeline', `Published pipeline v${v.version}`, 'Both pipelines', `v${v.version - 1}`, `v${v.version}`, reason || changes.join('; '));
  return { version: v.version, changes };
}
export function rollbackPipeline(me: Me, version: number, reason?: string) {
  const v = versions.find((x) => x.version === version); if (!v) throw new ApiError(404, 'not_found', 'No such version.');
  return publishPipeline(me, clone(v.snapshot), reason || `Rollback to v${version}`);
}

/* ---------- product catalog (Feature 8 catalog; floor price) ---------- */
export const catalog: Product[] = [
  { id: 'p1', name: 'Relay Voice Agent · Managed', model: 'Per-minute (managed)', description: 'Voice agent plus cloud and GPU infrastructure, end to end.', plans: [
    { name: 'Starter 5,000 min', minutes: 5000, price: 30000, floor: 26000 }, { name: 'Growth 20,000 min', minutes: 20000, price: 110000, floor: 95000 }, { name: 'Scale 50,000 min', minutes: 50000, price: 250000, floor: 215000 }],
    terms: ['Monthly plan', 'Quarterly contract', 'Yearly contract'], oneTimeFee: 25000, active: true },
  { id: 'p2', name: 'Relay Voice Agent · SaaS', model: 'SaaS (customer infrastructure)', description: 'The software only; the customer runs their own cloud and GPUs.', plans: [
    { name: 'SaaS Monthly', minutes: 0, price: 60000, floor: 52000 }, { name: 'SaaS Quarterly', minutes: 0, price: 55000, floor: 48000 }],
    terms: ['Monthly plan', 'Quarterly plan', 'Yearly contract'], oneTimeFee: 50000, active: true }
];
export function saveProduct(me: Me, p: Product, reason?: string) {
  needReason(me, reason);
  if (!p.name.trim() || !p.plans.length) throw new ApiError(422, 'invalid', 'A product needs a name and at least one plan.');
  const bad = p.plans.find((x) => !x.name.trim() || x.price <= 0 || x.floor > x.price);
  if (bad) throw new ApiError(422, 'invalid', `${bad.name || 'A plan'}: price must be above zero and the floor cannot be above the price.`);
  const i = catalog.findIndex((x) => x.id === p.id);
  if (i >= 0) { const before = catalog[i]; catalog[i] = clone(p); const diffs = p.plans.map((pl) => { const b = before.plans.find((x) => x.name === pl.name); return b && (b.price !== pl.price || b.floor !== pl.floor) ? `${pl.name}: ₹${b.price}/${b.floor} → ₹${pl.price}/${pl.floor}` : null; }).filter(Boolean).join('; ');
    log(me, 'Catalog', 'Product edited', p.name, '', diffs || 'Details updated', reason); }
  else { const n = { ...clone(p), id: 'p' + Date.now() }; catalog.push(n); log(me, 'Catalog', 'Product added', n.name, '—', `${n.plans.length} plans`, reason); }
  // the plan → price table the deals use
  catalog.forEach((c) => c.plans.forEach((pl) => { D.PLAN_PRICE[pl.name] = pl.price; }));
}

/* ---------- soft delete / restore (PRD 14: hidden, never erased) ---------- */
export const deleted: { dealId: string; at: string; by: string; reason: string }[] = [];
export function deleteDeal(me: Me, id: string, reason?: string) {
  if (me.role !== 'admin' && me.role !== 'super_admin' && me.role !== 'team_leader') throw new ApiError(403, 'permission_denied', 'Only Team Leaders and Admins can delete deals.');
  if (me.role !== 'super_admin' && !reason?.trim()) throw new ApiError(422, 'reason_required', 'Deleting needs a reason.');
  const d = leads.find((l) => l.id === id); if (!d) throw new ApiError(404, 'not_found', 'Deal not found.');
  if (me.role === 'team_leader' && d.teamId !== me.teamId) throw new ApiError(403, 'out_of_scope', 'Outside your team.');
  (d as Lead & { deletedAt?: string }).deletedAt = now(); deleted.unshift({ dealId: id, at: now(), by: me.name, reason: reason ?? '' });
  D.seedEvent(id, 'alert', 'Deal deleted (hidden, restorable)', me.name, { reason });
}
export function restoreDeal(me: Me, id: string, reason?: string) {
  if (me.role !== 'super_admin' && !reason?.trim()) throw new ApiError(422, 'reason_required', 'Restoring needs a reason.');
  const d = leads.find((l) => l.id === id) as (Lead & { deletedAt?: string }) | undefined; if (!d) throw new ApiError(404, 'not_found', 'Deal not found.');
  delete d.deletedAt; const i = deleted.findIndex((x) => x.dealId === id); if (i >= 0) deleted.splice(i, 1);
  D.seedEvent(id, 'edit', 'Deal restored', me.name, { reason });
}
export const isDeleted = (d: Lead) => !!(d as Lead & { deletedAt?: string }).deletedAt;

/* ---------- users (PRD 4.4 L) ---------- */
export function updateUser(me: Me, id: string, patch: { role?: Role; teamId?: string | null; active?: boolean }, reason?: string): User {
  needReason(me, reason);
  const u = db.users.find((x) => x.id === id); if (!u) throw new ApiError(404, 'not_found', 'No such user.');
  const touchesAdmins = u.role === 'admin' || u.role === 'super_admin' || patch.role === 'admin' || patch.role === 'super_admin';
  if (touchesAdmins && me.role !== 'super_admin') throw new ApiError(403, 'permission_denied', 'Only a Super admin can create, promote or remove Admins and Super admins.');
  if (u.id === me.id && (patch.active === false || (patch.role && patch.role !== u.role))) throw new ApiError(403, 'permission_denied', 'You cannot change your own role or deactivate yourself.');
  const supers = db.users.filter((x) => x.role === 'super_admin' && x.active);
  if (u.role === 'super_admin' && supers.length === 1 && (patch.active === false || (patch.role && patch.role !== 'super_admin'))) throw new ApiError(409, 'conflict', 'This is the last active Super admin. Add another first.');
  const role = patch.role ?? u.role; const teamId = role === 'admin' || role === 'super_admin' ? null : patch.teamId !== undefined ? patch.teamId : u.teamId;
  if (role === 'user' && !teamId) throw new ApiError(422, 'invalid', 'A sales rep must be in a team.');
  const before = `${u.role} · ${db.teams.find((t) => t.id === u.teamId)?.name ?? 'no team'} · ${u.active ? 'active' : 'inactive'}`;
  Object.assign(u, { role, teamId, active: patch.active ?? u.active, leadCap: role === 'user' ? u.leadCap ?? 20 : u.leadCap });
  db.teams.forEach((t) => { t.memberIds = t.memberIds.filter((m) => m !== u.id); if (t.id === teamId) t.memberIds.push(u.id); });
  log(me, 'Users', patch.active === false ? 'Deactivated' : patch.active === true ? 'Reactivated' : 'Role or team changed', u.name, before, `${u.role} · ${db.teams.find((t) => t.id === u.teamId)?.name ?? 'no team'} · ${u.active ? 'active' : 'inactive'}`, reason);
  if (patch.active === false) retryWaiting();
  return { ...u };
}
export type { Rule };
