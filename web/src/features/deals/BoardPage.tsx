import clsx from 'clsx';
import { useMemo, useState, type DragEvent } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError, LEAD_SOURCES, type Lead } from '@/api/types';
import { PageHeader } from '@/components/layout/PageHeader';
import { Avatar, Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Banner, useToast } from '@/components/ui/Feedback';
import { SelectField } from '@/components/ui/Field';
import { Icon } from '@/components/ui/Icon';
import { Dialog } from '@/components/ui/Overlay';
import { TextAreaField } from '@/components/ui/Field';
import { useAuth } from '@/auth/AuthProvider';
import { OwnerFilter, useOwnerFilter } from './OwnerFilter';
import { formatINR } from '@/lib/format';
import { PIPELINES, nextStage, stageOf, stagePath, type PipelineKey } from '@/lib/pipeline';
import { DueLine, HealthBadge, RoundBadge, healthOf, inStageText, stageLabel, valueText } from './bits';
import { useLogActivity } from './LogActivityDrawer';
import { ContactTypeBadge } from './ContactType';

/** Feature 4: Kanban per pipeline. Drag follows the move rules; the API is the judge. */
export function BoardPage() {
  const { me } = useAuth(); const nav = useNavigate(); const qc = useQueryClient(); const toast = useToast(); const log = useLogActivity();
  const [q, setQ] = useSearchParams(); const p: PipelineKey = q.get('p') === 'ec' ? 'existing_customer' : 'new_business';
  const { data = [], isLoading } = useQuery({ queryKey: ['deals'], queryFn: api.deals });
  const [search, setSearch] = useState(''); const [source, setSource] = useState(''); const [hf, setHf] = useState(q.get('health') ?? ''); const owner = useOwnerFilter();
  const [drag, setDrag] = useState<string | null>(null); const [over, setOver] = useState<string | null>(null);
  const [blocked, setBlocked] = useState<{ deal: Lead; message: string } | null>(null);
  const [back, setBack] = useState<{ deal: Lead; to: string } | null>(null);

  const open = useMemo(() => data.filter((d) => d.pipeline === p && d.status === 'open' && owner.match(d) && (!search || d.company.toLowerCase().includes(search.toLowerCase()) || d.contactName.toLowerCase().includes(search.toLowerCase())) && (!source || d.source === source) && (!hf || healthOf(d) === hf)), [data, p, search, source, hf, owner]);
  // The won-on-entry stage (Onboarding / Updated) is on the board too: those deals are Won but
  // still being set up there, so the column shows them until they leave it.
  const cols = PIPELINES[p].stages.filter((s) => !s.mergedInto && (!s.hiddenOnBoard || s.won));
  const wonHere = useMemo(() => data.filter((d) => d.pipeline === p && d.status === 'won' && stageOf(p, d.stage!)?.won && owner.match(d) && (!search || d.company.toLowerCase().includes(search.toLowerCase()) || d.contactName.toLowerCase().includes(search.toLowerCase())) && (!source || d.source === source)), [data, p, search, source, owner]);
  const inCol = (key: string) => stageOf(p, key)?.won ? wonHere.filter((d) => d.stage === key) : open.filter((d) => (d.stage === 'revision_2' ? 'revision' : d.stage) === key);
  const all = data.filter((d) => d.pipeline === p && d.status === 'open' && owner.match(d));
  const weighted = all.reduce((s, d) => s + (d.value ?? 0) * (stageOf(p, d.stage!)!.probability / 100), 0);

  const move = useMutation({
    mutationFn: (v: { d: Lead; to?: string; reason?: string }) => api.moveDeal(v.d.id, { to: v.to, reason: v.reason }),
    onSuccess: (d) => { qc.invalidateQueries({ queryKey: ['deals'] }); qc.invalidateQueries({ queryKey: ['today'] }); toast({ message: d.status === 'won' ? `${d.company} is Won` : `${d.company} moved to ${stageOf(d.pipeline!, d.stage!)?.label}` }); setBack(null); },
    onError: (e, v) => setBlocked({ deal: v.d, message: e instanceof ApiError ? e.message : 'Could not move the deal.' })
  });

  function drop(e: DragEvent, colKey: string) {
    e.preventDefault(); setOver(null); const d = data.find((x) => x.id === drag); setDrag(null); if (!d) return;
    const cur = d.stage === 'revision_2' ? 'revision' : d.stage!; if (cur === colKey) return;
    const stages = PIPELINES[p].stages; const ci = stages.findIndex((s) => s.key === cur), ti = stages.findIndex((s) => s.key === colKey);
    if (ti < ci) { setBack({ deal: d, to: colKey }); return; }
    if (nextStage(p, d.stage!)?.key !== colKey) { setBlocked({ deal: d, message: 'Deals move forward one stage at a time. To skip a stage, ask your Team Leader for an override.' }); return; }
    move.mutate({ d, to: colKey });
  }

  return <div className="flex min-w-0 flex-col gap-4">
    <PageHeader title="Pipeline board" subtitle={me?.role === 'user' ? 'Your open deals. Drag a card forward when its exit criteria are ticked.' : 'Open deals in your scope.'}
      actions={<><Button icon="file" onClick={() => log.open()}>Log activity</Button><Button variant="primary" icon="plus" onClick={() => nav('/app/leads/new')}>Add new lead</Button></>} />
    <div role="tablist" aria-label="Pipeline" className="inline-flex w-fit rounded-md border border-line-control bg-surface-1 p-0.5">
      {(['new_business', 'existing_customer'] as PipelineKey[]).map((k) => <button key={k} role="tab" aria-selected={p === k} onClick={() => { const nq = new URLSearchParams(q); if (k === 'existing_customer') nq.set('p', 'ec'); else nq.delete('p'); setQ(nq); }}
        className={clsx('h-8 rounded px-3 text-sm font-semibold', p === k ? 'bg-brand text-brand-on' : 'text-ink-2 hover:text-ink')}>{PIPELINES[k].label} <span className="opacity-75">{data.filter((d) => d.pipeline === k && d.status === 'open').length}</span></button>)}
    </div>
    <div className="grid gap-3 sm:grid-cols-4">
      <Stat label="Open deals" value={all.length} />
      <Stat label="Open pipeline value" value={formatINR(all.reduce((s, d) => s + (d.value ?? 0), 0), { compact: true })} hint="Annual contract value" />
      <Stat label="Weighted forecast" value={formatINR(weighted, { compact: true })} hint="Value × stage probability" />
      <Stat label="Stalled" value={all.filter((d) => healthOf(d) === 'stale').length} hint="7+ days with no activity" warn={all.some((d) => healthOf(d) === 'stale')} />
    </div>
    <div className="flex flex-wrap items-end gap-3">
      <label className="flex h-9 w-64 items-center gap-2 rounded-md border border-line-control bg-surface-1 px-2.5 text-ink-3"><Icon name="search" /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search company or contact" aria-label="Search deals" className="min-w-0 flex-1 border-0 bg-transparent text-sm text-ink outline-none" /></label>
      <OwnerFilter f={owner} />
      <SelectField label="Source" value={source} onChange={(e) => setSource(e.target.value)} options={[{ value: '', label: 'All sources' }, ...LEAD_SOURCES.map((s) => ({ value: s, label: s }))]} className="w-40" />
      <SelectField label="Health" value={hf} onChange={(e) => setHf(e.target.value)} options={[{ value: '', label: 'Any' }, { value: 'on_track', label: 'On track' }, { value: 'at_risk', label: 'At risk' }, { value: 'stale', label: 'Stale' }]} className="w-36" />
    </div>
    <div className="-mx-4 overflow-x-auto px-4 pb-2 sm:-mx-8 sm:px-8">
      <div className="flex w-max gap-5">
        {cols.map((c) => { const list = inCol(c.key); const total = list.reduce((s, d) => s + (d.value ?? 0), 0);
          return <section key={c.key} aria-label={c.label} onDragOver={(e) => { e.preventDefault(); setOver(c.key); }} onDragLeave={() => setOver((o) => (o === c.key ? null : o))} onDrop={(e) => drop(e, c.key)}
            className={clsx('flex w-[280px] flex-col gap-2 rounded-lg bg-surface-2 p-2 transition-shadow', over === c.key && 'shadow-[inset_0_0_0_2px_var(--brand)]')}>
            <header className="flex items-baseline justify-between gap-2 px-1 pt-1">
              <Link to={stagePath(p, c.key)} className="text-subheading text-ink no-underline hover:text-brand-ink">{c.label} <span className="ml-1 rounded-pill bg-surface-1 px-1.5 text-[11px] font-semibold text-ink-2">{list.length}</span></Link>
              <span className="text-xs text-ink-3">{total ? formatINR(total, { compact: true }) + ' · ' : ''}{c.probability}%</span>
            </header>
            {isLoading && <div className="h-24 animate-pulse rounded-md bg-surface-1" />}
            {list.map((d) => <Card key={d.id} d={d} dragging={drag === d.id} onDragStart={() => setDrag(d.id)} onDragEnd={() => setDrag(null)} draggable={d.ownerId === me?.id && d.status === 'open'} />)}
            {!isLoading && list.length === 0 && <p className="m-0 px-2 py-6 text-center text-xs text-ink-3">No deals here</p>}
          </section>; })}
      </div>
    </div>
    <p className="m-0 text-xs text-ink-3">Won deals stay in {p === 'new_business' ? 'Onboarding' : 'Updated'} while they are set up, and are listed under Won. Lost deals go to Lost.</p>

    {blocked && <Dialog title={`Can’t move ${blocked.deal.company} yet`} onClose={() => setBlocked(null)} footer={<><Button variant="quiet" onClick={() => setBlocked(null)}>Close</Button><Button variant="primary" onClick={() => nav(`/app/deals/${blocked.deal.id}`)}>Open the deal</Button></>}>
      <Banner tone="warning" title={blocked.message.startsWith('Tick these first') ? 'Some exit criteria are not ticked' : 'Move not allowed'}>{blocked.message.replace('Tick these first: ', '')}</Banner>
    </Dialog>}
    {back && <BackDialog d={back.deal} to={back.to} busy={move.isPending} onClose={() => setBack(null)} onConfirm={(reason) => move.mutate({ d: back.deal, to: back.to, reason })} />}
  </div>;
}

