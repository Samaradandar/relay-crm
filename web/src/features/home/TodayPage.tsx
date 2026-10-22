import clsx from 'clsx';
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError, type Lead } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { Badge, TeamChip } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Banner, Tabs, useToast } from '@/components/ui/Feedback';
import { Icon } from '@/components/ui/Icon';
import { formatDueIST, formatINR, formatRelativeIST, todayIST } from '@/lib/format';
import { howAssigned } from '@/lib/leads';
import { nudgeDue, sinceActivity, stageLabel } from '@/features/deals/bits';
import { useLogActivity } from '@/features/deals/LogActivityDrawer';
import { Hero, greeting } from './Hero';

type Tab = 'all' | 'overdue' | 'today' | 'nudge';

/** Feature 6 Today view as the sales rep home: work first, numbers on top. */
export function TodayPage() {
  const { me } = useAuth(); const nav = useNavigate(); const log = useLogActivity(); const qc = useQueryClient();
  const t = useQuery({ queryKey: ['today'], queryFn: api.today });
  const n = useQuery({ queryKey: ['myNumbers'], queryFn: api.myNumbers, enabled: me?.role === 'user' });
  const added = useQuery({ queryKey: ['myLeads'], queryFn: api.myLeads });
  const seen = useMutation({ mutationFn: api.markLeadsSeen, onSuccess: () => { qc.invalidateQueries({ queryKey: ['today'] }); qc.invalidateQueries({ queryKey: ['myLeads'] }); } });
  const [tab, setTab] = useState<Tab>('all');
  if (!me) return null;
  const mine = t.data?.mine ?? []; const theirs = t.data?.theirs ?? [];
  const overdue = (d: Lead) => formatDueIST(d.nextStepDue).overdue; const today = (d: Lead) => d.nextStepDue === todayIST();
  const keep = (d: Lead) => tab === 'all' || (tab === 'overdue' ? overdue(d) : tab === 'today' ? today(d) : nudgeDue(d));
  const nOver = [...mine, ...theirs].filter(overdue).length; const need = mine.filter((d) => overdue(d) || today(d)).length + theirs.filter(nudgeDue).length;

  return <div className="flex flex-col gap-5">
    <Hero title={me.role === 'user' ? `${greeting()}, ${me.name.split(' ')[0]}` : 'My Today'} meta={<>{me.team && <TeamChip name={me.team.name} color={me.team.color} />}<span>{need ? `${need} thing${need > 1 ? 's' : ''} need you today` : 'Nothing urgent today'}{nOver ? ` · ${nOver} overdue` : ''}</span></>}
      actions={<><Button icon="file" onClick={() => log.open()}>Log activity</Button><Button variant="primary" icon="plus" onClick={() => nav('/app/leads/new')}>Add new lead</Button></>} />

    {n.data && me.role === 'user' && <Numbers n={n.data} />}

    {(t.data?.stale.length ?? 0) > 0 && <Banner tone="warning" title={`${t.data!.stale.length} deal${t.data!.stale.length > 1 ? 's have' : ' has'} gone quiet for 7+ days`}
      action={<Link to={`/app/deals/${t.data!.stale[0].id}`} className="text-[13px] font-semibold text-warning">Open {t.data!.stale[0].company}</Link>}>{t.data!.stale.map((d) => `${d.company} (${sinceActivity(d)}d)`).join(', ')}. Log an activity or close it as Lost.</Banner>}

    <Tabs<Tab> items={[{ id: 'all', label: 'All', count: mine.length + theirs.length }, { id: 'overdue', label: 'Overdue', count: nOver, accent: nOver > 0 }, { id: 'today', label: 'Due today', count: [...mine, ...theirs].filter(today).length }, { id: 'nudge', label: 'Nudge due', count: theirs.filter(nudgeDue).length }]} active={tab} onChange={setTab} />

    <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_340px]">
      <Lane title="Your move" tone="brand" items={mine.filter(keep)} empty={tab === 'all' ? 'Nothing on your side. Every deal is waiting on the customer.' : 'Nothing here.'} render={(d) => <MoveItem key={d.id} d={d} />} split={(d) => overdue(d) || today(d)} />
      <Lane title="Waiting on them" tone="accent" items={theirs.filter(keep)} empty="No one owes you a reply." render={(d) => <WaitItem key={d.id} d={d} />} />
      <aside className="flex flex-col gap-4">
        <Side title="New leads for you" dot count={t.data?.newLeads.length}>
          {(t.data?.newLeads ?? []).length === 0 ? <p className="m-0 text-small text-ink-2">No new leads. They appear here the moment they are assigned to you.</p>
            : <ul className="m-0 flex list-none flex-col p-0">{t.data!.newLeads.map((l) => <li key={l.id} className="flex items-start gap-3 border-t border-line py-2.5 first:border-t-0 first:pt-0">
              <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-accent" aria-hidden />
              <Link to={`/app/deals/${l.id}`} className="flex min-w-0 flex-1 flex-col no-underline"><span className="truncate text-[13px] font-semibold text-ink">{l.company}</span><span className="truncate text-xs text-ink-3">{l.source} · {howAssigned(l)} · {formatRelativeIST(l.assignedAt!)}</span></Link>
              <Button size="sm" variant="quiet" onClick={() => seen.mutate([l.id])}>Got it</Button></li>)}</ul>}
        </Side>
        <Side title="Meetings today" count={t.data?.meetingsToday.length}>
          {(t.data?.meetingsToday ?? []).length === 0 ? <p className="m-0 text-small text-ink-2">No meetings today.</p>
            : <ul className="m-0 flex list-none flex-col gap-3 p-0">{t.data!.meetingsToday.map((a) => { const d = [...mine, ...theirs].find((x) => x.id === a.dealId);
              return <li key={a.id} className="grid grid-cols-[56px_minmax(0,1fr)] gap-3"><span className="text-body-strong text-brand-ink">{new Intl.DateTimeFormat('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: 'Asia/Kolkata' }).format(new Date(a.meetingAt!))}</span>
                <Link to={`/app/deals/${a.dealId}`} className="flex min-w-0 flex-col no-underline"><span className="truncate text-[13px] font-semibold text-ink">{d?.company ?? 'Deal'}</span><span className="truncate text-xs text-ink-3">{a.attendees}</span></Link></li>; })}</ul>}
        </Side>
        <Side title="Leads I added">
          <ul className="m-0 flex list-none flex-col gap-2 p-0">{(added.data?.added ?? []).slice(0, 4).map((a) => <li key={a.id} className="flex items-center justify-between gap-2 text-[13px]"><span className="truncate">{a.company}</span>{a.state === 'waiting' ? <Badge tone="accent" icon="clock">In Pool</Badge> : a.state === 'yours' ? <Badge tone="success">Yours</Badge> : <Badge tone="brand">{a.ownerName?.split(' ')[0]}</Badge>}</li>)}</ul>
        </Side>
      </aside>
    </div>
  </div>;
}

function Numbers({ n }: { n: NonNullable<Awaited<ReturnType<typeof api.myNumbers>>> }) {
  const pct = n.target ? Math.min(1, n.revenueMonth / n.target) : 0; const proj = n.target ? Math.min(1, n.projected / n.target) : 0;
  const ord = (x: number) => x + (['th', 'st', 'nd', 'rd'][((x % 100) - 20) % 10] || ['th', 'st', 'nd', 'rd'][x % 100] || 'th');
  return <div className="grid gap-4 md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
    <section className="panel flex flex-col gap-2 p-4" aria-label="Revenue this month">
      <div className="flex flex-wrap items-baseline justify-between gap-2"><span className="text-[13px] font-medium text-ink-2">Revenue this month</span><span className="text-xs text-ink-3">Best month {formatINR(n.best, { compact: true })} · {n.wonMonth} won</span></div>
      <div className="flex items-baseline gap-2"><span className="text-metric">{formatINR(n.revenueMonth, { compact: true })}</span><span className="text-ink-2">of {formatINR(n.target, { compact: true })} target</span><span className="ml-auto text-body-strong">{Math.round(pct * 100)}%</span></div>
      <div className="relative h-2.5 rounded-pill bg-surface-2" role="meter" aria-valuenow={Math.round(pct * 100)} aria-valuemin={0} aria-valuemax={100} aria-label="Revenue against target">
        <span className="absolute inset-y-0 left-0 rounded-pill" style={{ width: `${pct * 100}%`, background: 'var(--gradient-cinematic)' }} />
        {proj > pct && <span className="absolute -top-1 h-[18px] w-0.5 rounded bg-brand-hover" style={{ left: `calc(${proj * 100}% - 1px)` }} title="Projected by month end" />}
      </div>
      <span className="text-xs text-ink-3">Projected by month end {formatINR(n.projected, { compact: true })} (won + open deals closing this month × stage probability)</span>
    </section>
    <section className="flex items-center gap-4 rounded-lg border border-[color-mix(in_srgb,var(--gold)_55%,var(--border))] bg-gold-soft p-4" aria-label="Your rank">
      <span className="grid h-14 w-14 shrink-0 place-items-center rounded-full bg-gold text-[22px] font-bold text-gold-on shadow-[inset_0_-3px_0_var(--amber)]">#{n.rank}</span>
      <span className="flex flex-col"><span className="text-[13px] font-medium text-gold-ink">Company rank this month</span><span className="text-heading text-ink">{ord(n.rank)} of {n.reps} reps</span><span className="text-xs text-gold-ink">{n.behindNext ? `${formatINR(n.behindNext, { compact: true })} behind #${n.rank - 1}` : 'You are top of the board'}</span></span>
    </section>
  </div>;
}

/** A lane. With `split`, urgent items show first and the rest sit under "Coming up", collapsed after three. */
function Lane({ title, tone, items, empty, render, split }: { title: string; tone: 'brand' | 'accent'; items: Lead[]; empty: string; render: (d: Lead) => React.ReactNode; split?: (d: Lead) => boolean }) {
  const [all, setAll] = useState(false);
  const urgent = split ? items.filter(split) : items; const later = split ? items.filter((d) => !split(d)) : [];
  return <section className="flex min-w-0 flex-col gap-3" aria-label={title}>
    <h2 className={clsx('m-0 flex items-center justify-between border-b-2 pb-2 text-overline text-ink-2', tone === 'brand' ? 'border-brand' : 'border-accent')}>{title}<span className="text-ink-3">{items.length}</span></h2>
    {items.length === 0 ? <p className="m-0 rounded-lg border border-dashed border-line p-5 text-center text-small text-ink-3">{empty}</p> : urgent.map(render)}
    {later.length > 0 && <>
      <h3 className="m-0 mt-1 flex items-center justify-between text-overline text-ink-3">Coming up<span>{later.length}</span></h3>
      {(all ? later : later.slice(0, 3)).map(render)}
      {later.length > 3 && <Button variant="quiet" onClick={() => setAll(!all)}>{all ? 'Show fewer' : `Show ${later.length - 3} more`}</Button>}
    </>}
  </section>;
}

function useStepActions(d: Lead) {
  const qc = useQueryClient(); const toast = useToast();
  const done = () => ['today', 'deals', 'deal'].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const quick = useMutation({ mutationFn: (k: 'snooze' | 'nudge' | 'replied') => api[k](d.id), onSuccess: (_x, k) => { done(); toast({ message: k === 'snooze' ? `${d.company}: snoozed 2 days` : k === 'nudge' ? `Nudge logged for ${d.company}` : `${d.company} is back on your move` }); } });
  return { quick, done };
}

function MoveItem({ d }: { d: Lead }) {
  const { quick, done } = useStepActions(d); const toast = useToast();
  const [closing, setClosing] = useState(false); const [text, setText] = useState(''); const [due, setDue] = useState(''); const [lane, setLane] = useState<'mine' | 'theirs'>('mine'); const [err, setErr] = useState<string | null>(null);
  const save = useMutation({ mutationFn: () => api.setNextStep(d.id, { text, due, lane }), onSuccess: () => { done(); setClosing(false); toast({ message: `Done. Next step saved for ${d.company}` }); }, onError: (e) => setErr(e instanceof ApiError ? e.message : 'Could not save.') });
  const due0 = formatDueIST(d.nextStepDue);
  return <article className={clsx('panel flex flex-col gap-2.5 p-4', due0.overdue && 'border-l-[3px] !border-l-danger')}>
    <div className="flex items-start justify-between gap-2"><Link to={`/app/deals/${d.id}`} className="min-w-0 text-body-strong text-ink no-underline hover:text-brand-ink">{d.company}</Link><Badge>{stageLabel(d)}</Badge></div>
    <p className="m-0 text-[14px]">{d.nextStep}</p>
    <span className={clsx('flex items-center gap-1.5 text-small', due0.overdue ? 'font-semibold text-danger' : due0.today ? 'font-semibold text-ink' : 'text-ink-2')}><Icon name={due0.overdue ? 'alert' : 'clock'} size={14} />{due0.text}{d.snoozeCount ? <span className="font-normal text-ink-3">· snoozed {d.snoozeCount}×</span> : null}</span>
    {!closing ? <div className="flex flex-wrap items-center gap-2 border-t border-line pt-2.5">
      <Button size="sm" icon="check" onClick={() => { setClosing(true); setErr(null); }}>Done</Button>
      <Button size="sm" onClick={() => quick.mutate('snooze')} disabled={quick.isPending}>+2d</Button>
      {d.phone && <a href={`tel:${d.phone.replace(/\s/g, '')}`} className="ml-auto inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-[13px] font-semibold text-ink-2 no-underline hover:bg-surface-3"><Icon name="phone" size={14} />Call</a>}
    </div> : <form className="flex flex-col gap-2 rounded-md border border-line-control bg-surface-0 p-3" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <label className="text-[13px] font-semibold" htmlFor={'ns' + d.id}>What’s the next step?</label>
      <input id={'ns' + d.id} autoFocus value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Send revised pricing" className="h-9 rounded-md border border-line-control bg-surface-1 px-2.5 text-sm" />
      <div className="flex flex-wrap gap-2"><input type="date" aria-label="Due date" min={todayIST()} value={due} onChange={(e) => setDue(e.target.value)} className="h-9 rounded-md border border-line-control bg-surface-1 px-2.5 text-sm" />
        <select aria-label="Whose move" value={lane} onChange={(e) => setLane(e.target.value as 'mine' | 'theirs')} className="h-9 rounded-md border border-line-control bg-surface-1 px-2 text-sm"><option value="mine">My move</option><option value="theirs">Waiting on them</option></select></div>
      {err && <p className="m-0 text-caption text-danger">{err}</p>}
      <div className="flex justify-end gap-2"><Button size="sm" variant="quiet" onClick={() => setClosing(false)}>Cancel</Button><Button size="sm" type="submit" variant="primary" disabled={!text.trim() || !due} loading={save.isPending}>Save and close task</Button></div>
    </form>}
  </article>;
}

function WaitItem({ d }: { d: Lead }) {
  const { quick } = useStepActions(d); const due = nudgeDue(d);
  return <article className="panel flex flex-col gap-2.5 p-4">
    <div className="flex items-start justify-between gap-2"><Link to={`/app/deals/${d.id}`} className="min-w-0 text-body-strong text-ink no-underline hover:text-brand-ink">{d.company}</Link>{due ? <Badge tone="accent" icon="clock">Nudge due</Badge> : <Badge>{stageLabel(d)}</Badge>}</div>
    <p className="m-0 text-[14px]">{d.nextStep}</p>
    <span className="text-small text-ink-2">Waiting since {d.waitingSince ? formatRelativeIST(d.waitingSince).split(',')[0] : '—'} · {d.contactName}</span>
    <div className="flex flex-wrap gap-2 border-t border-line pt-2.5"><Button size="sm" icon="send" onClick={() => quick.mutate('nudge')} disabled={quick.isPending}>Nudge</Button><Button size="sm" onClick={() => quick.mutate('replied')} disabled={quick.isPending}>They replied</Button></div>
  </article>;
}

function Side({ title, count, dot, children }: { title: string; count?: number; dot?: boolean; children: React.ReactNode }) {
  return <section className="panel flex flex-col gap-3 p-4"><h2 className="m-0 flex items-center gap-2 text-subheading">{dot && <span className="h-2 w-2 rounded-full bg-accent" aria-hidden />}{title}{count ? <span className="text-ink-3">{count}</span> : null}</h2>{children}</section>;
}
