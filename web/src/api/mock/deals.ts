import type { Activity, ActivityInput, CriterionState, Lead, Me, TimelineEvent } from '../types';
import { ApiError } from '../types';
import { PIPELINES, PLANS, nextStage, stageOf, type CriterionDef, type PipelineKey } from '@/lib/pipeline';
import { leads, makeLead } from './leads';

/**
 * In-browser copy of the pipeline engine (PRD 9, 10, Features 2, 6, 7): proof-first ticks, one stage
 * at a time, Revision rounds, Won on Onboarding / Updated, Lost with a reason, and the Today queue.
 */
const h = (hoursAgo: number) => new Date(Date.now() - hoursAgo * 3_600_000).toISOString();
const istDate = (d: Date) => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(d);
const day = (n: number) => istDate(new Date(Date.now() + n * 86_400_000));
/** A wall-clock time in IST, n days from today. */
const at = (n: number, hh: number, mm = 0) => new Date(`${day(n)}T${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}:00+05:30`).toISOString();
const todayIST = () => istDate(new Date());
const nowIso = () => new Date().toISOString();
const STALE_DAYS = 7; const NUDGE_DAYS = 2;

/** Monthly price per plan (₹). Deal value = annual contract value (12 × monthly), shown compact. */
export const PLAN_PRICE: Record<string, number> = { 'Starter 5,000 min': 30_000, 'Growth 20,000 min': 1_10_000, 'Scale 50,000 min': 2_50_000, 'SaaS Monthly': 60_000, 'SaaS Quarterly': 55_000 };
export function computeValue(d: Lead): number | undefined {
  const f = d.fields ?? {};
  const monthly = f.finalPrice ? Number(f.finalPrice) : f.plan ? PLAN_PRICE[f.plan] : undefined;
  return monthly ? monthly * 12 * 100 : undefined;
}

export const activities: Activity[] = [];
export const events: TimelineEvent[] = [];
let aseq = 1, eseq = 1;
function ev(dealId: string, kind: TimelineEvent['kind'], title: string, by: string, extra: Partial<TimelineEvent> = {}, when = nowIso()) { events.push({ id: 'e' + eseq++, dealId, kind, title, by, at: when, ...extra }); }
function act(d: Lead, a: Partial<Activity> & Pick<Activity, 'kind' | 'discussed'>, hoursAgo: number) {
  const x: Activity = { id: 'a' + aseq++, dealId: d.id, twoWay: true, at: h(hoursAgo), byId: d.ownerId!, byName: d.ownerName!, nextStep: d.nextStep, followUp: d.nextStepDue, responsibleName: d.ownerName!, ...a };
  activities.push(x); ev(d.id, 'activity', '', x.byName, { activityId: x.id }, x.at);
  if (!d.lastActivityAt || x.at > d.lastActivityAt) d.lastActivityAt = x.at;
  return x;
}
const tick = (by: string, proof: string, hoursAgo = 30): CriterionState => ({ state: 'ticked', proof, by, at: h(hoursAgo) });
const verified = (by: string, proof: string, hoursAgo = 30): CriterionState => ({ state: 'verified', proof, by, at: h(hoursAgo), note: 'Verified by Rohit Bansal' });

/* ---------- seed: Priya's book across every stage ---------- */
const P = { ownerId: 'u-priya', generatedById: 'u-rohit', generatedByName: 'Rohit Bansal', method: 'round_robin' as const };
type Seed = Parameters<typeof makeLead>[0];
function deal(s: Seed, stage: string, daysInStage: number, pipeline: PipelineKey = 'new_business'): Lead {
  const d = makeLead({ ...P, assignedAt: h(24 * (daysInStage + 6)), createdAt: h(24 * (daysInStage + 6)), pooledAt: h(24 * (daysInStage + 6)), ...s, pipeline, stage, stageEnteredAt: h(24 * daysInStage), lastActivityAt: h(24 * Math.min(daysInStage, 2) + 3) });
  ev(d.id, 'assign', `Assigned to ${d.ownerName} by round-robin`, 'System', {}, d.assignedAt);
  const st = PIPELINES[pipeline].stages; const idx = st.findIndex((x) => x.key === stage);
  st.slice(0, idx).filter((x) => x.key !== 'revision_2' || (d.round ?? 1) > 1).forEach((x, i, arr) => {
    const next = arr[i + 1]?.key ?? stage;
    ev(d.id, 'stage', `Moved ${x.label} → ${stageOf(pipeline, next)?.label}`, d.ownerName!, {}, h(24 * (daysInStage + (arr.length - i) * 1.5)));
    x.criteria.forEach((c) => { d.criteria![c.key + '@' + x.key] = verified('Priya Sharma', 'Done', 24 * (daysInStage + 2)); });
  });
  d.value = computeValue(d);
  leads.push(d); return d;
}

