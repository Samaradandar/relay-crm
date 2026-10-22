import type { AssignmentMethod, DuplicateMatch, Lead, LeadSource, NewLeadInput, User, WaitingReason } from '../types';
import { db } from './db';
export { WAITING_LABEL } from '@/lib/leads';

/**
 * Phase 2 sample data and an in-browser copy of the assignment engine (PRD 6), so every home page
 * behaves like the backend: rules first, then round-robin among active sales reps under their cap.
 */
const h = (hoursAgo: number) => new Date(Date.now() - hoursAgo * 3_600_000).toISOString();
const day = (daysFromNow: number) => new Date(Date.now() + daysFromNow * 86_400_000).toISOString().slice(0, 10);

export interface Rule { id: string; name: string; priority: number; active: boolean; match: Partial<{ source: LeadSource; industry: string }>; target: { teamId?: string; groupId?: string; userId?: string } }
export const groups = [{ id: 'g-fnb', name: 'North F&B group', teamId: 't-north', memberIds: ['u-ankit', 'u-divya'] }];
export const rules: Rule[] = [
  { id: 'r1', name: 'Referral · Food & beverage → North F&B group', priority: 1, active: true, match: { source: 'Referral', industry: 'Food & beverage' }, target: { groupId: 'g-fnb' } },
  { id: 'r2', name: 'Events → Enterprise', priority: 2, active: true, match: { source: 'Event' }, target: { teamId: 't-ent' } },
  { id: 'r3', name: 'Inbound calls → Team South', priority: 3, active: true, match: { source: 'Inbound call' }, target: { teamId: 't-south' } }
];
export const settings = { defaultTeamId: 't-north' };
const pointers: Record<string, string | undefined> = {};


const userName = (id: string | null | undefined) => db.users.find((u) => u.id === id)?.name;
const teamOf = (id: string | null | undefined) => db.teams.find((t) => t.id === id);

/* ---------- seed ---------- */
let seq = 100;
function lead(p: Partial<Lead> & Pick<Lead, 'company' | 'contactName' | 'source'>): Lead {
  const owner = p.ownerId ?? null;
  return {
    id: 'd' + seq++, nextStep: 'Intro call', nextStepDue: day(2), generatedById: 'u-rohit', generatedByName: 'Rohit Bansal',
    createdAt: h(30), pooledAt: h(30), status: owner ? 'open' : 'pooled', teamId: owner ? db.users.find((u) => u.id === owner)?.teamId ?? null : null,
    ownerName: userName(owner), seenByOwner: true, ...p, ownerId: owner,
    // pipeline defaults: an assigned lead sits in the Lead stage, my move
    pipeline: p.pipeline ?? 'new_business', stage: p.stage ?? (owner ? 'lead' : undefined), stageEnteredAt: p.stageEnteredAt ?? p.assignedAt,
    lane: p.lane ?? 'mine', lastActivityAt: p.lastActivityAt ?? p.assignedAt, criteria: p.criteria ?? {}, fields: p.fields ?? {},
    contacts: p.contacts ?? [{ id: 'c' + seq, name: p.contactName, role: p.contactRole, phone: p.phone, email: p.email }], documents: p.documents ?? []
  } as Lead;
}
export { lead as makeLead };

const FILLER = ['Arora Traders', 'Bharat Logistics', 'Coastal Clinics', 'Deccan Motors', 'Elite Salons', 'Fresh Basket', 'Ganga Textiles', 'Hilltop Schools', 'Indus Pharma', 'Jaipur Gems', 'Kaveri Hotels', 'Lotus Realty', 'Metro Dental', 'Nilgiri Tea Co', 'Orchid Spa', 'Pioneer Steel', 'Quick Couriers', 'Royal Caterers', 'Sapphire Events', 'Tulsi Ayurveda', 'Urban Gyms', 'Vista Opticals', 'Westside Auto', 'Yamuna Foods', 'Zenith Labs'];
const NAMES = ['Ravi Khanna', 'Pooja Iyer', 'Amit Joshi', 'Sunita Rao', 'Deepak Jain', 'Neelam Shah', 'Farah Khan', 'Manoj Pillai', 'Kiran Desai', 'Rajesh Nair'];
const ROLES = ['Owner', 'Ops Head', 'CEO', 'Finance'];
function filler(ownerId: string, n: number, start: number): Lead[] {
  const inGroup = groups[0].memberIds.includes(ownerId);
  return Array.from({ length: n }, (_, i) => { const byRule = inGroup && i % 3 === 0; return lead({
    company: `${FILLER[(start + i) % FILLER.length]} ${['Pvt Ltd', 'LLP', '& Co', 'Group'][i % 4]}`, contactName: NAMES[(start + i) % NAMES.length], contactRole: ROLES[i % 4],
    source: byRule ? 'Referral' : (['Website', 'LinkedIn', 'Cold outbound', 'Referral'] as LeadSource[])[i % 4], industry: byRule ? 'Food & beverage' : undefined, ownerId,
    assignedAt: h(48 + i * 20), lastActivityAt: h(i % 9 === 4 ? 24 * 9 : 6 + (i % 5) * 18), method: byRule ? 'rule' : 'round_robin', ruleName: byRule ? rules[0].name : undefined, nextStepDue: day(i % 7 === 0 ? -1 : (i % 6) + 1) }); });
}

