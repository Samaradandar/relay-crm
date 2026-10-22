import clsx from 'clsx';
import type { Lead } from '@/api/types';
import { Badge } from '@/components/ui/Badge';
import { Icon } from '@/components/ui/Icon';
import { formatDueIST, formatINR } from '@/lib/format';
import { PIPELINES, stageOf, weightOf, type PipelineKey } from '@/lib/pipeline';

const DAY = 86_400_000;
export const daysIn = (d: Lead) => Math.max(0, Math.floor((Date.now() - Date.parse(d.stageEnteredAt ?? d.createdAt)) / DAY));
/** How long the deal has been in its current stage: "<1h", "5h", "3d". */
export const inStageMs = (d: Lead) => Math.max(0, Date.now() - Date.parse(d.stageEnteredAt ?? d.createdAt));
export const durationText = (ms: number) => ms < DAY ? (ms < 3_600_000 ? '<1h' : `${Math.floor(ms / 3_600_000)}h`) : `${Math.floor(ms / DAY)}d`;
export const inStageText = (d: Lead) => durationText(inStageMs(d));
export const sinceActivity = (d: Lead) => Math.floor((Date.now() - Date.parse(d.lastActivityAt ?? d.createdAt)) / DAY);
export const isStale = (d: Lead) => d.status === 'open' && sinceActivity(d) >= 7;
export const nudgeDue = (d: Lead) => d.lane === 'theirs' && !!d.waitingSince && Date.now() - Date.parse(d.waitingSince) >= 2 * DAY;
export function healthOf(d: Lead): 'on_track' | 'at_risk' | 'stale' { if (isStale(d)) return 'stale'; return formatDueIST(d.nextStepDue).overdue ? 'at_risk' : 'on_track'; }
export const valueText = (d: Lead) => (d.value ? formatINR(d.value, { compact: true }) : '—');
export const stageLabel = (d: Lead) => stageOf(d.pipeline ?? 'new_business', d.stage ?? 'lead')?.label ?? '—';

/** Health always carries a word (colour is never the only cue). Stale is deliberately neutral grey. */
export function HealthBadge({ d }: { d: Lead }) {
  const h = healthOf(d);
  return h === 'stale' ? <Badge icon="clock">Stale · {sinceActivity(d)}d quiet</Badge> : h === 'at_risk' ? <Badge tone="warning" icon="clock">At risk</Badge> : <Badge tone="success" dot>On track</Badge>;
}
export const RoundBadge = ({ round }: { round?: number }) => (round && round > 1 ? <span className="inline-flex h-[22px] items-center rounded-sm bg-accent px-1.5 text-caption font-semibold text-accent-on">R{round}</span> : null);

export function DueLine({ d, className }: { d: Lead; className?: string }) {
  const due = formatDueIST(d.nextStepDue);
  return <span className={clsx('flex min-w-0 items-center gap-1.5 text-[13px]', due.overdue ? 'font-medium text-danger' : 'text-ink-2', className)}>
    <Icon name={due.overdue ? 'alert' : d.lane === 'theirs' ? 'users' : 'clock'} size={14} className="shrink-0" />
    <span className="truncate">{d.nextStep}</span><span className="shrink-0 whitespace-nowrap">· {due.text}</span>
  </span>;
}

/** Thin segmented bar across the pipeline. Revision 2 lights the Revision segment. */
export function StageBar({ d, picked, onPick }: { d: Lead; picked?: string | null; onPick?: (stageKey: string | null) => void }) {
  const p: PipelineKey = d.pipeline ?? 'new_business';
  const stages = PIPELINES[p].stages.filter((s) => !s.mergedInto);
  const curKey = d.stage === 'revision_2' ? 'revision' : d.stage; const idx = stages.findIndex((s) => s.key === curKey);
  // New Business: every stage is worth a share of 100; finished stages count in full and the
  // current one by how many of its exit criteria are done
  const weighted = p === 'new_business';
  const list = d.liveCriteria ?? [];
  const share = list.length ? list.filter((c) => ['ticked', 'verified', 'overridden'].includes(d.criteria?.[c.key]?.state ?? '')).length / list.length : 0;
  const pct = !weighted || idx < 0 ? 0 : Math.round(stages.reduce((sum, s, i) => sum + (i < idx ? weightOf(p, s.key, s.probability) : i === idx ? weightOf(p, s.key, s.probability) * share : 0), 0));
  return <div className="flex flex-col gap-2">
    {weighted && <div className="flex items-center justify-between gap-3 text-small"><span className="text-ink-2">{d.status === 'lost' ? 'Lost at' : 'Journey complete'}</span><span className="text-body-strong">{pct}%</span></div>}
    <ol className="m-0 grid list-none gap-[3px] p-0" style={{ gridTemplateColumns: `repeat(${stages.length}, minmax(0,1fr))` }} aria-label="Stage progress">
    {stages.map((s, i) => { const st = d.status === 'lost' ? (i <= idx ? 'lost' : 'todo') : i < idx ? 'done' : i === idx ? 'cur' : 'todo';
      const canOpen = !!onPick && (st === 'done' || st === 'cur' || (st === 'lost' && i < idx));
      const Wrap = canOpen ? 'button' : 'div';
      return <li key={s.key} aria-current={st === 'cur' ? 'step' : undefined} className="flex min-w-0 flex-col">
        <Wrap {...(canOpen ? { type: 'button' as const, onClick: () => onPick!(picked === s.key ? null : s.key), 'aria-pressed': picked === s.key, title: `Look at ${s.label}` } : {})} className={clsx('flex min-w-0 flex-col gap-1.5 border-0 bg-transparent p-0 text-left', canOpen && 'cursor-pointer rounded hover:bg-surface-3', picked === s.key && 'bg-surface-3')}>
        <span className={clsx('h-1.5 rounded', st === 'cur' ? 'bg-brand' : st === 'done' ? 'bg-brand-soft shadow-[inset_0_0_0_1px_var(--brand)]' : st === 'lost' ? 'bg-danger-soft' : 'bg-surface-3')} />
        <span className={clsx('truncate text-caption', st === 'cur' ? 'font-semibold text-ink' : st === 'done' ? 'text-ink-2' : 'text-ink-3')}>{s.short ?? s.label}{st === 'cur' && d.round && d.round > 1 ? ` · R${d.round}` : ''}<span className="font-normal text-ink-3"> {weightOf(p, s.key, s.probability)}%</span></span>
        </Wrap>
      </li>; })}
    </ol>
  </div>;
}