function seed() {
  // Lead stage: give the older named leads some history
  const g = leads.find((l) => l.company === 'Greenleaf Dental Clinics')!;
  act(g, { kind: 'call', twoWay: false, discussed: 'No answer. Left a voicemail asking for a call back.' }, 30);
  const k = leads.find((l) => l.company === 'Kapoor Auto Spares')!;
  act(k, { kind: 'call', discussed: 'Spoke to Vivek. Gets 60–80 calls a day for spare part availability; wants pricing first.', requirements: 'Hindi + English', nextStep: 'Share pricing sheet', followUp: day(0) }, 20);
  k.criteria = { contact_verified: tick('Priya Sharma', 'Call · 2-way', 20) };
  const m = leads.find((l) => l.company === 'Mehta Home Loans')!; m.lastActivityAt = h(24 * 9); m.stageEnteredAt = h(24 * 12);

  // Qualification
  const rc = deal({ company: 'Royal Rajasthan Caterers', industry: 'Food & beverage', city: 'Jaipur', contactName: 'Mahendra Rathore', contactRole: 'Owner', phone: '+91 94140 22110', source: 'Referral', website: 'royalrajasthancaterers.in', nextStep: 'Book the discovery meeting with Mahendra and his ops manager', nextStepDue: day(0) }, 'qualification', 3);
  rc.criteria!.need = tick('Priya Sharma', 'Pain point written', 50); rc.fields!.painPoint = 'Misses wedding-season enquiry calls on weekends; 3 people on phones.';
  act(rc, { kind: 'call', discussed: 'Mahendra confirmed they lose bookings when lines are busy. Open to a demo.', requirements: 'Rajasthani-accented Hindi', nextStep: rc.nextStep, followUp: rc.nextStepDue }, 50);
  const cm = deal({ company: 'Café Mocha Chain', industry: 'Food & beverage', city: 'Delhi', contactName: 'Ritika Bose', contactRole: 'Ops Head', email: 'ritika@cafemocha.in', source: 'LinkedIn', website: 'cafemocha.in', nextStep: 'Waiting for Ritika to share call volumes per outlet', nextStepDue: day(-1), lane: 'theirs', waitingSince: h(24 * 3) }, 'qualification', 5);
  act(cm, { kind: 'email', discussed: 'Sent the one-pager; asked for monthly call volumes per outlet.', nextStep: cm.nextStep, followUp: cm.nextStepDue }, 24 * 3);

  // Meeting Scheduled
  const dh = deal({ company: 'Delhi Heart & Lung Hospital', industry: 'Healthcare', city: 'Delhi', contactName: 'Dr. Suresh Menon', contactRole: 'CEO', phone: '+91 98111 30303', source: 'Event', website: 'dhlh.in', nextStep: 'Run the discovery meeting: appointment-booking use case', nextStepDue: day(0) }, 'meeting_scheduled', 2);
  dh.fields = { ...dh.fields, painPoint: 'Front desk overloaded with appointment calls', meetingAt: at(0, 15, 0), attendees: 'Dr. Suresh Menon, Anjali (front desk lead)' };
  act(dh, { kind: 'meeting', twoWay: false, discussed: 'Discovery meeting booked.', meetingAt: at(0, 15, 0), attendees: 'Dr. Suresh Menon, Anjali (front desk lead)', nextStep: dh.nextStep, followUp: day(0) }, 40);
  const sf = deal({ company: 'SpeedFix Car Care', industry: 'Automotive', city: 'Noida', contactName: 'Aman Gill', contactRole: 'Owner', phone: '+91 98990 45454', source: 'Cold outbound', nextStep: 'Demo meeting at their Sector 18 workshop', nextStepDue: day(1) }, 'meeting_scheduled', 1);
  sf.fields = { ...sf.fields, meetingAt: at(1, 11, 30), attendees: 'Aman Gill' };
  act(sf, { kind: 'meeting', twoWay: false, discussed: 'Meeting booked for a live demo.', meetingAt: at(1, 11, 30), attendees: 'Aman Gill', nextStep: sf.nextStep, followUp: day(1) }, 26);

  // Prospecting
  const ab = deal({ company: 'ACME Bakes', industry: 'Food & beverage', city: 'Gurugram', contactName: 'Nitin Arora', contactRole: 'Owner', phone: '+91 98100 77001', source: 'Referral', website: 'acmebakes.in', nextStep: 'Confirm budget with Nitin and his CFO', nextStepDue: day(1) }, 'prospecting', 4);
  ab.fields = { ...ab.fields, useCase: 'Order-taking and delivery-status calls for 6 outlets', pricingModel: 'Per-minute (managed)', plan: 'Growth 20,000 min', minutes: '18000', expectedClose: day(20), closeReason: 'Wants it live before the Christmas rush' };
  ab.criteria = { ...ab.criteria, pricing: tick('Priya Sharma', 'Growth 20,000 min · 18,000 min/month', 40), timeline: tick('Priya Sharma', 'Close by ' + new Date(day(20) + 'T00:00:00Z').toLocaleDateString('en-IN', { day: 'numeric', month: 'short', timeZone: 'UTC' }), 40) };
  ab.contacts = [{ id: 'c-ab1', name: 'Nitin Arora', role: 'Owner', phone: '+91 98100 77001', decisionMaker: false }, { id: 'c-ab2', name: 'Kavita Arora', role: 'Finance', email: 'kavita@acmebakes.in' }];
  ab.value = computeValue(ab);
  act(ab, { kind: 'discovery', discussed: 'Walked through 6 outlets; 18k minutes a month expected. Nitin signs, Kavita (CFO) approves budget.', decisions: 'Go with per-minute managed', nextStep: ab.nextStep, followUp: ab.nextStepDue }, 40);
  const lr = deal({ company: 'Lakeview Resorts', industry: 'Hospitality', city: 'Udaipur', contactName: 'Karan Mewar', contactRole: 'Ops Head', email: 'karan@lakeviewresorts.in', source: 'Website', website: 'lakeviewresorts.in', nextStep: 'Send plan comparison: Growth vs Scale', nextStepDue: day(2) }, 'prospecting', 2);
  lr.fields = { ...lr.fields, pricingModel: 'Per-minute (managed)', plan: 'Scale 50,000 min', minutes: '42000', expectedClose: day(25), closeReason: 'Peak season starts in November' }; lr.value = computeValue(lr);
  act(lr, { kind: 'call', discussed: 'Reservations team of 8; 40k+ minutes in season.', nextStep: lr.nextStep, followUp: lr.nextStepDue }, 30);

  // Proposal
  const te = deal({ company: 'Tandoor Express Franchise', industry: 'Food & beverage', city: 'Delhi', contactName: 'Harpreet Kohli', contactRole: 'CEO', phone: '+91 98111 55443', source: 'Referral', website: 'tandoorexpress.in', nextStep: 'Waiting for Harpreet’s decision on the proposal', nextStepDue: day(1), lane: 'theirs', waitingSince: h(24 * 3) }, 'proposal', 4);
  te.fields = { ...te.fields, pricingModel: 'Per-minute (managed)', plan: 'Growth 20,000 min', minutes: '20000', expectedClose: day(10), proposalVersion: 'v1', proposalSent: day(-4) }; te.value = computeValue(te);
  te.documents = [{ id: 'doc1', name: 'Tandoor Express — Proposal v1.pdf', kind: 'proposal', status: 'Sent', at: h(24 * 4), by: 'Priya Sharma' }];
  act(te, { kind: 'email', twoWay: false, discussed: 'Emailed proposal v1 (Growth plan, 12 months).', nextStep: te.nextStep, followUp: te.nextStepDue }, 24 * 3);
  const mq = deal({ company: 'MediQuick Pharmacy', industry: 'Healthcare', city: 'Faridabad', contactName: 'Rohan Sethi', contactRole: 'Owner', phone: '+91 98730 11009', source: 'Inbound call', nextStep: 'Walk Rohan through the proposal on a call', nextStepDue: day(0) }, 'proposal', 1);
  mq.fields = { ...mq.fields, pricingModel: 'Per-minute (managed)', plan: 'Starter 5,000 min', minutes: '4500', expectedClose: day(12), proposalVersion: 'v1', proposalSent: day(-1) }; mq.value = computeValue(mq);
  mq.documents = [{ id: 'doc2', name: 'MediQuick — Proposal v1.pdf', kind: 'proposal', status: 'Sent', at: h(22), by: 'Priya Sharma' }];

  // Revision (one in round 2)
  const sr = deal({ company: 'Spice Route Restaurants', industry: 'Food & beverage', city: 'Gurugram', contactName: 'Vikas Malhotra', contactRole: 'Owner', phone: '+91 98100 66221', source: 'Event', round: 2, nextStep: 'Send revised price with 3-month quarterly plan', nextStepDue: day(0) }, 'revision_2', 2);
  sr.fields = { ...sr.fields, pricingModel: 'Per-minute (managed)', plan: 'Growth 20,000 min', minutes: '16000', expectedClose: day(6), changes: 'Wants quarterly billing and a lower first-quarter price', round2Reason: 'Customer asked for quarterly billing after legal review', finalPrice: '98000' }; sr.value = computeValue(sr);
  act(sr, { kind: 'call', discussed: 'Legal asked for quarterly billing; Vikas wants ₹98K/month.', objections: 'Price for first quarter', nextStep: sr.nextStep, followUp: sr.nextStepDue }, 20);
  const ed = deal({ company: 'Elite Dental Care', industry: 'Healthcare', city: 'Delhi', contactName: 'Dr. Neha Bajaj', contactRole: 'Owner', email: 'neha@elitedental.in', source: 'LinkedIn', round: 1, nextStep: 'Send the agreement for signature', nextStepDue: day(1) }, 'revision', 2);
  ed.fields = { ...ed.fields, pricingModel: 'Per-minute (managed)', plan: 'Starter 5,000 min', minutes: '5000', expectedClose: day(5), finalPrice: '30000', scope: '2 clinics, Hindi + English', agreedBy: 'Dr. Neha Bajaj' }; ed.value = computeValue(ed);
  ed.criteria = { ...ed.criteria, final_terms: tick('Priya Sharma', '₹30,000/month · 2 clinics · agreed by Dr. Neha Bajaj', 18) };

  // Closure
  const gp = deal({ company: 'Hotel Grand Palace', industry: 'Hospitality', city: 'Agra', contactName: 'Sameer Khanna', contactRole: 'Finance', phone: '+91 94120 88771', source: 'Website', nextStep: 'Chase the countersigned agreement', nextStepDue: day(-2), lane: 'theirs', waitingSince: h(24 * 4) }, 'closure', 4);
  gp.fields = { ...gp.fields, pricingModel: 'Per-minute (managed)', plan: 'Growth 20,000 min', expectedClose: day(3), finalPrice: '105000', agreementStatus: 'Sent' }; gp.value = computeValue(gp);
  gp.documents = [{ id: 'doc3', name: 'Grand Palace — Agreement.pdf', kind: 'agreement', status: 'Sent', at: h(24 * 4), by: 'Priya Sharma' }];
  act(gp, { kind: 'email', twoWay: false, discussed: 'Agreement sent for signature.', nextStep: gp.nextStep, followUp: gp.nextStepDue }, 24 * 4);

  // Won this month (Onboarding)
  const bs = deal({ company: 'Bikaner Sweets Corner', industry: 'Food & beverage', city: 'Delhi', contactName: 'Gopal Agarwal', contactRole: 'Owner', phone: '+91 98110 90909', source: 'Referral', nextStep: 'Kickoff call with their outlet managers', nextStepDue: day(1) }, 'onboarding', 5);
  Object.assign(bs, { status: 'won', wonAt: h(24 * 5) }); bs.fields = { ...bs.fields, pricingModel: 'Per-minute (managed)', plan: 'Growth 20,000 min', finalPrice: '110000', goLive: day(9) }; bs.value = computeValue(bs);
  ev(bs.id, 'won', 'Won · agreement countersigned', 'Priya Sharma', {}, h(24 * 5));
  const cc = deal({ company: 'CityCare Clinics', industry: 'Healthcare', city: 'Noida', contactName: 'Dr. Arvind Rao', contactRole: 'CEO', email: 'arvind@citycare.in', source: 'Website', nextStep: 'Confirm go-live date with Dr. Rao', nextStepDue: day(2) }, 'onboarding', 6);
  Object.assign(cc, { status: 'won', wonAt: h(24 * 6) }); cc.fields = { ...cc.fields, pricingModel: 'Per-minute (managed)', plan: 'Starter 5,000 min', finalPrice: '42000', kickoffAt: day(-3), goLive: day(4) }; cc.value = computeValue(cc);
  cc.criteria = { ...cc.criteria, kickoff: tick('Priya Sharma', 'Kickoff done · go-live planned', 70) };

  // Lost
  const qb = deal({ company: 'QuickBite Cafe', industry: 'Food & beverage', city: 'Delhi', contactName: 'Sonal Jain', source: 'Website', nextStep: '—', nextStepDue: day(-10) }, 'prospecting', 12);
  Object.assign(qb, { status: 'lost', lostReason: 'Budget constraints', lostNotes: 'Owner will revisit after Diwali.', lostFromStage: 'prospecting', lostAt: h(24 * 8) });

  // Existing customers (account owned by Priya)
  const tu = deal({ company: 'Bikaner Sweets Corner', industry: 'Food & beverage', city: 'Delhi', contactName: 'Gopal Agarwal', contactRole: 'Owner', phone: '+91 98110 90909', source: 'Referral', nextStep: 'Confirm top-up size: 10,000 or 20,000 minutes', nextStepDue: day(0) }, 'request', 1, 'existing_customer');
  tu.fields = { ...tu.fields, requestType: 'Minute top-up' }; tu.criteria = { request_type: tick('Priya Sharma', 'Minute top-up', 20) };
  const ss = deal({ company: 'Sunrise Public Schools', industry: 'Education', city: 'Ghaziabad', contactName: 'Mrs. Kiran Bedi', contactRole: 'Finance', email: 'accounts@sunriseschools.in', source: 'Referral', nextStep: 'Waiting for the board’s decision on the second campus', nextStepDue: day(3), lane: 'theirs', waitingSince: h(24) }, 'proposal', 3, 'existing_customer');
  ss.fields = { ...ss.fields, requestType: 'Upsell', plan: 'Growth 20,000 min', proposalSent: day(-3) }; ss.value = computeValue(ss);
}
seed();

