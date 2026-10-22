import clsx from 'clsx';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError, type Lead, type RepSummary, type TeamOverview } from '@/api/types';
import { Avatar, Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Banner, useToast } from '@/components/ui/Feedback';
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { CapMeter, Dialog } from '@/components/ui/Overlay';
import { formatINR, formatRelativeIST, todayIST } from '@/lib/format';

export const pct = (a: number, b: number) => (b ? Math.round((a / b) * 100) : 0);

/** Revenue against target as a thin gradient bar with the percentage. */
export function TargetBar({ value, target, compact }: { value: number; target: number; compact?: boolean }) {
  const p = target ? Math.min(1, value / target) : 0;
  return <div className="flex flex-col gap-1">
    <div className="flex items-baseline justify-between gap-2"><span className={compact ? 'text-body-strong' : 'text-metric'}>{formatINR(value, { compact: true })}</span><span className="text-xs text-ink-3">of {formatINR(target, { compact: true })} · <b className="text-ink">{pct(value, target)}%</b></span></div>
    <span className={clsx('relative block overflow-hidden rounded-pill bg-surface-2', compact ? 'h-1.5' : 'h-2.5')} role="meter" aria-valuenow={pct(value, target)} aria-valuemin={0} aria-valuemax={100} aria-label="Revenue against target"><span className="absolute inset-y-0 left-0 rounded-pill" style={{ width: `${p * 100}%`, background: 'var(--gradient-cinematic)' }} /></span>
  </div>;
}

export function TeamStrip({ o }: { o: TeamOverview }) {
  return <div className="grid gap-4 md:grid-cols-[minmax(0,2fr)_repeat(4,minmax(0,1fr))]">
    <section className="panel flex flex-col gap-1.5 p-4"><span className="text-[13px] font-medium text-ink-2">Team revenue this month</span><TargetBar value={o.revenueMonth} target={o.target} /><span className="text-xs text-ink-3">Sum of each rep’s target</span></section>
    <Tile label="Open deals" value={o.openDeals} hint={`${formatINR(o.openValue, { compact: true })} pipeline`} />
    <Tile label="Weighted forecast" value={formatINR(o.weighted, { compact: true })} hint="Value × stage probability" />
    <Tile label="Overdue follow-ups" value={o.overdue} tone={o.overdue ? 'danger' : undefined} hint="Next steps past due" />
    <Tile label="Stale deals" value={o.stale} tone={o.stale ? 'warning' : undefined} hint="7+ days quiet" />
  </div>;
}
function Tile({ label, value, hint, tone }: { label: string; value: React.ReactNode; hint: string; tone?: 'danger' | 'warning' }) {
  return <div className={clsx('panel flex flex-col gap-0.5 p-4', tone === 'danger' && '!border-danger', tone === 'warning' && '!border-warning')}><span className="text-[13px] font-medium text-ink-2">{label}</span><span className={clsx('text-metric', tone === 'danger' && 'text-danger', tone === 'warning' && 'text-warning')}>{value}</span><span className="text-xs text-ink-3">{hint}</span></div>;
}

