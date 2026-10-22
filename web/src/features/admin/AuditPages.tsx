import clsx from 'clsx';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import type { Role } from '@/api/types';
import { ROLE_LABEL } from '@/auth/permissions';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { SelectField } from '@/components/ui/Field';
import { Icon } from '@/components/ui/Icon';
import { useReasonPrompt } from '@/components/ui/ReasonPrompt';
import { formatDateTimeIST, formatRelativeIST } from '@/lib/format';
import { stageLabel, valueText } from '@/features/deals/bits';

/** PRD 12: every change — who, when, old → new, reason. Kept forever; nothing is erased. */
export function AuditPage() {
  const q = useQuery({ queryKey: ['audit'], queryFn: api.auditLog });
  const [area, setArea] = useState(''); const [actor, setActor] = useState(''); const [search, setSearch] = useState(''); const [size, setSize] = useState(25); const [page, setPage] = useState(0);
  const rows = useMemo(() => (q.data ?? []).filter((r) => (!area || r.area === area) && (!actor || r.actor === actor) && (!search || `${r.action} ${r.target} ${r.reason} ${r.to}`.toLowerCase().includes(search.toLowerCase()))), [q.data, area, actor, search]);
  const actors = [...new Set((q.data ?? []).map((r) => r.actor))].sort();
  const shown = rows.slice(page * size, page * size + size); const pages = Math.max(1, Math.ceil(rows.length / size));
  return <div className="flex flex-col gap-4">
    <PageHeader title="Audit log" subtitle="Every change across Relay with who, when, old and new value, and the reason. Rows are never edited or deleted." />
    <div className="flex flex-wrap items-end gap-3">
      <label className="flex h-9 w-64 items-center gap-2 rounded-md border border-line-control bg-surface-1 px-2.5 text-ink-3"><Icon name="search" /><input value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} placeholder="Search action, record or reason" aria-label="Search" className="min-w-0 flex-1 border-0 bg-transparent text-sm text-ink outline-none" /></label>
      <SelectField label="Area" value={area} onChange={(e) => { setArea(e.target.value); setPage(0); }} options={[{ value: '', label: 'Everything' }, ...['Deals', 'Assignment', 'Pipeline', 'Catalog', 'Users', 'Teams', 'Exports'].map((x) => ({ value: x, label: x }))]} className="w-40" />
      <SelectField label="Who" value={actor} onChange={(e) => { setActor(e.target.value); setPage(0); }} options={[{ value: '', label: 'Anyone' }, ...actors.map((x) => ({ value: x, label: x }))]} className="w-48" />
    </div>
    <div className="overflow-x-auto rounded-lg border border-line bg-surface-1"><table className="w-full border-collapse text-sm">
      <thead><tr>{['When (IST)', 'Who', 'Area', 'What', 'Record', 'Old → new', 'Reason'].map((h) => <th key={h} className="whitespace-nowrap border-b border-line bg-surface-2 px-3 py-2.5 text-left text-xs font-semibold text-ink-2">{h}</th>)}</tr></thead>
      <tbody>{shown.map((r) => <tr key={r.id} className="align-top hover:bg-surface-3">
        <td className="whitespace-nowrap border-b border-line px-3 py-2.5 text-ink-2">{formatDateTimeIST(r.at).replace(' IST', '')}</td>
        <td className="whitespace-nowrap border-b border-line px-3 py-2.5"><span className="flex flex-col"><span className="font-semibold">{r.actor}</span><span className="text-xs text-ink-3">{r.role === 'system' ? 'System' : ROLE_LABEL[r.role as Role] ?? ''}</span></span></td>
        <td className="border-b border-line px-3 py-2.5"><Badge tone={r.area === 'Deals' ? 'neutral' : 'brand'}>{r.area}</Badge></td>
        <td className="border-b border-line px-3 py-2.5">{r.action}</td>
        <td className="border-b border-line px-3 py-2.5">{r.dealId ? <Link to={`/app/deals/${r.dealId}`} className="text-brand-ink">{r.target}</Link> : r.target}</td>
        <td className="max-w-[280px] border-b border-line px-3 py-2.5 text-ink-2">{r.from && <s className="text-ink-3">{r.from}</s>}{r.from && r.to ? ' → ' : ''}{r.to}</td>
        <td className="max-w-[260px] border-b border-line px-3 py-2.5 text-ink-2">{r.reason || <span className="text-ink-3">—</span>}</td>
      </tr>)}</tbody>
    </table>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-3 py-2 text-[13px] text-ink-2"><span>{rows.length} entries</span>
        <span className="flex items-center gap-2">Rows <select aria-label="Rows per page" value={size} onChange={(e) => { setSize(Number(e.target.value)); setPage(0); }} className="h-7 rounded-md border border-line-control bg-surface-1 px-1.5 text-[13px]">{[10, 25, 50].map((n) => <option key={n}>{n}</option>)}</select><Button size="sm" variant="quiet" disabled={page === 0} onClick={() => setPage(page - 1)} aria-label="Previous page">‹</Button>{page + 1} / {pages}<Button size="sm" variant="quiet" disabled={page >= pages - 1} onClick={() => setPage(page + 1)} aria-label="Next page">›</Button></span></div>
    </div>
  </div>;
}