/* ---------- rules ---------- */
export const own = (me: Me, d: Lead) => d.ownerId === me.id;
export function requireOwn(me: Me, d: Lead | undefined): Lead {
  if (!d) throw new ApiError(404, 'not_found', 'Deal not found.');
  if (!own(me, d)) throw new ApiError(403, 'permission_denied', 'Only the deal owner ticks criteria and moves the deal.');
  return d;
}
const daysSince = (iso?: string) => (iso ? (Date.now() - Date.parse(iso)) / 86_400_000 : 0);
export const isStale = (d: Lead) => d.status === 'open' && daysSince(d.lastActivityAt) >= STALE_DAYS;
export const nudgeDue = (d: Lead) => d.lane === 'theirs' && daysSince(d.waitingSince) >= NUDGE_DAYS;
export function health(d: Lead): 'on_track' | 'at_risk' | 'stale' {
  if (isStale(d)) return 'stale';
  return d.nextStepDue < todayIST() ? 'at_risk' : 'on_track';
}

/** Criteria live per stage; keys from earlier stages are kept as "key@stage". */
export function criterionState(d: Lead, c: CriterionDef): CriterionState {
  if (c.proof.kind === 'auto') {
    const ok = c.proof.check === 'basic_info' ? !!(d.company && d.contactName && (d.phone || d.email) && d.source) : !!(d.company && (d.website || d.industry));
    return ok ? { state: 'ticked', proof: c.proof.check === 'basic_info' ? 'All basic fields filled' : d.website ?? d.industry, by: 'System' } : { state: 'open' };
  }
  return d.criteria?.[c.key] ?? { state: 'open' };
}
const done = (s: CriterionState) => s.state === 'ticked' || s.state === 'verified' || s.state === 'overridden';
export function missing(d: Lead): string[] {
  const st = stageOf(d.pipeline!, d.stage!); if (!st) return [];
  return st.criteria.filter((c) => !done(criterionState(d, c))).map((c) => c.label);
}

