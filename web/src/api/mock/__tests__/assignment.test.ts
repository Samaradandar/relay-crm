import { describe, expect, it } from 'vitest';
import { db } from '../db';
import { activeLeads, findDuplicates, newLead, runAssignment, assign, leads } from '../leads';

const priya = db.users.find((u) => u.id === 'u-priya')!;
const base = { company: 'Test Co', contactName: 'A', phone: '+91 90000 00001', nextStep: 'Call', nextStepDue: '2099-01-01' };

describe('assignment engine (PRD 6)', () => {
  it('first matching rule decides; a full rep group makes the lead wait', () => {
    const r = runAssignment(newLead({ ...base, source: 'Referral', industry: 'Food & beverage' }, priya));
    expect(r).toEqual({ ok: false, reason: 'all_reps_at_cap', teamId: 't-north' });
  });
  it('a team with no active rep makes the lead wait with that reason', () => {
    const r = runAssignment(newLead({ ...base, source: 'Event' }, priya));
    expect(r.ok).toBe(false); if (!r.ok) expect(r.reason).toBe('no_active_reps');
  });
  it('no rule → default team round-robin, skipping reps at cap and never a Team Leader', () => {
    const r = runAssignment(newLead({ ...base, source: 'Website' }, priya));
    expect(r.ok).toBe(true); if (r.ok) { expect(r.owner.id).toBe('u-priya'); expect(r.method).toBe('round_robin'); expect(r.owner.role).toBe('user'); }
  });
  it('round-robin moves on and respects caps as leads land', () => {
    const before = activeLeads('u-priya');
    for (let i = 0; i < 3; i++) { const l = newLead({ ...base, company: 'RR ' + i, source: 'Website' }, priya); leads.push(l); const r = runAssignment(l); if (r.ok) assign(l, r.owner, r.method); }
    expect(activeLeads('u-priya')).toBeLessThanOrEqual(priya.leadCap!); expect(activeLeads('u-priya')).toBe(before + 3);
  });
});

describe('duplicate rule (PRD 4.6)', () => {
  it('outside scope: shows company and owner only, never a link', () => {
    const m = findDuplicates({ company: 'Shree Cement Dealers' }, () => false)[0];
    expect(m.company).toBe('Shree Cement Dealers'); expect(m.inScope).toBe(false); expect(m.leadId).toBeUndefined();
  });
  it('same phone is an exact match', () => {
    expect(findDuplicates({ phone: '98110 22345' }, () => true)[0]).toMatchObject({ kind: 'exact', field: 'phone' });
  });
});
