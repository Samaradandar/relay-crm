import clsx from 'clsx';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import type { AddedLead, Lead } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { Badge, TeamChip } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { CapMeter } from '@/components/ui/Overlay';
import { Icon } from '@/components/ui/Icon';
import { formatDueIST, formatRelativeIST, formatWaiting } from '@/lib/format';
import { howAssigned } from '@/lib/leads';
import { useNow } from '@/features/pool/PoolTable';
import { Hero, greeting } from './Hero';

/**
 * Sales rep home, Phase 2. Two jobs: put leads into the Pool, and pick up the leads assigned to you.
 * From Phase 3 the "My open leads" list hands over to the Today list (tasks and stages).
 */
export function RepHome() {
  const { me } = useAuth(); const nav = useNavigate(); const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ['myLeads'], queryFn: api.myLeads });
  const seen = useMutation({ mutationFn: api.markLeadsSeen, onSuccess: () => qc.invalidateQueries({ queryKey: ['myLeads'] }) });
  if (!me) return null;
  const owned = data?.owned ?? []; const added = data?.added ?? [];
  const fresh = owned.filter((l) => !l.seenByOwner);
  const book = owned.filter((l) => l.seenByOwner).sort((a, b) => a.nextStepDue.localeCompare(b.nextStepDue));
  const overdue = owned.filter((l) => formatDueIST(l.nextStepDue).overdue).length;
  const month = new Date().toISOString().slice(0, 7);
  const addedMonth = added.filter((a) => a.createdAt.slice(0, 7) === month);
  const cap = me.leadCap ?? 0;

  const line = [fresh.length ? `${fresh.length} new lead${fresh.length > 1 ? 's' : ''} for you` : 'No new leads right now', overdue ? `${overdue} next step${overdue > 1 ? 's' : ''} overdue` : null].filter(Boolean).join(' · ');

  return <div className="flex flex-col gap-6">
    <Hero title={`${greeting()}, ${me.name.split(' ')[0]}`} meta={<>{me.team && <TeamChip name={me.team.name} color={me.team.color} />}<span>{line}</span></>}
      actions={<Button variant="primary" icon="plus" onClick={() => nav('/app/leads/new')}>Add new lead</Button>} />

    <div className="grid gap-4 sm:grid-cols-3">
      <Tile label="My open leads" value={<>{owned.length}<span className="text-base font-medium text-ink-3"> / {cap}</span></>}><CapMeter active={owned.length} cap={cap} /><span className="text-xs text-ink-3">New leads stop coming by round-robin at your cap.</span></Tile>
      <Tile label="New for you" value={<span className="inline-flex items-center gap-2">{fresh.length}{fresh.length > 0 && <span className="h-2 w-2 rounded-full bg-accent" aria-hidden />}</span>}><span className="text-xs text-ink-3">Assigned to you and not looked at yet</span></Tile>
      <Tile label="Leads I added this month" value={addedMonth.length}><span className="text-xs text-ink-3">{addedMonth.filter((a) => a.state === 'yours').length} came back to you · {addedMonth.filter((a) => a.state === 'assigned').length} to teammates · {addedMonth.filter((a) => a.state === 'waiting').length} waiting</span></Tile>
    </div>

    <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_360px]">
      <div className="flex min-w-0 flex-col gap-6">
        <section aria-labelledby="new-h" className="flex flex-col gap-3">
          <div className="flex items-end justify-between gap-3">
            <h2 id="new-h" className="m-0 flex items-center gap-2 text-heading">New for you {fresh.length > 0 && <span className="min-w-5 rounded-pill bg-accent px-1.5 text-center text-[11px] font-semibold leading-[18px] text-accent-on">{fresh.length}</span>}</h2>
            {fresh.length > 1 && <Button size="sm" variant="quiet" icon="check" onClick={() => seen.mutate(fresh.map((l) => l.id))}>Mark all as seen</Button>}
          </div>
          {fresh.length === 0
            ? <div className="panel flex items-center gap-3 p-5 text-ink-2"><span className="grid h-8 w-8 place-items-center rounded-full bg-success-soft text-success"><Icon name="check" /></span>You have seen every lead assigned to you. New ones appear here the moment they are assigned.</div>
            : <div className="grid gap-3 lg:grid-cols-2">{fresh.map((l) => <NewLeadCard key={l.id} l={l} onSeen={() => seen.mutate([l.id])} />)}</div>}
        </section>

        <section aria-labelledby="book-h" className="flex flex-col gap-3">
          <h2 id="book-h" className="m-0 text-heading">My open leads <span className="font-normal text-ink-3">· by next step due</span></h2>
          <div className="overflow-x-auto rounded-lg border border-line bg-surface-1">
            <table className="w-full border-collapse text-sm">
              <thead><tr>{['Company', 'Next step', 'Due', 'How it came to you'].map((h) => <th key={h} className="whitespace-nowrap border-b border-line bg-surface-2 px-3 py-2.5 text-left text-xs font-semibold text-ink-2">{h}</th>)}</tr></thead>
              <tbody>
                {book.length === 0 && <tr><td colSpan={4} className="px-3 py-8 text-center text-ink-2">No other open leads yet.</td></tr>}
                {book.map((l) => { const d = formatDueIST(l.nextStepDue); return <tr key={l.id} className="hover:bg-surface-3">
                  <td className="min-w-[200px] border-b border-line px-3 py-2.5"><span className="flex flex-col"><span className="text-body-strong">{l.company}</span><span className="text-xs text-ink-3">{l.contactName}{l.contactRole ? `, ${l.contactRole}` : ''} · {l.source}</span></span></td>
                  <td className="max-w-[260px] truncate border-b border-line px-3 text-ink-2" title={l.nextStep}>{l.nextStep}</td>
                  <td className={clsx('whitespace-nowrap border-b border-line px-3', d.overdue ? 'font-semibold text-danger' : d.today ? 'font-semibold text-ink' : 'text-ink-2')}>{d.overdue && <Icon name="alert" size={14} className="mr-1 inline align-[-2px]" />}{d.text}</td>
                  <td className="border-b border-line px-3 text-xs text-ink-3"><span className="block max-w-[200px] truncate" title={howAssigned(l)}>{howAssigned(l)}</span><span>{formatRelativeIST(l.assignedAt!)}</span></td>
                </tr>; })}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <aside className="flex flex-col gap-4">
        <section className="panel flex flex-col gap-3 p-5" aria-labelledby="added-h">
          <div className="flex items-center justify-between gap-2"><h2 id="added-h" className="m-0 text-heading">Leads I added</h2><Link to="/app/leads/new" className="text-[13px] font-semibold text-brand-ink no-underline hover:underline">+ Add</Link></div>
          <p className="m-0 text-xs text-ink-3">Where each lead went after you saved it. You stay recorded as the person who generated it.</p>
          <AddedList items={added.slice(0, 8)} />
        </section>
        <section className="flex items-start gap-3 rounded-lg border border-line bg-brand-soft p-4 text-small text-ink-2">
          <Icon name="info" size={20} className="shrink-0 text-brand-ink" />
          <span>Leads you add go into the <b className="text-ink">Lead Pool</b>, not straight to you. Relay assigns them by rule or round-robin, so the same lead never gets worked twice.</span>
        </section>
      </aside>
    </div>
  </div>;
}

