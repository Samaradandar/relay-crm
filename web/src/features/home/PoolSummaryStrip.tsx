import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import { Button } from '@/components/ui/Button';
import { Icon } from '@/components/ui/Icon';
import { formatWaiting } from '@/lib/format';

/** Admin and Super admin home stays the Approvals inbox (Phase 1); from Phase 2 this strip sits on top of it. */
export function PoolSummaryStrip() {
  const nav = useNavigate();
  const { data: s } = useQuery({ queryKey: ['poolSummary'], queryFn: api.poolSummary });
  if (!s) return <div className="panel mb-5 h-[124px] animate-pulse" aria-hidden />;
  const assigned = s.assignedToday.rule + s.assignedToday.round_robin + s.assignedToday.manual;
  return <section aria-label="Lead Pool today" className="panel mb-6 grid gap-0 overflow-hidden sm:grid-cols-2 xl:grid-cols-[1.2fr_1.2fr_1fr_1fr_auto]">
    <Cell label="In the Lead Pool" value={s.total}>
      <span className="flex flex-wrap gap-1.5">{s.byTeam.filter((t) => t.count).map((t) => <span key={t.teamId} className="inline-flex h-[22px] items-center gap-1.5 rounded-pill bg-surface-2 pl-1 pr-2 text-caption"><span className="h-3 w-3 rounded-full" style={{ background: t.color }} />{t.teamName} <b>{t.count}</b></span>)}</span>
    </Cell>
    <Cell label="Stuck, need a person" value={s.waiting} tone={s.waiting ? 'warning' : undefined}>
      <span className="text-xs text-ink-3">{s.byReason.all_reps_at_cap} all reps at cap · {s.byReason.no_active_reps} no active rep{s.oldestWaitingAt ? ` · oldest ${formatWaiting(s.oldestWaitingAt)}` : ''}</span>
    </Cell>
    <Cell label="Assigned today" value={assigned}><span className="text-xs text-ink-3">{s.assignedToday.rule} rule · {s.assignedToday.round_robin} round-robin · {s.assignedToday.manual} by hand</span></Cell>
    <Cell label="Reps at cap" value={<>{s.repsAtCap}<span className="text-base font-medium text-ink-3"> of {s.reps}</span></>}><span className="text-xs text-ink-3">{s.duplicatesFlagged ? `${s.duplicatesFlagged} duplicate warning${s.duplicatesFlagged === 1 ? '' : 's'} overridden` : 'No duplicate overrides to review'}</span></Cell>
    <div className="flex items-center border-t border-line p-4 sm:col-span-2 xl:col-span-1 xl:border-l xl:border-t-0"><Button icon="pool" onClick={() => nav('/app/pool')}>Open Lead Pool</Button></div>
  </section>;
}

function Cell({ label, value, tone, children }: { label: string; value: React.ReactNode; tone?: 'warning'; children?: React.ReactNode }) {
  return <div className="flex flex-col gap-1 border-t border-line p-4 first:border-t-0 sm:[&:nth-child(2)]:border-t-0 xl:border-l xl:border-t-0 xl:first:border-l-0">
    <span className="flex items-center gap-1.5 text-[13px] font-medium text-ink-2">{tone === 'warning' && <Icon name="alert" size={14} className="text-warning" />}{label}</span>
    <span className={`text-metric ${tone === 'warning' ? 'text-warning' : ''}`}>{value}</span>{children}
  </div>;
}