/** Matching logged activities for an activity-type criterion (the proof options). */
export function proofActivities(d: Lead, c: CriterionDef): Activity[] {
  if (c.proof.kind !== 'activity') return [];
  const p = c.proof;
  return activities.filter((a) => a.dealId === d.id && p.activity.includes(a.kind) && (!p.twoWay || a.twoWay)).sort((a, b) => b.at.localeCompare(a.at));
}

export function doTick(me: Me, d: Lead, key: string, input: { activityId?: string; fields?: Record<string, string>; file?: { name: string; status?: string } }) {
  const st = stageOf(d.pipeline!, d.stage!)!; const c = st.criteria.find((x) => x.key === key);
  if (!c) throw new ApiError(404, 'not_found', 'No such criterion in this stage.');
  let proof = '';
  if (c.proof.kind === 'auto') throw new ApiError(422, 'invalid', 'This one ticks itself when the fields are filled in.');
  if (c.proof.kind === 'activity') {
    const a = proofActivities(d, c).find((x) => x.id === input.activityId);
    if (!a) throw new ApiError(422, 'proof_required', c.proof.explain);
    proof = `${a.kind[0].toUpperCase() + a.kind.slice(1)} · ${new Date(a.at).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', timeZone: 'Asia/Kolkata' })}`;
  }
  if (c.proof.kind === 'fields') {
    const vals = input.fields ?? {};
    const empty = c.proof.fields.filter((x) => !vals[x.key]?.toString().trim());
    if (empty.length) throw new ApiError(422, 'proof_required', `Fill in: ${empty.map((x) => x.label).join(', ')}.`);
    if (key === 'fit' && Object.values(vals).some((v) => v === 'No')) throw new ApiError(422, 'invalid', 'This lead does not fit the target profile on every check. Close it as Lost, or ask your Team Leader to override.');
    d.fields = { ...d.fields, ...vals };
    if (vals.decisionMaker) d.contacts = d.contacts?.map((x) => ({ ...x, decisionMaker: x.name === vals.decisionMaker || x.decisionMaker }));
    const show = (v: string) => /^\d{4}-\d{2}-\d{2}$/.test(v) ? new Date(v + 'T00:00:00Z').toLocaleDateString('en-IN', { day: 'numeric', month: 'short', timeZone: 'UTC' }) : v;
    proof = c.proof.fields.map((x) => vals[x.key]).filter((v) => v && v.length < 40).slice(0, 3).map(show).join(' · ') || 'Details recorded';
    d.value = computeValue(d) ?? d.value;
  }
  if (c.proof.kind === 'file') {
    if (!input.file?.name) throw new ApiError(422, 'proof_required', c.proof.explain);
    const status = input.file.status ?? c.proof.status?.[0];
    d.documents = [...(d.documents ?? []), { id: 'doc' + Date.now(), name: input.file.name, kind: /invoice/i.test(c.label) ? 'invoice' : /proposal/i.test(c.label) ? 'proposal' : 'agreement', status, at: nowIso(), by: me.name }];
    if (status) d.fields = { ...d.fields, agreementStatus: status };
    proof = `${input.file.name}${status ? ' · ' + status : ''}`;
  }
  d.criteria = { ...d.criteria, [key]: { state: 'ticked', proof, by: me.name, at: nowIso() } };
  ev(d.id, 'criterion', `Ticked “${c.label}”`, me.name, { detail: proof });
}

