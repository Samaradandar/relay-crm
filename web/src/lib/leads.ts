import type { AssignmentMethod, WaitingReason } from '@/api/types';

/** Words for assignment fields, shared by every screen (never show raw enum values). */
export const WAITING_LABEL: Record<WaitingReason, string> = {
  no_rule_match: 'No rule matched and no default team',
  all_reps_at_cap: 'Every eligible rep is at their cap',
  no_active_reps: 'No active rep in the target',
  awaiting_team_leader: 'Held for your Team Leader to pick up by hand'
};
export const WAITING_SHORT: Record<WaitingReason, string> = { all_reps_at_cap: 'All reps at cap', no_active_reps: 'No active rep', no_rule_match: 'No rule matched', awaiting_team_leader: 'Being assigned' };
export const METHOD_LABEL: Record<AssignmentMethod, string> = { rule: 'Rule', round_robin: 'Round-robin', manual: 'Manual' };

/** "Round-robin", "Rule: Events → Enterprise", "By Rohit Bansal". */
export function howAssigned(l: { method?: AssignmentMethod; ruleName?: string; assignedByName?: string }): string {
  if (l.method === 'manual') return l.assignedByName ? `By ${l.assignedByName}` : 'Manual';
  if (l.method === 'rule') return l.ruleName ? `Rule: ${l.ruleName}` : 'Rule';
  if (l.method === 'round_robin') return 'Round-robin';
  // no assignment at all: an existing-customer request goes straight to the account owner
  return 'Account owner';
}