function Tile({ label, value, children }: { label: string; value: React.ReactNode; children?: React.ReactNode }) {
  return <div className="panel flex flex-col gap-1.5 p-4"><span className="text-[13px] font-medium text-ink-2">{label}</span><span className="text-metric">{value}</span>{children}</div>;
}

function NewLeadCard({ l, onSeen }: { l: Lead; onSeen: () => void }) {
  const d = formatDueIST(l.nextStepDue);
  return <article className="panel relative flex flex-col gap-3 overflow-hidden p-4 pl-5 shadow-card">
    <span className="absolute inset-y-0 left-0 w-1 bg-accent" aria-hidden />
    <div className="flex items-start justify-between gap-3">
      <div className="flex min-w-0 flex-col"><span className="text-body-strong">{l.company}</span><span className="text-small text-ink-2">{l.contactName}{l.contactRole ? `, ${l.contactRole}` : ''}{l.city ? ` · ${l.city}` : ''}</span></div>
      <Badge tone="accent" dot>New</Badge>
    </div>
    <div className={clsx('flex items-center gap-2 rounded-md px-2.5 py-2 text-small', d.overdue ? 'bg-danger-soft text-danger' : 'bg-surface-2 text-ink')}>
      <Icon name={d.overdue ? 'alert' : 'clock'} size={14} /><span className="min-w-0 flex-1 truncate">{l.nextStep}</span><span className="whitespace-nowrap font-semibold">{d.text}</span>
    </div>
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-3">
      <span>{l.source}</span><span>·</span><span>{howAssigned(l)}</span><span>·</span><span>{formatRelativeIST(l.assignedAt!)}</span>
      {l.generatedById !== l.ownerId && <><span>·</span><span>added by {l.generatedByName}</span></>}
    </div>
    <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
      {l.phone && <a href={`tel:${l.phone.replace(/\s/g, '')}`} className="inline-flex h-7 items-center gap-1.5 rounded-md border border-line-control px-2.5 text-[13px] font-semibold text-ink no-underline hover:bg-surface-3"><Icon name="phone" size={14} />{l.phone}</a>}
      {l.email && <a href={`mailto:${l.email}`} className="inline-flex h-7 items-center gap-1.5 rounded-md border border-line-control px-2.5 text-[13px] font-semibold text-ink no-underline hover:bg-surface-3"><Icon name="mail" size={14} />Email</a>}
      <Button size="sm" variant="quiet" icon="check" className="ml-auto" onClick={onSeen}>Got it</Button>
    </div>
  </article>;
}

function AddedList({ items }: { items: AddedLead[] }) {
  const now = useNow();
  if (items.length === 0) return <p className="m-0 text-small text-ink-2">You have not added any leads yet.</p>;
  return <ul className="m-0 flex list-none flex-col p-0">{items.map((a) => <li key={a.id} className="flex flex-col gap-1 border-t border-line py-2.5 first:border-t-0">
    <span className="flex items-center justify-between gap-2"><span className="truncate text-[13px] font-semibold">{a.company}</span>
      {a.state === 'waiting' ? <Badge tone="accent" icon="clock">In Pool · {formatWaiting(a.since, now)}</Badge> : a.state === 'yours' ? <Badge tone="success" icon="check">Yours</Badge> : <Badge tone="brand" icon="user">Assigned</Badge>}
    </span>
    <span className="text-xs text-ink-3">{a.state === 'waiting' ? `Waiting for ${a.teamName ?? 'a team'} to assign` : a.state === 'yours' ? `Came back to you · ${a.method === 'rule' ? 'rule' : a.method === 'manual' ? 'manual' : 'round-robin'}` : `To ${a.ownerName}${a.teamName ? `, ${a.teamName}` : ''}`} · added {formatRelativeIST(a.createdAt)}</span>
  </li>)}</ul>;
}
