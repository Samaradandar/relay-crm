import type { ApprovalRequest, Lead, RepLoad, Team } from '@/api/types';
import { ApprovalBadge, Avatar, Badge, TeamChip } from '@/components/ui/Badge';
import { CapMeter } from '@/components/ui/Overlay';
import { Icon } from '@/components/ui/Icon';
import { formatRelativeIST } from '@/lib/format';
import { howAssigned } from '@/lib/leads';

/** Each rep's active leads against their cap, and how many they got this week (PRD 6 distribution panel). */
export function TeamLoad({ reps, teams, showTeam }: { reps: RepLoad[]; teams: Team[]; showTeam?: boolean }) {
  const rows = [...reps].sort((a, b) => Number(b.active) - Number(a.active) || b.activeLeads / b.cap - a.activeLeads / a.cap);
  return <div className="overflow-x-auto rounded-lg border border-line bg-surface-1">
    <table className="w-full border-collapse text-sm">
      <thead><tr>{['Rep', ...(showTeam ? ['Team'] : []), 'Active leads vs cap', 'This week', 'Last lead'].map((h, i) => <th key={h} className={`whitespace-nowrap border-b border-line bg-surface-2 px-3 py-2.5 text-xs font-semibold text-ink-2 ${i >= (showTeam ? 3 : 2) ? 'text-right' : 'text-left'}`}>{h}</th>)}</tr></thead>
      <tbody>{rows.map((r) => { const t = teams.find((x) => x.id === r.teamId); return <tr key={r.id} className={r.active ? 'hover:bg-surface-3' : 'opacity-60'}>
        <td className="whitespace-nowrap border-b border-line px-3 py-2"><span className="flex items-center gap-2.5"><Avatar name={r.name} size={26} /><span className="text-body-strong">{r.name}</span>{!r.active && <Badge>Inactive</Badge>}</span></td>
        {showTeam && <td className="whitespace-nowrap border-b border-line px-3">{t && <TeamChip name={t.name} color={t.color} />}</td>}
        <td className="border-b border-line px-3"><div className="max-w-[260px]"><CapMeter active={r.activeLeads} cap={r.cap} /></div></td>
        <td className="whitespace-nowrap border-b border-line px-3 text-right">{r.assignedThisWeek}</td>
        <td className="whitespace-nowrap border-b border-line px-3 text-right text-ink-2">{r.lastAssignedAt ? formatRelativeIST(r.lastAssignedAt) : '—'}</td>
      </tr>; })}</tbody>
    </table>
  </div>;
}

/** Cross-team transfer requests (PRD 4.5a) in plain words. */
export function TransferList({ items, myTeamId, myId, teams }: { items: ApprovalRequest[]; myTeamId?: string | null; myId?: string; teams: Team[] }) {
  if (items.length === 0) return <p className="m-0 text-small text-ink-2">No transfer requests. To move a deal to or from another team, open the deal and choose “Request transfer”.</p>;
  return <ul className="m-0 flex list-none flex-col gap-3 p-0">{items.map((a) => {
    const other = a.teamIds?.find((t) => t !== myTeamId); const ot = teams.find((t) => t.id === other);
    return <li key={a.id} className="flex flex-col gap-1.5 rounded-md border border-line p-3">
      <span className="flex items-start justify-between gap-2"><span className="text-body-strong">{a.title}</span><ApprovalBadge status={a.status} /></span>
      <span className="text-small text-ink-2">{a.summary}</span>
      <span className="flex flex-wrap items-center justify-between gap-2 text-xs text-ink-3"><span>{a.requestedByName} · {formatRelativeIST(a.requestedAt)}</span>{ot && <TeamChip name={ot.name} color={ot.color} />}</span>
      {a.status === 'pending' && <span className="text-xs text-ink-3">Admins decide. {a.requestedBy !== myId ? 'You can add a comment for the approver; you cannot block it.' : 'You get a notification when it is decided.'}</span>}
    </li>;
  })}</ul>;
}

/** Latest assignments with how each was made: rule, round-robin or by name. */
export function RecentAssignments({ leads }: { leads: Lead[] }) {
  if (leads.length === 0) return <p className="m-0 text-small text-ink-2">Nothing assigned yet.</p>;
  return <ul className="m-0 flex list-none flex-col p-0">{leads.map((l) => <li key={l.id} className="flex items-center gap-3 border-t border-line py-2.5 first:border-t-0 first:pt-0">
    <Avatar name={l.ownerName ?? '?'} size={26} />
    <span className="flex min-w-0 flex-1 flex-col"><span className="truncate text-[13px] font-semibold">{l.company} <span className="font-normal text-ink-2">→ {l.ownerName}</span></span><span className="truncate text-xs text-ink-3">{howAssigned(l)} · {formatRelativeIST(l.assignedAt!)}</span></span>
    {l.overCap && <span title="Assigned while the rep was at their cap"><Icon name="alert" className="text-warning" /></span>}
  </li>)}</ul>;
}
