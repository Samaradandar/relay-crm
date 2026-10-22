import { describe, expect, it } from 'vitest';
import { db } from '../db';
import { leads } from '../leads';
import '../deals';
import * as T from '../team';
import { permissionsFor } from '@/auth/permissions';
import type { Me } from '../../types';

const meOf = (id: string): Me => { const u = db.users.find((x) => x.id === id)!; return { ...u, team: db.teams.find((t) => t.id === u.teamId) ?? null, permissions: permissionsFor(u.role) }; };
const rohit = meOf('u-rohit'); const neha = meOf('u-neha');
const find = (c: string) => leads.find((l) => l.company === c && l.pipeline)!;

describe('Team Leader rules (PRD 4.4, 10)', () => {
  it('sees ticks to verify and proof missing only for their own team', () => {
    const n = T.needsYou(rohit, []);
    expect(n.verify.length).toBeGreaterThan(0);
    expect(n.verify.every((v) => find(v.company).teamId === 't-north')).toBe(true);
    expect(n.proofMissing.map((p) => p.company)).toContain('Sagar Ratna Outlets');
    expect(n.approvals.map((a) => a.id)).toEqual(expect.arrayContaining(['a6', 'a7']));
    expect(T.needsYou(neha, []).approvals).toHaveLength(0);
  });
  it('rejecting a tick needs a reason and notifies the rep', () => {
    const d = find('Karim’s Kebab House');
    expect(() => T.verify(rohit, d, 'budget', 'rejected')).toThrow(/why/);
    T.verify(rohit, d, 'budget', 'rejected', 'Budget source is hearsay');
    expect(d.criteria!.budget.state).toBe('rejected');
    expect(db.notifications[0].userId).toBe('u-ankit');
    T.verify(rohit, d, 'decision_maker', 'verified'); expect(d.criteria!.decision_maker.state).toBe('verified');
  });
  it('override needs a reason, marks criteria Overridden, never on own deals', () => {
    const d = find('Paws & Claws Vet Clinics');
    expect(() => T.override(rohit, d, undefined, '')).toThrow(/reason/);
    T.override(rohit, d, undefined, 'Meeting happened offline at the expo');
    expect(d.stage).toBe('meeting_scheduled');
    expect(Object.values(d.criteria!).some((c) => c.state === 'overridden')).toBe(true);
  });
  it('reassign stays inside the team; reopen brings Lost back to its stage', () => {
    const d = find('Urban Smiles Dental');
    expect(() => T.reassign(rohit, d, 'u-rahul', 'x')).toThrow(/within your team/);
    T.reassign(rohit, d, 'u-priya', 'Divya on leave'); expect(d.ownerName).toBe('Priya Sharma');
    const l = find('Fast Fold Laundry'); T.reopen(rohit, l, 'They replied today'); expect(l.status).toBe('open'); expect(l.stage).toBe('qualification');
  });
  it('team overview adds up the reps', () => {
    const o = T.overview(rohit);
    expect(o.reps.map((r) => r.name).sort()).toEqual(['Ankit Verma', 'Divya Nair', 'Priya Sharma']);
    expect(o.target).toBe(o.reps.reduce((s, r) => s + r.target, 0));
  });
});