export function doUntick(me: Me, d: Lead, key: string, reason: string) {
  if (!reason.trim()) throw new ApiError(422, 'reason_required', 'Add a reason for unticking.');
  const c = stageOf(d.pipeline!, d.stage!)!.criteria.find((x) => x.key === key)!;
  const next = { ...d.criteria }; delete next[key]; d.criteria = next;
  ev(d.id, 'criterion', `Unticked “${c.label}”`, me.name, { reason });
}

function enter(d: Lead, to: string, me: Me) {
  const st = stageOf(d.pipeline!, to)!;
  // keep this stage's ticks as key@stage so the new checklist starts fresh (snapshot on entry)
  const kept: Record<string, CriterionState> = {};
  Object.entries(d.criteria ?? {}).forEach(([k, v]) => { kept[k.includes('@') ? k : `${k}@${d.stage}`] = v; });
  d.criteria = kept; d.stage = to; d.stageEnteredAt = nowIso();
  if (st.won) { d.status = 'won'; d.wonAt = nowIso(); ev(d.id, 'won', d.pipeline === 'new_business' ? 'Won · deal entered Onboarding' : 'Won · subscription updated', me.name); }
}

export function doMove(me: Me, d: Lead, opts: { to?: string; reason?: string; round2?: boolean }) {
  if (d.status === 'lost') throw new ApiError(409, 'conflict', 'This deal is Lost. Ask your Team Leader to reopen it.');
  const p = d.pipeline!; const stages = PIPELINES[p].stages; const cur = stageOf(p, d.stage!)!;
  const from = cur.label;
  if (opts.round2) {
    if (!opts.reason?.trim()) throw new ApiError(422, 'reason_required', 'Say why another round of changes is needed.');
    if (cur.key !== 'revision' && cur.key !== 'revision_2') throw new ApiError(422, 'invalid', 'Only a deal in Revision can go to another round.');
    d.round = (d.round ?? 1) + 1; d.fields = { ...d.fields, round2Reason: opts.reason };
    if (cur.key === 'revision') enter(d, 'revision_2', me); else d.criteria = Object.fromEntries(Object.entries(d.criteria ?? {}).filter(([k]) => k.includes('@')));
    ev(d.id, 'stage', `Revision round ${d.round}`, me.name, { reason: opts.reason });
    d.lastActivityAt = nowIso(); return;
  }
  const target = opts.to ? stageOf(p, opts.to) : nextStage(p, cur.key);
  if (!target) throw new ApiError(422, 'invalid', 'This deal is already at the last stage.');
  const ci = stages.indexOf(cur), ti = stages.indexOf(target);
  if (ti < ci || (cur.key === 'revision_2' && target.key === 'revision')) {
    if (!opts.reason?.trim()) throw new ApiError(422, 'reason_required', 'Moving back needs a reason.');
    enter(d, target.key, me); ev(d.id, 'stage', `Moved back ${from} → ${target.label}`, me.name, { reason: opts.reason }); d.lastActivityAt = nowIso(); return;
  }
  if (target !== nextStage(p, cur.key)) throw new ApiError(403, 'permission_denied', 'Deals move forward one stage at a time. To skip a stage, ask your Team Leader for an override.');
  const miss = missing(d);
  if (miss.length) throw new ApiError(422, 'criteria_missing', `Tick these first: ${miss.join(' · ')}`);
  if (target.key === 'proposal' && !d.fields?.expectedClose) throw new ApiError(422, 'invalid', 'Set the expected close date first.');
  enter(d, target.key, me); ev(d.id, 'stage', `Moved ${from} → ${target.label}`, me.name); d.lastActivityAt = nowIso();
}

