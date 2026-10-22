import clsx from 'clsx';
import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import type { Lead } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { PageHeader } from '@/components/layout/PageHeader';
import { Avatar, Badge, TeamChip } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Banner } from '@/components/ui/Feedback';
import { Icon } from '@/components/ui/Icon';
import { CapMeter } from '@/components/ui/Overlay';
import { formatDueIST, formatRelativeIST } from '@/lib/format';
import { ACTIVITY_LABEL } from '@/lib/pipeline';
import { nudgeDue, stageLabel } from '@/features/deals/bits';
import { AssignTaskDialog, RepCard, TargetBar, TeamStrip } from './TeamBits';

/** PRD 7 Team monitoring: one card per rep plus the team totals. */
export function MyTeamPage() {
  const { me } = useAuth(); const o = useQuery({ queryKey: ['teamOverview'], queryFn: api.teamOverview });
  return <div className="flex flex-col gap-5">
    <PageHeader title="My team" subtitle={<span className="inline-flex items-center gap-2">{me?.team && <TeamChip name={me.team.name} color={me.team.color} />}Workload, follow-ups and outcomes for every rep. Click a number to open the deals behind it.</span>} />
    {o.data && <TeamStrip o={o.data} />}
    <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">{(o.data?.reps ?? []).map((r) => <RepCard key={r.id} r={r} />)}</div>
  </div>;
}

/** PRD 5 Sales Team Profile + Feature 6: the TL opens a rep's Today and can assign tasks. */
export function RepPage() {
  const { id = '' } = useParams(); const q = useQuery({ queryKey: ['repProfile', id], queryFn: () => api.repProfile(id) });
  const [task, setTask] = useState<{ dealId?: string } | null>(null);
  if (q.isError) return <Banner tone="danger" title="This person is not in your team" />;
  if (!q.data) return <div className="panel h-64 animate-pulse" />;
  const { summary: r, today, lost, recent } = q.data;
  const deals = [...today.mine, ...today.theirs];
  const max = Math.max(1, ...r.byStage.map((s) => s.count));
  return <div className="flex flex-col gap-5">
    <nav className="text-[13px] text-ink-3"><Link to="/app/team" className="text-ink-2 no-underline hover:text-ink">My team</Link> / <span className="text-ink">{r.name}</span></nav>
    <header className="panel flex flex-wrap items-center gap-4 p-5">
      <Avatar name={r.name} size={52} />
      <div className="flex min-w-0 flex-1 flex-col gap-1"><h1 className="m-0 text-display">{r.name}</h1><p className="m-0 text-small text-ink-2">Sales rep · {r.email} · {r.active ? 'Active' : 'Inactive'} · {r.generated} leads generated · {r.assigned} assigned</p></div>
      <div className="flex gap-2"><Link to={`/app/board?owner=${r.id}`} className="inline-flex h-9 items-center gap-2 rounded-md border border-line-control bg-surface-1 px-3.5 text-sm font-semibold text-ink no-underline hover:bg-surface-3"><Icon name="board" />Their board</Link><Button variant="primary" icon="plus" onClick={() => setTask({})} disabled={!deals.length}>Assign task</Button></div>
    </header>

    <div className="grid gap-4 md:grid-cols-[minmax(0,1.4fr)_repeat(4,minmax(0,1fr))]">
      <section className="panel p-4"><span className="text-[13px] font-medium text-ink-2">Revenue this month</span><TargetBar value={r.revenueMonth} target={r.target} /></section>
      <section className="panel flex flex-col gap-2 p-4"><span className="text-[13px] font-medium text-ink-2">Active leads vs cap</span><span className="text-metric">{r.activeLeads}<span className="text-base font-medium text-ink-3"> / {r.cap}</span></span><CapMeter active={r.activeLeads} cap={r.cap} /></section>
      <Num label="Overdue" n={r.overdue} tone="danger" /><Num label="Stale" n={r.stale} tone="warning" />
      <section className="panel flex flex-col gap-0.5 p-4"><span className="text-[13px] font-medium text-ink-2">This month</span><span className="text-metric"><span className="text-success">{r.wonMonth}</span> <span className="text-base text-ink-3">won</span> · <span className={r.lostMonth ? 'text-danger' : ''}>{r.lostMonth}</span> <span className="text-base text-ink-3">lost</span></span></section>
    </div>

    <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_360px]">
      <Lane title="Their move" tone="brand" items={today.mine} onTask={(dealId) => setTask({ dealId })} />
      <Lane title="Waiting on the customer" tone="accent" items={today.theirs} onTask={(dealId) => setTask({ dealId })} />
      <aside className="flex flex-col gap-4">
        <section className="panel flex flex-col gap-2.5 p-4"><h2 className="m-0 text-subheading">Pipeline by stage</h2>
          {r.byStage.map((s) => <Link key={s.key} to={`/app/stages/nb/${s.key}?owner=${r.id}`} className="grid grid-cols-[96px_minmax(0,1fr)_24px] items-center gap-2 text-[13px] text-ink no-underline hover:text-brand-ink"><span className="truncate">{s.label}</span><span className="h-2 rounded bg-surface-2"><span className="block h-2 rounded bg-brand" style={{ width: `${(s.count / max) * 100}%` }} /></span><span className="text-right font-semibold">{s.count}</span></Link>)}
        </section>
        <section className="panel flex flex-col gap-2 p-4"><h2 className="m-0 text-subheading">Recent activity</h2>
          {recent.length === 0 ? <p className="m-0 text-small text-ink-3">No activity logged yet.</p> : <ul className="m-0 flex list-none flex-col gap-2.5 p-0">{recent.map((a) => <li key={a.id} className="flex flex-col text-[13px]"><span><b>{ACTIVITY_LABEL[a.kind]}</b> · <Link to={`/app/deals/${a.dealId}`} className="text-ink no-underline hover:text-brand-ink">{a.company}</Link></span><span className="line-clamp-2 text-ink-2">{a.discussed}</span><span className="text-xs text-ink-3">{formatRelativeIST(a.at)}</span></li>)}</ul>}
        </section>
        <section className="panel flex flex-col gap-2 p-4"><h2 className="m-0 text-subheading">Lost deals and reasons</h2>
          {lost.length === 0 ? <p className="m-0 text-small text-ink-3">None.</p> : <ul className="m-0 flex list-none flex-col gap-1.5 p-0">{lost.map((l) => <li key={l.company} className="flex items-center justify-between gap-2 text-[13px]"><span className="truncate">{l.company}</span><Badge tone="danger">{l.reason}</Badge></li>)}</ul>}
        </section>
      </aside>
    </div>
    {task && <AssignTaskDialog repName={r.name.split(' ')[0]} deals={deals} initialDealId={task.dealId} onClose={() => setTask(null)} />}
  </div>;
}

