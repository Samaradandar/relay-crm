import type { Role } from '@/api/types';
import type { IconName } from '@/components/ui/Icon';
import { ENV } from '@/config/env';
import { PIPELINES, stagePath } from '@/lib/pipeline';

export interface NavItem { id: string; label: string; icon: IconName; path: string; roles: Role[]; phase: number; group?: string; /** Page of a released phase whose screen is not built yet. */ pending?: boolean; /** Last phase this entry shows in (replaced later). */ until?: number; /** Stage entries render as an indented list with a count. */ stage?: boolean }

const ALL: Role[] = ['user', 'team_leader', 'admin', 'super_admin'];
const ADMINS: Role[] = ['admin', 'super_admin'];

/**
 * One entry per page, tagged with the build phase that delivers it. Pages from a phase above
 * VITE_RELEASE_PHASE stay hidden, so each phase's frontend merges together with its backend.
 * Phase 2+ entries are listed so the map stays complete; their routes arrive with their phase.
 */
const stageItems = (p: 'new_business' | 'existing_customer', group: string, phase: number): NavItem[] =>
  PIPELINES[p].stages.filter((st) => !st.mergedInto).map((st) => ({ id: `stage:${p}:${st.key}`, label: st.label, icon: 'board', path: stagePath(p, st.key), roles: ALL, phase: st.key === 'onboarding' ? 4 : phase, group, stage: true }));