/** PRD 14: deleted records are hidden, never erased, and can be restored (TL team, Admin, Super admin). */
export function DeletedPage() {
  const qc = useQueryClient(); const q = useQuery({ queryKey: ['deleted'], queryFn: api.deletedDeals });
  const { ask, element } = useReasonPrompt(() => ['deleted', 'deals', 'audit'].forEach((k) => qc.invalidateQueries({ queryKey: [k] })));
  return <div className="flex flex-col gap-4">
    <PageHeader title="Deleted records" subtitle="Deleted deals are hidden from every page but kept forever with their history. Restore puts them back exactly as they were." />
    {(q.data ?? []).length === 0 ? <div className="panel flex items-center gap-3 p-6 text-ink-2"><Icon name="check" className="text-success" />Nothing deleted. Team Leaders and Admins can delete a deal from its page (More → Delete deal).</div>
      : <div className="overflow-x-auto rounded-lg border border-line bg-surface-1"><table className="w-full border-collapse text-sm"><thead><tr>{['Deal', 'Stage', 'Value', 'Owner', 'Deleted', 'Reason', ''].map((h) => <th key={h} className="whitespace-nowrap border-b border-line bg-surface-2 px-3 py-2.5 text-left text-xs font-semibold text-ink-2">{h}</th>)}</tr></thead>
        <tbody>{q.data!.map((x) => <tr key={x.deal.id}><td className="border-b border-line px-3 py-2.5 font-semibold">{x.deal.company}</td><td className="border-b border-line px-3">{stageLabel(x.deal)}</td><td className="border-b border-line px-3">{valueText(x.deal)}</td><td className="border-b border-line px-3">{x.deal.ownerName}</td><td className="border-b border-line px-3 text-ink-2">{x.by} · {formatRelativeIST(x.at)}</td><td className="border-b border-line px-3 text-ink-2">{x.reason || '—'}</td>
          <td className="border-b border-line px-3 text-right"><Button size="sm" icon="undo" onClick={() => ask({ title: `Restore ${x.deal.company}?`, action: 'Restore', done: `${x.deal.company} restored`, run: (r) => api.restoreDeal(x.deal.id, r) })}>Restore</Button></td></tr>)}</tbody></table></div>}
    {element}
  </div>;
}

const Y = 'Yes', N = 'No';
const MATRIX: [string, string, string, string, string][] = [
  ['See deals, accounts and activities', 'Own', 'Team', 'All', 'All'], ['Lead Pool', N, 'Team’s share', 'All', 'All'], ['Create a lead', Y, Y, 'Yes + reason', Y],
  ['Assign or reassign a lead', N, 'Team + reason', 'Anyone + reason', 'Anyone'], ['Tick criteria and move forward', 'Own', 'Own deals', 'Own deals', 'Own deals'],
  ['Verify ticks', N, 'Team', 'All + reason', 'All'], ['Override a gate / skip a stage', N, 'Team (not own) + reason', 'All + reason', 'All'], ['Close as Lost', 'Own + reason', 'Team + reason', 'All + reason', 'All'],
  ['Reopen a Lost deal', N, 'Team + reason', 'All + reason', 'All'], ['Change deal value > 10%', 'Needs TL approval', 'Needs Admin approval', 'All + reason', 'All'],
  ['Discount up to 10%', 'Needs TL approval', 'Yes + reason', 'Yes + reason', Y], ['Discount above 10% or below floor', 'Needs Admin approval', 'Needs Admin approval', 'Yes + reason', Y],
  ['Move a deal to another team', N, 'Request only', 'Approve or move + reason', 'Approve or move'], ['Soft-delete / restore a deal', N, 'Team + reason', 'All + reason', 'All'],
  ['Record payment status', 'View only', 'Team + reason', 'All + reason', 'All'], ['Set targets and incentive %', N, 'Team + reason', 'All + reason', 'All'],
  ['Edit pipeline, criteria, probabilities', N, N, 'Yes + reason', Y], ['Assignment rules, default team, caps', N, N, 'Yes + reason', Y], ['Catalog, price list, floor price', N, N, 'Yes + reason', Y],
  ['Create Users and Team Leaders', N, 'Request only', 'Yes + reason', Y], ['Create, promote or remove Admins / Super admins', N, N, N, Y], ['Approve requests', N, 'Own reps’ team-level requests', 'All + reason', 'All']
];
/** Read-only view of the PRD 4.4 matrix so the Super admin can see exactly who can do what. */
export function PermissionsPage() {
  return <div className="flex flex-col gap-4">
    <PageHeader title="Roles and permissions" subtitle="What each role can do (PRD 4.4). A reason is required for Users, Team Leaders and Admins on important actions, and optional for a Super admin." />
    <div className="overflow-x-auto rounded-lg border border-line bg-surface-1"><table className="w-full border-collapse text-sm">
      <thead><tr>{['Action', 'Sales rep', 'Team Leader', 'Admin', 'Super admin'].map((h) => <th key={h} className="whitespace-nowrap border-b border-line bg-surface-2 px-3 py-2.5 text-left text-xs font-semibold text-ink-2">{h}</th>)}</tr></thead>
      <tbody>{MATRIX.map((r) => <tr key={r[0]} className="hover:bg-surface-3"><td className="border-b border-line px-3 py-2.5 font-semibold">{r[0]}</td>{r.slice(1).map((c, i) => <td key={i} className={clsx('border-b border-line px-3 py-2.5', c === N ? 'text-ink-3' : c.startsWith('Needs') || c.startsWith('Request') ? 'text-warning' : 'text-ink')}>{c === N ? '—' : c}</td>)}</tr>)}</tbody>
    </table></div>
  </div>;
}