export const leads: Lead[] = [
  // Priya: three new leads waiting for her to look at, then her open book
  lead({ company: 'Sharma Sweets & Bakers', industry: 'Food & beverage', city: 'Delhi', contactName: 'Meera Sharma', contactRole: 'Owner', phone: '+91 98110 22345', source: 'Website', ownerId: 'u-priya', assignedAt: h(0.6), method: 'round_robin', generatedById: 'u-rohit', generatedByName: 'Rohit Bansal', createdAt: h(0.7), pooledAt: h(0.7), nextStep: 'Call to understand order-taking volume', nextStepDue: day(0), seenByOwner: false }),
  lead({ company: 'Apex Diagnostics', industry: 'Healthcare', city: 'Gurugram', contactName: 'Dr. Kunal Batra', contactRole: 'Ops Head', email: 'kunal@apexdx.in', source: 'LinkedIn', ownerId: 'u-priya', assignedAt: h(2.2), method: 'manual', assignedByName: 'Rohit Bansal', generatedById: 'u-ankit', generatedByName: 'Ankit Verma', createdAt: h(5), pooledAt: h(5), nextStep: 'Send voice agent demo recording', nextStepDue: day(1), seenByOwner: false }),
  lead({ company: 'Noida Packers & Movers', industry: 'Logistics', city: 'Noida', contactName: 'Harish Gupta', contactRole: 'CEO', phone: '+91 99580 11223', source: 'Cold outbound', ownerId: 'u-priya', assignedAt: h(3.5), method: 'round_robin', generatedById: 'u-priya', generatedByName: 'Priya Sharma', createdAt: h(3.6), pooledAt: h(3.6), nextStep: 'Book a 20-min discovery call', nextStepDue: day(1), seenByOwner: false }),
  lead({ company: 'Greenleaf Dental Clinics', industry: 'Healthcare', city: 'Delhi', contactName: 'Dr. Anita Rao', contactRole: 'Owner', phone: '+91 98100 44556', source: 'Referral', ownerId: 'u-priya', assignedAt: h(50), method: 'round_robin', nextStep: 'Follow up on missed-call volume numbers', nextStepDue: day(-1) }),
  lead({ company: 'Kapoor Auto Spares', industry: 'Automotive', city: 'Faridabad', contactName: 'Vivek Kapoor', contactRole: 'Owner', phone: '+91 98730 99887', source: 'Inbound call', ownerId: 'u-priya', assignedAt: h(75), method: 'manual', assignedByName: 'Rohit Bansal', nextStep: 'Share pricing sheet', nextStepDue: day(0) }),
  lead({ company: 'Blue Lotus Hotels', industry: 'Hospitality', city: 'Jaipur', contactName: 'Rhea Singh', contactRole: 'Ops Head', email: 'rhea@bluelotus.in', source: 'Website', ownerId: 'u-priya', assignedAt: h(100), method: 'round_robin', nextStep: 'Intro call with GM', nextStepDue: day(2) }),
  lead({ company: 'Mehta Home Loans', industry: 'Finance', city: 'Delhi', contactName: 'Sanjiv Mehta', contactRole: 'Finance', phone: '+91 98990 12121', source: 'LinkedIn', ownerId: 'u-priya', assignedAt: h(130), method: 'round_robin', nextStep: 'Call back after Diwali rush', nextStepDue: day(4) }),
  lead({ company: 'FitZone Gyms', industry: 'Fitness', city: 'Gurugram', contactName: 'Arjun Malik', contactRole: 'Owner', phone: '+91 98111 77889', source: 'Event', ownerId: 'u-priya', assignedAt: h(160), method: 'manual', assignedByName: 'Rohit Bansal', nextStep: 'Demo for membership renewals bot', nextStepDue: day(3) }),
  // Leads Priya added that went elsewhere
  lead({ company: 'Saffron Kitchens', industry: 'Food & beverage', city: 'Delhi', contactName: 'Imran Qureshi', contactRole: 'Owner', phone: '+91 98765 40001', source: 'Referral', generatedById: 'u-priya', generatedByName: 'Priya Sharma', createdAt: h(6), pooledAt: h(6), waitingReason: 'all_reps_at_cap', teamId: 't-north' }),
  lead({ company: 'Orbit Events Pvt Ltd', industry: 'Events', city: 'Mumbai', contactName: 'Tanya Fernandes', contactRole: 'CEO', email: 'tanya@orbitevents.in', source: 'Event', generatedById: 'u-priya', generatedByName: 'Priya Sharma', createdAt: h(9), pooledAt: h(9), waitingReason: 'no_active_reps', teamId: 't-ent' }),
  lead({ company: 'Shree Cement Dealers', industry: 'Construction', city: 'Pune', contactName: 'Ajay Patil', source: 'Inbound call', ownerId: 'u-rahul', assignedAt: h(26), method: 'rule', ruleName: rules[2].name, generatedById: 'u-priya', generatedByName: 'Priya Sharma', createdAt: h(26), pooledAt: h(26) }),
  // More waiting leads in Team North (rule target group is full)
  lead({ company: 'Punjab Grill House', industry: 'Food & beverage', city: 'Chandigarh', contactName: 'Gurpreet Singh', source: 'Referral', generatedById: 'u-divya', generatedByName: 'Divya Nair', createdAt: h(27), pooledAt: h(27), waitingReason: 'all_reps_at_cap', teamId: 't-north' }),
  lead({ company: 'Chai Point Express', industry: 'Food & beverage', city: 'Delhi', contactName: 'Nikhil Arora', source: 'Referral', generatedById: 'u-rohit', generatedByName: 'Rohit Bansal', createdAt: h(1.5), pooledAt: h(1.5), waitingReason: 'all_reps_at_cap', teamId: 't-north' }),
  // Enterprise has no active rep; South has one rep at cap
  lead({ company: 'Tata Projects Regional', industry: 'Construction', city: 'Hyderabad', contactName: 'K. Srinivas', source: 'Event', generatedById: 'u-arjun', generatedByName: 'Arjun Rao', createdAt: h(52), pooledAt: h(52), waitingReason: 'no_active_reps', teamId: 't-ent' }),
  lead({ company: 'Pune Pet Clinics', industry: 'Healthcare', city: 'Pune', contactName: 'Dr. Sameer Joshi', source: 'Inbound call', ownerId: 'u-kavya', assignedAt: h(1), method: 'rule', ruleName: rules[2].name, generatedById: 'u-neha', generatedByName: 'Neha Kapoor', createdAt: h(1), pooledAt: h(1), overCap: false }),
  ...filler('u-ankit', 20, 0), ...filler('u-divya', 20, 7), ...filler('u-rahul', 13, 3), ...filler('u-kavya', 19, 11), ...filler('u-priya', 1, 20)
];

