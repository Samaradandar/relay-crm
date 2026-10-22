import clsx from 'clsx';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import type { Lead, StageVisit } from '@/api/types';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { formatDateTimeIST } from '@/lib/format';

const EXIT_WORD: Record<string, string> = { forward: 'moved on', backward: 'moved back', override: 'moved on by an override', skip: 'skipped ahead', lost: 'marked lost' };

/** An earlier (or the current) stage of this lead, read-only: the checklist it had, what was ticked and the proof behind it. */
export function StageHistory({ d, stageKey, onClose }: { d: Lead; stageKey: string; onClose: () => void }) {
  const history = useQuery({ queryKey: ['stageHistory', d.id], queryFn: () => api.stageHistory(d.id) });
  const keys = stageKey === 'revision' ? ['revision', 'revision_2'] : [stageKey];
  const visits = (history.data ?? []).filter((v) => keys.includes(v.stageKey));
  return <section className="panel flex flex-col gap-4 p-5" aria-labelledby="hist-h">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 id="hist-h" className="m-0 text-heading">{visits[0]?.stageName ?? 'Stage'} <span className="text-small font-normal text-ink-3">· read only</span></h2>
      <Button size="sm" variant="quiet" onClick={onClose}>Close</Button>
    </div>
    {history.isLoading && <div className="h-24 animate-pulse rounded-md bg-surface-2" />}
    {!history.isLoading && visits.length === 0 && <p className="m-0 text-small text-ink-2">This lead has not been in this stage.</p>}
    {visits.map((v, i) => <Visit key={v.entryId} v={v} n={i + 1} of={visits.length} />)}
  </section>;
}

function Visit({ v, n, of }: { v: StageVisit; n: number; of: number }) {
  const done = v.criteria.filter((c) => c.ticked).length;
  return <div className="flex flex-col gap-3 rounded-lg border border-line bg-surface-0 p-4">
    <div className="flex flex-wrap items-center gap-2 text-small">
      {of > 1 && <Badge>Visit {n} of {of}</Badge>}
      {v.round > 1 && <Badge>Round {v.round}</Badge>}
      <span className="text-ink-2">In {formatDateTimeIST(v.enteredAt)}{v.enteredBy ? ` by ${v.enteredBy}` : ''}</span>
      <span className="text-ink-3">·</span>
      <span className="text-ink-2">{v.current ? 'This is the current stage' : `Out ${v.exitedAt ? formatDateTimeIST(v.exitedAt) : ''}${v.exitKind ? ` (${EXIT_WORD[v.exitKind] ?? v.exitKind})` : ''}`}</span>
      <span className="ml-auto text-ink-2">{done}/{v.criteria.length} exit criteria done</span>
    </div>
    {v.reason && <p className="m-0 text-small text-ink-2">Reason: {v.reason}</p>}
    <ul className="m-0 flex list-none flex-col p-0">
      {v.criteria.map((c) => <li key={c.key} className="flex flex-col gap-1 border-t border-line py-2.5 first:border-t-0">
        <div className="flex items-start gap-2.5">
          <span aria-hidden className={clsx('mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-sm border-[1.5px]', c.ticked ? 'border-brand bg-brand text-brand-on' : 'border-line-control')}>{c.ticked && <Icon name="check" size={14} />}</span>
          <div className="flex min-w-0 flex-1 flex-col">
            <span className="text-[14px] font-medium">{c.label}</span>
            <span className="text-small text-ink-2">{c.ticked ? `Ticked${c.tickedBy ? ` by ${c.tickedBy}` : ''}${c.tickedAt ? `, ${formatDateTimeIST(c.tickedAt)}` : ''}` : v.current ? 'Not done yet' : 'Not ticked (moved on without it)'}</span>
          </div>
          {c.status === 'verified' && <Badge tone="success" icon="check">Verified</Badge>}
          {c.status === 'overridden' && <Badge>Overridden</Badge>}
          {c.proofMissing && <Badge tone="danger" icon="alert">Proof missing</Badge>}
        </div>
        {c.ticked && c.details.length > 0 && <dl className="m-0 ml-[30px] grid grid-cols-[minmax(0,180px)_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-small">
          {c.details.map((x) => <div key={x.label} className="contents"><dt className="text-ink-3">{x.label}</dt><dd className="m-0">{x.value}</dd></div>)}
        </dl>}
        {c.ticked && c.activity && <p className="m-0 ml-[30px] text-small text-ink-2">
          {c.activity.type.charAt(0).toUpperCase() + c.activity.type.slice(1)} on {formatDateTimeIST(c.activity.at)}{c.activity.status ? ` (${c.activity.status})` : ''}
          {c.activity.attendees.length > 0 ? `, with ${c.activity.attendees.join(', ')}` : ''}{c.activity.discussed ? `. ${c.activity.discussed}` : ''}
        </p>}
      </li>)}
      {v.criteria.length === 0 && <li className="text-small text-ink-3">This stage had no exit criteria.</li>}
    </ul>
  </div>;
}