export const NAV: NavItem[] = [
  { id: 'home', label: 'Home', icon: 'today', path: '/app', roles: ALL, phase: 1, until: 2 },
  { id: 'today', label: 'Today', icon: 'today', path: '/app', roles: ['user'], phase: 3 },
  { id: 'home3', label: 'Home', icon: 'today', path: '/app', roles: ['team_leader'], phase: 3 },
  { id: 'overview', label: 'Company overview', icon: 'chart', path: '/app/overview', roles: ADMINS, phase: 3 },
  { id: 'mytoday', label: 'My Today', icon: 'clock', path: '/app/my-today', roles: ['team_leader'], phase: 3 },
  { id: 'myteam', label: 'My team', icon: 'users', path: '/app/team', roles: ['team_leader'], phase: 3 },
  { id: 'board', label: 'Pipeline board', icon: 'board', path: '/app/board', roles: ALL, phase: 3 },
  { id: 'pool', label: 'Lead Pool', icon: 'pool', path: '/app/pool', roles: ['team_leader', ...ADMINS], phase: 2 },
  { id: 'transfers', label: 'Transfers', icon: 'users', path: '/app/transfers', roles: ['team_leader', ...ADMINS], phase: 2 },
  { id: 'capture-review', label: 'Capture review', icon: 'inbox', path: '/app/leads/review', roles: ['team_leader', ...ADMINS], phase: 6 },
  { id: 'import-csv', label: 'Import leads', icon: 'file', path: '/app/leads/import', roles: ['team_leader', ...ADMINS], phase: 2 },
  { id: 'approvals', label: 'Approvals', icon: 'inbox', path: '/app/approvals', roles: ALL, phase: 1 },
  { id: 'incentives', label: 'Incentives', icon: 'trophy', path: '/app/incentives', roles: ALL, phase: 6 },
  { id: 'notifications', label: 'Notifications', icon: 'bell', path: '/app/notifications', roles: ALL, phase: 1 },
  { id: 'mydash', label: 'My dashboard', icon: 'chart', path: '/app/me/dashboard', roles: ['user'], phase: 6, group: 'My numbers' },
  { id: 'leaderboard', label: 'Leaderboard', icon: 'trophy', path: '/app/leaderboard', roles: ALL, phase: 6, group: 'My numbers' },
  { id: 'reports', label: 'Reports', icon: 'chart', path: '/app/reports', roles: ALL, phase: 6, group: 'My numbers' },
  ...stageItems('new_business', 'New business', 3),
  ...stageItems('existing_customer', 'Existing customers', 5),
  { id: 'won', label: 'Won', icon: 'trophy', path: '/app/won', roles: ALL, phase: 3, group: 'Closed' },
  { id: 'lost', label: 'Lost', icon: 'x', path: '/app/lost', roles: ALL, phase: 3, group: 'Closed' },
  { id: 'churn', label: 'Churn History', icon: 'alert', path: '/app/churn', roles: ALL, phase: 5, group: 'Closed' },
  { id: 'accounts', label: 'Accounts', icon: 'customers', path: '/app/accounts', roles: ALL, phase: 4, group: 'Customers' },
  { id: 'customers', label: 'Customers', icon: 'users', path: '/app/customers', roles: ALL, phase: 4, group: 'Customers' },
  { id: 'at-risk', label: 'At risk', icon: 'alert', path: '/app/customers/at-risk', roles: ['team_leader', ...ADMINS], phase: 5, group: 'Customers' },
  { id: 'monitoring', label: 'Team monitoring', icon: 'users', path: '/app/teams-monitor', roles: ADMINS, phase: 3, group: 'Team', pending: true },
  { id: 'targets', label: 'Targets', icon: 'chart', path: '/app/team/targets', roles: ['team_leader', ...ADMINS], phase: 6, group: 'Team' },
  { id: 'incentive-rates', label: 'Set incentive rates', icon: 'chart', path: '/app/team/incentive-rates', roles: ADMINS, phase: 6, group: 'Team' },
  { id: 'users', label: 'Users', icon: 'users', path: '/app/admin/users', roles: ADMINS, phase: 1, group: 'Admin' },
  { id: 'teams', label: 'Teams', icon: 'customers', path: '/app/admin/teams', roles: ADMINS, phase: 1, group: 'Admin' },
  { id: 'audit', label: 'Audit log', icon: 'file', path: '/app/admin/audit', roles: ADMINS, phase: 1, group: 'Admin' },
  { id: 'deleted', label: 'Deleted records', icon: 'undo', path: '/app/deleted', roles: ['team_leader', ...ADMINS], phase: 3, group: 'Admin' },
  { id: 'jobs', label: 'Failed jobs', icon: 'alert', path: '/app/admin/jobs', roles: ADMINS, phase: 1, group: 'Admin' },
  { id: 'perms', label: 'Roles & permissions', icon: 'shield', path: '/app/admin/permissions', roles: ['super_admin'], phase: 1, group: 'Admin' },
  { id: 'set-assign', label: 'Assignment', icon: 'pool', path: '/app/settings/assignment', roles: ADMINS, phase: 2, group: 'Settings' },
  { id: 'set-pipe', label: 'Pipeline', icon: 'board', path: '/app/settings/pipeline', roles: ADMINS, phase: 3, group: 'Settings' },
  { id: 'set-capture', label: 'Lead capture', icon: 'pool', path: '/app/settings/capture', roles: ADMINS, phase: 6, group: 'Settings' },
  { id: 'set-lists', label: 'Settings lists', icon: 'file', path: '/app/settings/lists', roles: ADMINS, phase: 3, group: 'Settings' },
  { id: 'set-proposal', label: 'Proposal template', icon: 'file', path: '/app/settings/proposal-template', roles: ADMINS, phase: 4, group: 'Settings' },
  { id: 'set-cat', label: 'Product catalog', icon: 'customers', path: '/app/settings/catalog', roles: ADMINS, phase: 3, group: 'Settings' },
  { id: 'set-churn', label: 'Churn reasons', icon: 'alert', path: '/app/settings/churn-reasons', roles: ADMINS, phase: 5, group: 'Settings' }
];

export function navFor(role: Role, phase = ENV.releasePhase): NavItem[] {
  return NAV.filter((n) => n.phase <= phase && (n.until == null || phase <= n.until) && !n.pending && n.roles.includes(role));
}

/** Where each role lands after signing in. */
export function homeFor(role: Role): string {
  switch (role) {
    case 'admin':
    case 'super_admin': return '/app/approvals';
    default: return '/app';
  }
}