/* ---------- counting ---------- */
export const activeLeads = (userId: string) => leads.filter((l) => l.status === 'open' && l.ownerId === userId).length;
export const capOf = (u: User) => u.leadCap ?? 0;
const isRep = (u: User) => u.role === 'user';
export const atCap = (u: User) => activeLeads(u.id) >= capOf(u);

/* ---------- the engine ---------- */
function matches(r: Rule, l: Lead) {
  return Object.entries(r.match).every(([k, v]) => (l as unknown as Record<string, unknown>)[k] === v);
}

/** Rules in priority order (first match wins), else the default team, then round-robin skipping reps at cap. */
export function runAssignment(l: Lead, dry = false): { ok: true; owner: User; method: AssignmentMethod; ruleName?: string } | { ok: false; reason: WaitingReason; teamId: string | null } {
  const rule = [...rules].filter((r) => r.active).sort((a, b) => a.priority - b.priority).find((r) => matches(r, l));
  let candidates: User[]; let teamId: string | null; let key: string;
  if (rule?.target.userId) { const u = db.users.find((x) => x.id === rule.target.userId)!; candidates = [u]; teamId = u.teamId; key = 'u:' + u.id; }
  else if (rule?.target.groupId) { const g = groups.find((x) => x.id === rule.target.groupId)!; candidates = g.memberIds.map((id) => db.users.find((u) => u.id === id)!); teamId = g.teamId; key = 'g:' + g.id; }
  else { teamId = rule?.target.teamId ?? settings.defaultTeamId; if (!teamId) return { ok: false, reason: 'no_rule_match', teamId: null }; candidates = db.users.filter((u) => u.teamId === teamId); key = 't:' + teamId; }
  const eligible = candidates.filter((u) => u && isRep(u) && u.active && u.teamId === teamId);
  if (eligible.length === 0) return { ok: false, reason: 'no_active_reps', teamId };
  const start = Math.max(-1, eligible.findIndex((u) => u.id === pointers[key]));
  for (let i = 1; i <= eligible.length; i++) {
    const u = eligible[(start + i) % eligible.length];
    if (!atCap(u)) { if (!dry) pointers[key] = u.id; return { ok: true, owner: u, method: rule ? 'rule' : 'round_robin', ruleName: rule?.name }; }
  }
  return { ok: false, reason: 'all_reps_at_cap', teamId };
}