export function doLose(me: Me, d: Lead, reason: string, notes: string) {
  if (!reason) throw new ApiError(422, 'reason_required', 'Pick a lost reason.');
  Object.assign(d, { status: 'lost', lostReason: reason, lostNotes: notes, lostFromStage: d.stage, lostAt: nowIso() });
  ev(d.id, 'lost', `Closed as Lost · ${reason}`, me.name, { reason: notes || undefined });
}

/* ---------- next steps and Today (Feature 6) ---------- */
export function doNextStep(me: Me, d: Lead, step: { text: string; due: string; lane: 'mine' | 'theirs' }, reasonForEvent = 'Next step done') {
  if (!step.text.trim() || !step.due) throw new ApiError(422, 'invalid', 'Every deal needs a next step and a due date.');
  if (step.due < todayIST()) throw new ApiError(422, 'invalid', 'The due date cannot be in the past.');
  const prev = d.nextStep;
  Object.assign(d, { nextStep: step.text, nextStepDue: step.due, lane: step.lane, waitingSince: step.lane === 'theirs' ? nowIso() : undefined, lastActivityAt: nowIso() });
  ev(d.id, 'edit', `${reasonForEvent}: “${prev}”`, me.name, { detail: `Next: ${step.text} · due ${step.due}${step.lane === 'theirs' ? ' · waiting on them' : ''}` });
}
export function doSnooze(me: Me, d: Lead) {
  const base = d.nextStepDue < todayIST() ? todayIST() : d.nextStepDue;
  const due = istDate(new Date(Date.parse(base + 'T12:00:00+05:30') + 2 * 86_400_000));
  d.nextStepDue = due; d.snoozeCount = (d.snoozeCount ?? 0) + 1;
  ev(d.id, 'edit', 'Snoozed 2 days', me.name, { detail: `Now due ${due} · snoozed ${d.snoozeCount}×` });
}
export function doNudge(me: Me, d: Lead) {
  const a: Activity = { id: 'a' + aseq++, dealId: d.id, kind: 'email', twoWay: false, at: nowIso(), byId: me.id, byName: me.name, discussed: 'Nudge sent: checking in on the open question.', nextStep: d.nextStep, followUp: d.nextStepDue, responsibleName: me.name };
  activities.push(a); ev(d.id, 'activity', '', me.name, { activityId: a.id }); d.waitingSince = nowIso(); d.lastActivityAt = nowIso();
}
export function doReplied(me: Me, d: Lead) { d.lane = 'mine'; d.waitingSince = undefined; d.lastActivityAt = nowIso(); ev(d.id, 'edit', 'They replied · back to your move', me.name); }

