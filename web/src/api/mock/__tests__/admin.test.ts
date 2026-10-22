import { describe, expect, it } from 'vitest';
import { db } from '../db';
import { leads } from '../leads';
import '../deals'; import '../team';
import * as AD from '../admin';
import { PIPELINES } from '@/lib/pipeline';
import { permissionsFor } from '@/auth/permissions';
import type { Me } from '../../types';

const meOf = (id: string): Me => { const u = db.users.find((x) => x.id === id)!; return { ...u, team: null, permissions: permissionsFor(u.role) }; };
const arjun = meOf('u-arjun'); const vikram = meOf('u-vikram'); const rohit = meOf('u-rohit');

describe('Admin and Super admin rules', () => {
  it('Admins need a reason on every change; Super admin does not', () => {
    expect(() => AD.setCap(arjun, 'u-ankit', 22)).toThrow(/reason/);
    expect(() => AD.setCap(rohit, 'u-ankit', 22, 'x')).toThrow(/Admins/);
    AD.setCap(vikram, 'u-ankit', 22);
    expect(db.users.find((u) => u.id === 'u-ankit')!.leadCap).toBe(22);
    expect(AD.audit[0]).toMatchObject({ action: 'Lead cap changed', from: '20', to: '22', actor: 'Vikram Singh' });
  });
  it('raising a cap re-runs waiting leads into the Pool owner', () => {
    const before = leads.filter((l) => l.status === 'pooled').length;
    AD.setCap(arjun, 'u-divya', 23, 'Divya back from leave');
    expect(leads.filter((l) => l.status === 'pooled').length).toBeLessThan(before);
  });
  it('testing a rule is read-only', () => {
    const a = AD.testRule({ source: 'Event' }); const b = AD.testRule({ source: 'Event' });
    expect(a).toEqual(b); expect(a.target).toBe('Enterprise');
  });
  it('publishing validates, creates a version, and rollback republishes', () => {
    const draft = JSON.parse(JSON.stringify({ new_business: PIPELINES.new_business, existing_customer: PIPELINES.existing_customer }));
    draft.new_business.stages[3].probability = 150;
    expect(() => AD.publishPipeline(vikram, draft)).toThrow(/0–100/);
    draft.new_business.stages[3].probability = 25;
    const v = AD.publishPipeline(arjun, draft, 'Measured win rate is higher');
    expect(PIPELINES.new_business.stages[3].probability).toBe(25);
    AD.rollbackPipeline(vikram, v.version - 1);
    expect(PIPELINES.new_business.stages[3].probability).toBe(20);
  });
  it('only a Super admin touches Admins, and the last Super admin stays', () => {
    expect(() => AD.updateUser(arjun, 'u-arjun', { active: false }, 'x')).toThrow();
    expect(() => AD.updateUser(arjun, 'u-priya', { role: 'admin' }, 'x')).toThrow(/Super admin/);
    expect(() => AD.updateUser(vikram, 'u-vikram', { active: false })).toThrow();
  });
  it('delete hides, restore brings back', () => {
    const d = leads.find((l) => l.company === 'Lakeview Resorts')!;
    AD.deleteDeal(arjun, d.id, 'Duplicate of an older lead'); expect(AD.isDeleted(d)).toBe(true);
    AD.restoreDeal(vikram, d.id); expect(AD.isDeleted(d)).toBe(false);
  });
});