export function newLead(input: NewLeadInput, by: User): Lead {
  return { id: 'd' + seq++, ...input, nextStepDue: input.nextStepDue || day(1), generatedById: by.id, generatedByName: by.name, createdAt: new Date().toISOString(), pooledAt: new Date().toISOString(), status: 'pooled', teamId: null, ownerId: null };
}

export function assign(l: Lead, owner: User, method: AssignmentMethod, extra: Partial<Lead> = {}) {
  const now = new Date().toISOString();
  Object.assign(l, { status: 'open', ownerId: owner.id, ownerName: owner.name, teamId: owner.teamId, assignedAt: now, method, waitingReason: undefined, seenByOwner: false,
    pipeline: 'new_business', stage: 'lead', stageEnteredAt: now, lane: 'mine', lastActivityAt: now, criteria: l.criteria ?? {}, fields: l.fields ?? {},
    contacts: l.contacts ?? [{ id: 'c' + Date.now(), name: l.contactName, role: l.contactRole, phone: l.phone, email: l.email, decisionMaker: !!l.contactDecisionMaker }], documents: l.documents ?? [], ...extra });
}

/* ---------- duplicates (PRD 4.6): checked against everything, revealed by scope ---------- */
const norm = (s = '') => s.toLowerCase().replace(/\b(pvt|ltd|llp|private|limited|co|group|&|and|the)\b/g, '').replace(/[^a-z0-9]/g, '');
const domain = (s = '') => s.toLowerCase().replace(/^https?:\/\//, '').replace(/^www\./, '').split('/')[0];
const digits = (s = '') => s.replace(/\D/g, '').slice(-10);
function similar(a: string, b: string) {
  const x = norm(a), y = norm(b); if (!x || !y) return false; if (x === y || x.includes(y) || y.includes(x)) return x.length > 3 && y.length > 3;
  const grams = (s: string) => new Set(Array.from({ length: Math.max(0, s.length - 2) }, (_, i) => s.slice(i, i + 3)));
  const A = grams(x), B = grams(y); const common = [...A].filter((g) => B.has(g)).length;
  return common / Math.max(1, Math.min(A.size, B.size)) >= 0.5;
}

export function findDuplicates(input: Partial<NewLeadInput>, inScope: (l: Lead) => boolean): DuplicateMatch[] {
  const out: DuplicateMatch[] = [];
  for (const l of leads) {
    let field: DuplicateMatch['field'] | null = null; let kind: DuplicateMatch['kind'] = 'exact';
    if (input.website && l.website && domain(input.website) === domain(l.website)) field = 'website';
    else if (input.phone && l.phone && digits(input.phone).length === 10 && digits(input.phone) === digits(l.phone)) field = 'phone';
    else if (input.email && l.email && input.email.trim().toLowerCase() === l.email.toLowerCase()) field = 'email';
    else if (input.company && input.company.trim().length >= 3 && similar(input.company, l.company)) { field = 'name'; kind = 'similar'; }
    if (!field) continue;
    const scoped = inScope(l);
    out.push({ kind, field, company: l.company, ownerName: l.ownerName ?? null, teamName: teamOf(l.teamId)?.name ?? null, inScope: scoped, leadId: scoped ? l.id : undefined });
  }
  return out.sort((a, b) => (a.kind === b.kind ? 0 : a.kind === 'exact' ? -1 : 1)).slice(0, 4);
}