export function BackDialog({ d, to, busy, onClose, onConfirm }: { d: Lead; to: string; busy?: boolean; onClose: () => void; onConfirm: (reason: string) => void }) {
  const [reason, setReason] = useState('');
  return <Dialog title={`Move ${d.company} back to ${stageOf(d.pipeline!, to)?.label}?`} description="Moving backward is allowed with a reason. It is saved in the deal’s history." onClose={onClose}
    footer={<><Button variant="quiet" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!reason.trim()} loading={busy} onClick={() => onConfirm(reason)}>Move back</Button></>}>
    <TextAreaField label="Reason" required rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Decision maker changed; need to re-qualify" />
  </Dialog>;
}

function Card({ d, dragging, draggable, onDragStart, onDragEnd }: { d: Lead; dragging: boolean; draggable: boolean; onDragStart: () => void; onDragEnd: () => void }) {
  const nav = useNavigate(); const miss = Object.values(d.criteria ?? {}).some((c) => c.state === 'proof_missing');
  return <article draggable={draggable} onDragStart={(e) => { e.dataTransfer.effectAllowed = 'move'; onDragStart(); }} onDragEnd={onDragEnd}
    onClick={() => nav(`/app/deals/${d.id}`)} onKeyDown={(e) => { if (e.key === 'Enter') nav(`/app/deals/${d.id}`); }} tabIndex={0} aria-label={`${d.company}, ${valueText(d)}`}
    className={clsx('flex cursor-pointer flex-col gap-2 rounded-md border border-line bg-surface-1 p-3 shadow-card hover:border-line-control', draggable && 'active:cursor-grabbing', dragging && 'rotate-1 opacity-60 shadow-drag')}>
    <div className="flex items-start justify-between gap-2"><span className="text-body-strong">{d.company}</span>{d.value ? <span className="whitespace-nowrap text-body-strong">{valueText(d)}</span> : null}</div>
    <span className="flex flex-wrap items-center gap-1.5 text-small text-ink-2">{d.contactName}{d.contactRole ? `, ${d.contactRole}` : ''}<ContactTypeBadge decisionMaker={d.contactDecisionMaker} /></span>
    {miss && <span className="flex items-center gap-1.5 rounded-sm bg-danger-soft px-1.5 py-1 text-caption text-danger"><Icon name="alert" size={14} />Proof missing</span>}
    <DueLine d={d} />
    <div className="flex items-center justify-between gap-2"><span className="flex flex-wrap items-center gap-1.5">{d.status === 'won' ? <Badge tone="success" icon="trophy">Won</Badge> : <HealthBadge d={d} />}<RoundBadge round={d.round} /><span className="text-xs text-ink-3">{inStageText(d)} in {stageLabel(d)}</span></span>{d.ownerName && <Avatar name={d.ownerName} size={22} />}</div>
  </article>;
}

function Stat({ label, value, hint, warn }: { label: string; value: React.ReactNode; hint?: string; warn?: boolean }) {
  return <div className={clsx('panel flex flex-col gap-0.5 px-4 py-3', warn && '!border-warning')}><span className="text-[13px] font-medium text-ink-2">{label}</span><span className="text-metric">{value}</span>{hint && <span className="text-xs text-ink-3">{hint}</span>}</div>;
}