function Num({ label, n, tone }: { label: string; n: number; tone: 'danger' | 'warning' }) {
  return <section className={clsx('panel flex flex-col gap-0.5 p-4', n && tone === 'danger' && '!border-danger', n && tone === 'warning' && '!border-warning')}><span className="text-[13px] font-medium text-ink-2">{label}</span><span className={clsx('text-metric', n ? (tone === 'danger' ? 'text-danger' : 'text-warning') : '')}>{n}</span></section>;
}

function Lane({ title, tone, items, onTask }: { title: string; tone: 'brand' | 'accent'; items: Lead[]; onTask: (dealId: string) => void }) {
  const [all, setAll] = useState(false); const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(new Date());
  const urgent = items.filter((d) => d.nextStepDue <= today); const later = items.filter((d) => d.nextStepDue > today);
  const shown = [...urgent, ...(all ? later : later.slice(0, 3))];
  return <section className="flex min-w-0 flex-col gap-3" aria-label={title}>
    <h2 className={clsx('m-0 flex items-center justify-between border-b-2 pb-2 text-overline text-ink-2', tone === 'brand' ? 'border-brand' : 'border-accent')}>{title}<span className="text-ink-3">{items.length}</span></h2>
    {items.length === 0 && <p className="m-0 rounded-lg border border-dashed border-line p-5 text-center text-small text-ink-3">Nothing here.</p>}
    {shown.map((d) => { const due = formatDueIST(d.nextStepDue); return <article key={d.id} className={clsx('panel flex flex-col gap-1.5 p-3.5', due.overdue && 'border-l-[3px] !border-l-danger')}>
      <div className="flex items-start justify-between gap-2"><Link to={`/app/deals/${d.id}`} className="text-body-strong text-ink no-underline hover:text-brand-ink">{d.company}</Link>{tone === 'accent' && nudgeDue(d) ? <Badge tone="accent" icon="clock">Nudge due</Badge> : <Badge>{stageLabel(d)}</Badge>}</div>
      <p className="m-0 text-[14px]">{d.nextStep}</p>
      <div className="flex items-center justify-between gap-2"><span className={clsx('text-small', due.overdue ? 'font-semibold text-danger' : 'text-ink-2')}>{due.text}</span><Button size="sm" variant="quiet" onClick={() => onTask(d.id)}>Assign task</Button></div>
    </article>; })}
    {later.length > 3 && <Button variant="quiet" onClick={() => setAll(!all)}>{all ? 'Show fewer' : `Show ${later.length - 3} more coming up`}</Button>}
  </section>;
}