export function doLog(me: Me, d: Lead, input: ActivityInput): Activity {
  if (!input.discussed.trim()) throw new ApiError(422, 'invalid', 'Write what was discussed.');
  if (!input.nextStep.trim() || !input.followUp) throw new ApiError(422, 'invalid', 'Every note needs a next step and a follow-up date.');
  if (input.kind === 'meeting' && (!input.meetingAt || !input.attendees?.trim())) throw new ApiError(422, 'invalid', 'A meeting needs a date, time and attendees.');
  const a: Activity = { ...input, id: 'a' + aseq++, byId: me.id, byName: me.name, responsibleName: me.name };
  activities.push(a); ev(d.id, 'activity', '', me.name, { activityId: a.id }, a.at);
  Object.assign(d, { lastActivityAt: nowIso(), nextStep: input.nextStep, nextStepDue: input.followUp, lane: input.lane ?? 'mine', waitingSince: input.lane === 'theirs' ? nowIso() : undefined });
  if (input.kind === 'meeting' && input.meetingAt) d.fields = { ...d.fields, meetingAt: input.meetingAt, attendees: input.attendees ?? '' };
  if (input.kind === 'walkthrough') d.fields = { ...d.fields, walkthroughAt: input.at.slice(0, 10) };
  return a;
}

