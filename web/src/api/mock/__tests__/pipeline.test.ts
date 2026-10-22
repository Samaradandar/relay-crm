import { describe, expect, it } from 'vitest';
import { db } from '../db';
import { leads } from '../leads';
import { doLose, doMove, doNextStep, doTick, doLog, missing, requireOwn, criterionState } from '../deals';
import { stageOf } from '@/lib/pipeline';
import { permissionsFor } from '@/auth/permissions';
import type { Me } from '../../types';

const meOf = (id: string): Me => { const u = db.users.find((x) => x.id === id)!; return { ...u, team: null, permissions: permissionsFor(u.role) }; };
const priya = meOf('u-priya'); const rohit = meOf('u-rohit');
const find = (c: string) => leads.find((l) => l.company === c && l.status !== 'pooled')!;
const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(new Date());

describe('pipeline rules (PRD 10, Feature 2)', () => {
  it('blocks a forward move and lists what is missing', () => {
    const d = find('Royal Rajasthan Caterers');
    expect(() => doMove(priya, d, {})).toThrow(/Fits target customer profile/);
  });
  it('proof first: a two-way activity must exist before Contact verified', () => {
    const d = find('Blue Lotus Hotels');
    expect(() => doTick(priya, d, 'contact_verified', {})).toThrow(/two-way/);
    const a = doLog(priya, d, { dealId: d.id, kind: 'call', twoWay: true, at: new Date().toISOString(), discussed: 'Spoke to Rhea', nextStep: 'Send deck', followUp: today });
    doTick(priya, d, 'contact_verified', { activityId: a.id });
    expect(criterionState(d, stageOf('new_business', 'lead')!.criteria[2]).state).toBe('ticked');
  });
  it('moves one stage at a time once every criterion is ticked, and keeps old ticks', () => {
    const d = find('Blue Lotus Hotels'); d.website = 'bluelotus.in';
    expect(missing(d)).toEqual([]);
    doMove(priya, d, {}); expect(d.stage).toBe('qualification');
    expect(Object.keys(d.criteria!).every((k) => k.includes('@'))).toBe(true);
    expect(() => doMove(priya, d, { to: 'prospecting' })).toThrow(/one stage at a time/);
  });
  it('moving back needs a reason', () => {
    const d = find('Blue Lotus Hotels');
    expect(() => doMove(priya, d, { to: 'lead' })).toThrow(/reason/);
    doMove(priya, d, { to: 'lead', reason: 'Wrong contact' }); expect(d.stage).toBe('lead');
  });
  it('Revision → Revision 2 needs a reason and bumps the round; Closure → Onboarding marks Won', () => {
    const r = find('Elite Dental Care');
    expect(() => doMove(priya, r, { round2: true })).toThrow(/why/);
    doMove(priya, r, { round2: true, reason: 'New clinic added to scope' }); expect(r.stage).toBe('revision_2'); expect(r.round).toBe(2);
    const c = find('Hotel Grand Palace');
    doTick(priya, c, 'countersigned', { file: { name: 'signed.pdf' } });
    doTick(priya, c, 'onboarding_info', { fields: { billingContact: 'Sameer Khanna', startDate: today, pricingModel: 'Per-minute (managed)', term: 'Yearly contract', billingFrequency: 'Monthly', renewalDate: today } });
    doMove(priya, c, {}); expect(c.stage).toBe('onboarding'); expect(c.status).toBe('won');
  });
  it('only the owner moves a deal; Lost needs a reason; next step cannot be in the past', () => {
    const d = find('Lakeview Resorts');
    expect(() => requireOwn(rohit, d)).toThrow(/owner/);
    expect(() => doNextStep(priya, d, { text: 'x', due: '2020-01-01', lane: 'mine' })).toThrow(/past/);
    expect(() => doLose(priya, d, '', '')).toThrow(/reason/);
    doLose(priya, d, 'Competitor', 'Chose a cheaper IVR'); expect(d.status).toBe('lost');
  });
});
