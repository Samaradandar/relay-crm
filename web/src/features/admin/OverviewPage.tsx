import clsx from 'clsx';
import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import { PageHeader } from '@/components/layout/PageHeader';
import { Avatar, TeamChip } from '@/components/ui/Badge';
import { SelectField } from '@/components/ui/Field';
import { Icon } from '@/components/ui/Icon';
import { CapMeter } from '@/components/ui/Overlay';
import { formatINR } from '@/lib/format';
import { TargetBar, pct } from '@/features/team/TeamBits';

type Sort = 'target' | 'revenue' | 'overdue' | 'stale';

/** PRD 7 for Admins and Super admins: every team side by side, then every rep across the company. */
export function OverviewPage() {
  const o = useQuery({ queryKey: ['companyOverview'], queryFn: api.companyOverview });
  const [team, setTeam] = useState(''); const [sort, setSort] = useState<Sort>('target');
  const reps = useMemo(() => (o.data?.reps ?? []).filter((r) => r.active && (!team || r.teamId === team)).sort((a, b) => sort === 'target' ? pct(b.revenueMonth, b.target) - pct(a.revenueMonth, a.target) : sort === 'revenue' ? b.revenueMonth - a.revenueMonth : sort === 'overdue' ? b.overdue - a.overdue : b.stale - a.stale), [o.data, team, sort]);
  if (!o.data) return <div className="panel h-64 animate-pulse" />;
  const t = o.data.teams; const sum = (k: keyof (typeof t)[number]) => t.reduce((s, x) => s + (x[k] as number), 0);
  const teamOf = (id: string | null) => t.find((x) => x.id === id);

  return <div className="flex flex-col gap-5">
    <PageHeader title="Company overview" subtitle="All teams side by side. Revenue is ex-GST; targets are the sum of each rep’s target." />
    <div className="grid gap-4 md:grid-cols-[minmax(0,2fr)_repeat(4,minmax(0,1fr))]">
      <section className="panel flex flex-col gap-1.5 p-4"><span className="text-[13px] font-medium text-ink-2">Company revenue this month</span><TargetBar value={sum('revenueMonth')} target={sum('target')} /></section>
      <Tile label="Open pipeline" value={formatINR(sum('openValue'), { compact: true })} hint={`${sum('openDeals')} deals · weighted ${formatINR(sum('weighted'), { compact: true })}`} />
      <Tile label="Waiting in the Pool" value={sum('poolWaiting')} hint={`${sum('atCap')} reps at cap`} to="/app/pool" warn={sum('poolWaiting') > 0} />
      <Tile label="Approvals for Admins" value={o.data.pendingApprovals} hint="Never expire; reminder 09:30 IST" to="/app/approvals" />
      <Tile label="Failed background jobs" value={o.data.failedJobs} hint="Emails and live updates" to="/app/admin/jobs" danger={o.data.failedJobs > 0} />
    </div>

    <section className="flex flex-col gap-3" aria-labelledby="teams-h"><h2 id="teams-h" className="m-0 text-heading">Teams</h2>
      <div className="overflow-x-auto rounded-lg border border-line bg-surface-1"><table className="w-full border-collapse text-sm">
        <thead><tr>{['Team', 'Revenue vs target', 'Open deals', 'Weighted', 'Won', 'Overdue', 'Stale', 'Reps at cap', 'In Pool'].map((h, i) => <th key={h} className={clsx('whitespace-nowrap border-b border-line bg-surface-2 px-3 py-2.5 text-xs font-semibold text-ink-2', i > 1 ? 'text-right' : 'text-left')}>{h}</th>)}</tr></thead>
        <tbody>{t.map((x) => <tr key={x.id} className="hover:bg-surface-3">
          <td className="border-b border-line px-3 py-3"><span className="flex flex-col gap-1"><TeamChip name={x.name} color={x.color} /><span className="text-xs text-ink-3">TL {x.leader} · {x.activeReps} active reps</span></span></td>
          <td className="w-[260px] border-b border-line px-3">{x.target ? <TargetBar value={x.revenueMonth} target={x.target} compact /> : <span className="text-ink-3">No active reps</span>}</td>
          <td className="border-b border-line px-3 text-right"><b>{x.openDeals}</b> <span className="text-xs text-ink-3">{formatINR(x.openValue, { compact: true })}</span></td>
          <td className="border-b border-line px-3 text-right">{formatINR(x.weighted, { compact: true })}</td>
          <td className="border-b border-line px-3 text-right text-success">{x.wonMonth}</td>
          <td className={clsx('border-b border-line px-3 text-right', x.overdue && 'font-semibold text-danger')}>{x.overdue}</td>
          <td className={clsx('border-b border-line px-3 text-right', x.stale && 'font-semibold text-warning')}>{x.stale}</td>
          <td className="border-b border-line px-3 text-right">{x.atCap}/{x.activeReps}</td>
          <td className={clsx('border-b border-line px-3 text-right', x.poolWaiting && 'font-semibold text-warning')}>{x.poolWaiting}</td>
        </tr>)}</tbody>
      </table></div>
      {t.some((x) => x.activeReps === 0) && <p className="m-0 flex items-center gap-1.5 text-small text-warning"><Icon name="alert" size={14} />{t.filter((x) => x.activeReps === 0).map((x) => x.name).join(', ')} has no active sales rep, so its leads wait in the Pool. Add a rep or change the assignment rules.</p>}
    </section>

    <section className="flex flex-col gap-3" aria-labelledby="reps-h">
      <div className="flex flex-wrap items-end justify-between gap-3"><h2 id="reps-h" className="m-0 text-heading">Every rep</h2>
        <div className="flex gap-3"><SelectField label="Team" value={team} onChange={(e) => setTeam(e.target.value)} options={[{ value: '', label: 'All teams' }, ...t.map((x) => ({ value: x.id, label: x.name }))]} className="w-40" />
          <SelectField label="Sort by" value={sort} onChange={(e) => setSort(e.target.value as Sort)} options={[{ value: 'target', label: '% of target' }, { value: 'revenue', label: 'Revenue' }, { value: 'overdue', label: 'Most overdue' }, { value: 'stale', label: 'Most stale' }]} className="w-40" /></div></div>
      <div className="overflow-x-auto rounded-lg border border-line bg-surface-1"><table className="w-full border-collapse text-sm">
        <thead><tr>{['Rep', 'Team', 'Revenue vs target', 'Active vs cap', 'Overdue', 'Stale', 'Won', 'Lost'].map((h, i) => <th key={h} className={clsx('whitespace-nowrap border-b border-line bg-surface-2 px-3 py-2.5 text-xs font-semibold text-ink-2', i > 3 ? 'text-right' : 'text-left')}>{h}</th>)}</tr></thead>
        <tbody>{reps.map((r) => { const tm = teamOf(r.teamId); return <tr key={r.id} className="hover:bg-surface-3">
          <td className="border-b border-line px-3 py-2.5"><Link to={`/app/team/${r.id}`} className="flex items-center gap-2 text-ink no-underline hover:text-brand-ink"><Avatar name={r.name} size={26} /><span className="font-semibold">{r.name}</span></Link></td>
          <td className="border-b border-line px-3">{tm && <TeamChip name={tm.name} color={tm.color} />}</td>
          <td className="w-[220px] border-b border-line px-3"><TargetBar value={r.revenueMonth} target={r.target} compact /></td>
          <td className="w-[180px] border-b border-line px-3"><CapMeter active={r.activeLeads} cap={r.cap} /></td>
          <td className={clsx('border-b border-line px-3 text-right', r.overdue && 'font-semibold text-danger')}>{r.overdue}</td>
          <td className={clsx('border-b border-line px-3 text-right', r.stale && 'font-semibold text-warning')}>{r.stale}</td>
          <td className="border-b border-line px-3 text-right">{r.wonMonth}</td><td className="border-b border-line px-3 text-right">{r.lostMonth}</td>
        </tr>; })}</tbody>
      </table></div>
    </section>
  </div>;
}

function Tile({ label, value, hint, to, warn, danger }: { label: string; value: React.ReactNode; hint: string; to?: string; warn?: boolean; danger?: boolean }) {
  const body = <><span className="text-[13px] font-medium text-ink-2">{label}</span><span className={clsx('text-metric', danger && 'text-danger', warn && 'text-warning')}>{value}</span><span className="text-xs text-ink-3">{hint}</span></>;
  const cls = clsx('panel flex flex-col gap-0.5 p-4 no-underline', warn && '!border-warning', danger && '!border-danger', to && 'hover:border-line-control');
  return to ? <Link to={to} className={cls + ' text-ink'}>{body}</Link> : <div className={cls}>{body}</div>;
}