/* ---------- my numbers (the strip on Today; full view on My dashboard) ---------- */
/** Revenue already booked this month from deals not modelled in the sample data (₹). */
const BASE: Record<string, number> = { 'u-priya': 0, 'u-rahul': 21_40_000, 'u-kavya': 18_20_000, 'u-ankit': 6_40_000, 'u-divya': 5_20_000 };
export const TARGETS: Record<string, number> = { 'u-priya': 25_00_000, 'u-rahul': 25_00_000, 'u-kavya': 22_00_000, 'u-ankit': 18_00_000, 'u-divya': 15_00_000 };
export function numbersFor(userId: string) {
  const month = todayIST().slice(0, 7);
  const mine = leads.filter((d) => d.ownerId === userId);
  const won = mine.filter((d) => d.status === 'won' && d.wonAt?.slice(0, 7) === month);
  const revenue = (BASE[userId] ?? 0) + won.reduce((s, d) => s + (d.value ?? 0) / 100, 0);
  const projected = revenue + mine.filter((d) => d.status === 'open' && d.fields?.expectedClose?.slice(0, 7) === month)
    .reduce((s, d) => s + ((d.value ?? 0) / 100) * (stageOf(d.pipeline!, d.stage!)!.probability / 100), 0);
  const board = Object.keys(BASE).map((id) => [id, id === userId ? revenue : revenueOf(id)] as const).sort((a, b) => b[1] - a[1]);
  const rank = board.findIndex(([id]) => id === userId) + 1;
  return { revenueMonth: revenue * 100, target: (TARGETS[userId] ?? 0) * 100, best: 21_00_000 * 100, projected: Math.round(projected) * 100, rank, reps: board.length, behindNext: rank > 1 ? (board[rank - 2][1] - revenue) * 100 : null, wonMonth: won.length };
}
function revenueOf(uid: string) { const month = todayIST().slice(0, 7); return (BASE[uid] ?? 0) + leads.filter((d) => d.ownerId === uid && d.status === 'won' && d.wonAt?.slice(0, 7) === month).reduce((s, d) => s + (d.value ?? 0) / 100, 0); }
export { PLANS, deal as seedDeal, act as seedActivity, tick as seedTick, ev as seedEvent, h as hoursAgo, day as daysFromNow, at as atIST, todayIST };
