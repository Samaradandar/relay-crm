import { describe, expect, it } from 'vitest';
import { homeFor, navFor } from '../navigation';

describe('role routing', () => {
  it('sends Admins and Super admins to Approvals, others to Home', () => {
    expect(homeFor('admin')).toBe('/app/approvals');
    expect(homeFor('super_admin')).toBe('/app/approvals');
    expect(homeFor('team_leader')).toBe('/app');
    expect(homeFor('user')).toBe('/app');
  });
  it('shows only released pages the role can open', () => {
    const rep = navFor('user', 1).map((n) => n.id);
    expect(rep).toEqual(['home', 'approvals', 'notifications']);
    expect(navFor('admin', 1).map((n) => n.id)).toContain('users');
    expect(navFor('user', 2).map((n) => n.id)).not.toContain('pool');
    expect(navFor('team_leader', 2).map((n) => n.id)).toContain('pool');
  });
});
