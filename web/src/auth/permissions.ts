import type { Me, Permission, Role } from '@/api/types';

/**
 * Phase 1 slice of the PRD permission matrix (section 4.4 I, J, L), used ONLY to decide what the
 * screen shows. The backend is the source of truth: in live mode /me returns the real permission list.
 */
const P = (key: string, scope: Permission['scope'], mode: Permission['mode'] = 'allow', reason: Permission['reason'] = 'none'): Permission => ({ key, scope, mode, reason });
const MATRIX: Record<Role, Permission[]> = {
  user: [P('audit.view', 'own'), P('profile.view_own', 'own'), P('approval.request', 'own', 'allow', 'required'), P('password.reset', 'own', 'allow', 'none')],
  team_leader: [P('audit.view', 'team'), P('profile.view_own', 'own'), P('profile.view_others', 'team'), P('approval.request', 'team', 'allow', 'required'), P('approval.decide', 'team', 'allow', 'required'), P('user.manage', 'team', 'request_only', 'required')],
  admin: [P('audit.view', 'all'), P('profile.view_own', 'all'), P('profile.view_others', 'all'), P('approval.decide', 'all', 'allow', 'required'), P('user.manage', 'all', 'allow', 'required'), P('team.manage', 'all', 'allow', 'required'), P('team.colour.change', 'all', 'allow', 'required'), P('password.reset', 'all', 'allow', 'required'), P('jobs.view', 'all')],
  super_admin: [P('audit.view', 'all'), P('profile.view_own', 'all'), P('profile.view_others', 'all'), P('approval.decide', 'all', 'allow', 'optional'), P('user.manage', 'all', 'allow', 'optional'), P('team.manage', 'all', 'allow', 'optional'), P('team.colour.change', 'all', 'allow', 'optional'), P('admin.manage', 'all', 'allow', 'optional'), P('password.reset', 'all', 'allow', 'optional'), P('jobs.view', 'all')]
};
export const permissionsFor = (role: Role) => MATRIX[role];

export function can(me: Me | null, key: string): Permission | null {
  return me?.permissions.find((p) => p.key === key && p.scope !== 'none') ?? null;
}

/** Reason box behaviour for an action: required for User/TL/Admin on important actions, optional for Super admin. */
export function reasonFor(me: Me | null, key: string): 'required' | 'optional' | 'none' {
  const p = can(me, key);
  if (!p) return 'none';
  if (me?.role === 'super_admin') return 'optional';
  if (me?.role === 'admin') return 'required';
  return p.reason;
}

export const ROLE_LABEL: Record<Role, string> = { user: 'Sales rep', team_leader: 'Team Leader', admin: 'Admin', super_admin: 'Super admin' };