/** One card per rep (PRD 7): load vs cap, follow-ups, outcomes, revenue vs target. Numbers open the deals behind them. */
export function RepCard({ r }: { r: RepSummary }) {
  const stat = (n: number, label: string, to: string, tone?: 'danger' | 'warning') => <Link to={to} className={clsx('flex flex-col rounded-md px-2 py-1.5 no-underline hover:bg-surface-3', n && tone === 'danger' ? 'text-danger' : n && tone === 'warning' ? 'text-warning' : 'text-ink')}><span className="text-heading">{n}</span><span className="text-[11px] font-medium text-ink-3">{label}</span></Link>;
  const board = `/app/board?owner=${r.id}`;
  return <article className="panel flex flex-col gap-3 p-4">
    <header className="flex items-center gap-3"><Avatar name={r.name} size={36} /><span className="flex min-w-0 flex-1 flex-col"><Link to={`/app/team/${r.id}`} className="truncate text-body-strong text-ink no-underline hover:text-brand-ink">{r.name}</Link><span className="truncate text-xs text-ink-3">{r.lastActivityAt ? `Last activity ${formatRelativeIST(r.lastActivityAt)}` : 'No activity yet'}</span></span>{!r.active && <Badge>Inactive</Badge>}</header>
    <TargetBar value={r.revenueMonth} target={r.target} compact />
    <div className="flex flex-col gap-1"><span className="text-[11px] font-medium text-ink-3">Active leads vs cap</span><CapMeter active={r.activeLeads} cap={r.cap} /></div>
    <div className="-mx-2 grid grid-cols-4">{stat(r.overdue, 'Overdue', `${board}&health=at_risk`, 'danger')}{stat(r.dueToday, 'Due today', `/app/team/${r.id}`)}{stat(r.stale, 'Stale', `${board}&health=stale`, 'warning')}{stat(r.meetingsWeek, 'Meetings 7d', `/app/team/${r.id}`)}</div>
    <div className="flex items-center justify-between gap-2 border-t border-line pt-3 text-small"><span className="text-ink-2"><b className="text-success">{r.wonMonth}</b> won · <b className={r.lostMonth ? 'text-danger' : 'text-ink'}>{r.lostMonth}</b> lost this month</span><Link to={`/app/team/${r.id}`} className="text-[13px] font-semibold text-brand-ink no-underline hover:underline">Open Today →</Link></div>
  </article>;
}

/** PRD Feature 6: a Team Leader assigns a task on a rep's deal; it lands on the rep's Today. */
export function AssignTaskDialog({ repName, deals, initialDealId, onClose }: { repName: string; deals: Lead[]; initialDealId?: string; onClose: () => void }) {
  const qc = useQueryClient(); const toast = useToast();
  const [dealId, setDealId] = useState(initialDealId ?? ''); const [text, setText] = useState(''); const [due, setDue] = useState(''); const [err, setErr] = useState<string | null>(null);
  const m = useMutation({ mutationFn: () => api.assignTask(dealId, { text, due }), onSuccess: () => { ['repProfile', 'teamOverview', 'deals', 'deal'].forEach((k) => qc.invalidateQueries({ queryKey: [k] })); toast({ message: `Task sent to ${repName}` }); onClose(); }, onError: (e) => setErr(e instanceof ApiError ? e.message : 'Could not assign the task.') });
  return <Dialog title={`Assign a task to ${repName}`} description="It replaces the deal’s next step and appears at the top of their Today. They get a notification." onClose={onClose}
    footer={<><Button variant="quiet" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!dealId || !text.trim() || !due} loading={m.isPending} onClick={() => m.mutate()}>Assign task</Button></>}>
    <SelectField label="Deal" required value={dealId} onChange={(e) => setDealId(e.target.value)} options={[{ value: '', label: 'Choose a deal…' }, ...deals.map((d) => ({ value: d.id, label: `${d.company} · now: ${d.nextStep}` }))]} />
    <TextField label="Task" required value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Call Salman and confirm the second outlet" />
    <TextField label="Due" required type="date" min={todayIST()} value={due} onChange={(e) => setDue(e.target.value)} />
    {err && <Banner tone="danger" title={err} />}
  </Dialog>;
}

export function ReasonDialog({ title, description, action, danger, onClose, onConfirm, busy, optional }: { title: string; description?: string; action: string; danger?: boolean; busy?: boolean; optional?: boolean; onClose: () => void; onConfirm: (reason: string) => void }) {
  const [reason, setReason] = useState('');
  return <Dialog title={title} description={description} onClose={onClose} footer={<><Button variant="quiet" onClick={onClose}>Cancel</Button><Button variant={danger ? 'danger' : 'primary'} disabled={!optional && !reason.trim()} loading={busy} onClick={() => onConfirm(reason)}>{action}</Button></>}>
    <TextAreaField label="Reason" required={!optional} optionalNote={optional ? '(optional)' : undefined} rows={3} value={reason} onChange={(e) => setReason(e.target.value)} hint="Saved in history. The person it affects can see it." />
  </Dialog>;
}
