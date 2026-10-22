import clsx from 'clsx';
import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { useAuth } from '@/auth/AuthProvider';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Banner } from '@/components/ui/Feedback';
import { Icon } from '@/components/ui/Icon';
import { useReasonPrompt } from '@/components/ui/ReasonPrompt';
import { formatDateTimeIST } from '@/lib/format';
import { PIPELINES, type PipelineDef, type PipelineKey, type StageDef } from '@/lib/pipeline';
import type { PipelineSettingsLive } from '@/api/types';

const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));
const kindLabel = { auto: 'Automatic', activity: 'Logged activity', fields: 'Fields', file: 'File' } as const;

/**
 * PRD 9 Pipeline configuration. Changes go into a draft; publishing validates them and creates a new
 * version. A change applies only to deals that enter a stage after it is published; rollback republishes.
 */
export function PipelineSettingsPage() {
  const [p, setP] = useState<PipelineKey>('new_business');
  const live = useQuery({ queryKey: ['pipelineSettings', p], queryFn: () => api.pipelineSettings(p) });
  if (live.isLoading) return <div className="panel h-64 animate-pulse" />;
  if (live.data) return <LivePipelineSettings key={`${p}:${live.data.live.version_no}`} p={p} setP={setP} data={live.data} />;
  return <MockPipelineSettings />;
}

type LiveDef = PipelineSettingsLive['live'];

/** Live mode: edits the published version the backend actually runs (names, probabilities, exit
 * criteria labels; removing a criterion). Publishing saves a draft and publishes it as a new version. */
function LivePipelineSettings({ p, setP, data }: { p: PipelineKey; setP: (k: PipelineKey) => void; data: PipelineSettingsLive }) {
  const { me } = useAuth(); const qc = useQueryClient();
  const cur = data.live;
  const [draft, setDraft] = useState<LiveDef>(() => clone(cur));
  const changes = useMemo(() => {
    const out: string[] = [];
    draft.stages.forEach((s) => { const c = cur.stages.find((x) => x.key === s.key); if (!c) return;
      if (c.name !== s.name) out.push(`${c.name} renamed “${s.name}”`);
      if (c.probability_pct !== s.probability_pct) out.push(`${s.name}: ${c.probability_pct}% → ${s.probability_pct}%`); });
    const sig = (list: LiveDef['criteria']) => JSON.stringify(list.map((c) => `${c.key}/${c.label}`));
    cur.stages.forEach((s) => { if (sig(cur.criteria.filter((c) => c.stage_key === s.key)) !== sig(draft.criteria.filter((c) => c.stage_key === s.key))) out.push(`${s.name}: exit criteria changed`); });
    return out;
  }, [draft, cur]);
  const { ask, element } = useReasonPrompt(() => ['pipelineSettings', 'deals', 'deal', 'stagePage'].forEach((k) => qc.invalidateQueries({ queryKey: [k] })));
  const editStage = (key: string, patch: Partial<LiveDef['stages'][number]>) => setDraft((d) => ({ ...d, stages: d.stages.map((s) => (s.key === key ? { ...s, ...patch } : s)) }));
  const editCrit = (stage: string, key: string, label: string | null) => setDraft((d) => ({ ...d, criteria: label === null ? d.criteria.filter((c) => !(c.stage_key === stage && c.key === key)) : d.criteria.map((c) => (c.stage_key === stage && c.key === key ? { ...c, label } : c)) }));
  const published = data.versions.filter((v) => v.status === 'published').sort((a, b) => b.version_no - a.version_no);

  return <div className="flex flex-col gap-5">
    <PageHeader title="Pipeline settings" subtitle={`Stages, exit criteria and probabilities. Live version: v${cur.version_no}. Changes apply only to deals that enter a stage after you publish.`}
      actions={<><Button variant="quiet" disabled={!changes.length} onClick={() => setDraft(clone(cur))}>Discard draft</Button><Button variant="primary" icon="send" disabled={!changes.length}
        onClick={() => ask({ title: `Publish ${PIPELINES[p].label} as a new version?`, description: <span>{changes.length} change{changes.length > 1 ? 's' : ''}: {changes.join('; ')}.</span>, action: 'Validate and publish', done: 'New pipeline version published', run: (x) => api.publishPipelineLive(p, draft, x) })}>Publish{changes.length ? ` ${changes.length} change${changes.length > 1 ? 's' : ''}` : ''}</Button></>} />
    {changes.length > 0 && <Banner tone="notice" title="Unpublished draft">{changes.join(' · ')}</Banner>}
    {me?.role === 'admin' && <p className="m-0 text-small text-ink-3">As an Admin you add a reason when you publish. Stage changes that move deals (removing a stage with deals in it) are for the Super admin.</p>}
    <div role="tablist" aria-label="Pipeline" className="inline-flex w-fit rounded-md border border-line-control bg-surface-1 p-0.5">
      {(['new_business', 'existing_customer'] as PipelineKey[]).map((k) => <button key={k} role="tab" aria-selected={p === k} onClick={() => setP(k)} className={clsx('h-8 rounded px-3 text-sm font-semibold', p === k ? 'bg-brand text-brand-on' : 'text-ink-2 hover:text-ink')}>{PIPELINES[k].label}</button>)}
    </div>
    <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
      <ol className="m-0 flex list-none flex-col gap-3 p-0">{draft.stages.map((s, i) => <li key={s.key} className="panel flex flex-col gap-3 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <span className="grid h-7 w-7 place-items-center rounded-full bg-brand-soft text-[13px] font-semibold text-brand-ink">{i + 1}</span>
          <input aria-label="Stage name" value={s.name} onChange={(e) => editStage(s.key, { name: e.target.value })} className="h-9 min-w-0 flex-1 rounded-md border border-line-control bg-surface-1 px-2.5 text-body-strong" />
          <label className="flex items-center gap-1.5 text-small text-ink-2">Probability<input type="number" min={0} max={100} aria-label={`${s.name} probability`} value={s.probability_pct} onChange={(e) => editStage(s.key, { probability_pct: Number(e.target.value) })} className="h-9 w-20 rounded-md border border-line-control bg-surface-1 px-2 text-sm" />%</label>
          {s.won_on_entry && <Badge tone="success" icon="trophy">Won on entry</Badge>}{s.is_conditional && <Badge>Conditional</Badge>}
        </div>
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0">{draft.criteria.filter((c) => c.stage_key === s.key).map((c) => <li key={c.key} className="flex items-center gap-2">
          <Icon name="check" size={14} className="text-ink-3" />
          <input aria-label="Criterion" value={c.label} onChange={(e) => editCrit(s.key, c.key, e.target.value)} className="h-8 min-w-0 flex-1 rounded-md border border-line bg-surface-1 px-2 text-[13px]" />
          <Badge>{PROOF_LABEL[c.proof_type] ?? c.proof_type}</Badge>
          <Button size="sm" variant="quiet" aria-label={`Remove ${c.label}`} onClick={() => editCrit(s.key, c.key, null)}>✕</Button>
        </li>)}</ul>
      </li>)}</ol>
      <aside className="panel flex flex-col gap-3 p-4 xl:sticky xl:top-[72px]"><h2 className="m-0 text-heading">Versions</h2><p className="m-0 text-xs text-ink-3">Every version is kept. Rollback publishes an earlier version again as a new version.</p>
        <ol className="m-0 flex list-none flex-col gap-3 p-0">{published.map((v, i) => <li key={v.version_no} className="flex flex-col gap-1 border-t border-line pt-3 first:border-t-0 first:pt-0">
          <span className="flex items-center justify-between gap-2"><span className="text-body-strong">v{v.version_no}{i === 0 && <span className="ml-2"><Badge tone="success">Live</Badge></span>}</span>{i > 0 && <Button size="sm" variant="quiet" icon="undo" onClick={() => ask({ title: `Roll back to v${v.version_no}?`, description: `This publishes v${v.version_no} again as a new version.`, action: 'Roll back', done: `Rolled back to v${v.version_no}`, run: (x) => api.rollbackPipelineLive(p, v.version_no, x) })}>Roll back</Button>}</span>
          {v.notes && <span className="text-small text-ink-2">{v.notes}</span>}<span className="text-xs text-ink-3">{v.published_by_name ?? 'System'}{v.published_at ? ` · ${formatDateTimeIST(v.published_at)}` : ''}</span>
        </li>)}</ol>
      </aside>
    </div>
    {element}
  </div>;
}

const PROOF_LABEL: Record<string, string> = { fields: 'Fields', activity: 'Logged activity', checklist: 'Checklist', contact_tag: 'Contact tag', deal_lines: 'Product lines', document: 'Document', closure_change: 'Plan change', proposal_round: 'Customer answer', proposal_sent: 'Proposal emailed' };

/** Mock mode (no backend): the original draft editor over the static pipeline definition. */
function MockPipelineSettings() {
  const { me } = useAuth(); const qc = useQueryClient();
  const versions = useQuery({ queryKey: ['pipelineVersions'], queryFn: api.pipelineVersions });
  const [p, setP] = useState<PipelineKey>('new_business');
  const [draft, setDraft] = useState<Record<PipelineKey, PipelineDef>>(() => clone({ new_business: PIPELINES.new_business, existing_customer: PIPELINES.existing_customer }));
  const live = { new_business: PIPELINES.new_business, existing_customer: PIPELINES.existing_customer };
  const changes = useMemo(() => {
    const out: string[] = [];
    (Object.keys(draft) as PipelineKey[]).forEach((k) => draft[k].stages.forEach((s) => { const cur = live[k].stages.find((x) => x.key === s.key); if (!cur) return;
      if (cur.label !== s.label) out.push(`${cur.label} renamed “${s.label}”`); if (cur.probability !== s.probability) out.push(`${s.label}: ${cur.probability}% → ${s.probability}%`);
      if (JSON.stringify(cur.criteria.map((c) => c.label)) !== JSON.stringify(s.criteria.map((c) => c.label))) out.push(`${s.label}: exit criteria changed`); }));
    return out;
  }, [draft, versions.data]); // eslint-disable-line react-hooks/exhaustive-deps
  const { ask, element } = useReasonPrompt(() => { ['pipelineVersions', 'deals', 'deal'].forEach((k) => qc.invalidateQueries({ queryKey: [k] })); });
  const latest = versions.data?.[versions.data.length - 1];
  const edit = (i: number, patch: Partial<StageDef>) => setDraft((d) => { const n = clone(d); Object.assign(n[p].stages[i], patch); return n; });
  const reset = () => setDraft(clone(live));

  return <div className="flex flex-col gap-5">
    <PageHeader title="Pipeline settings" subtitle={`Stages, exit criteria and probabilities. Live version: v${latest?.version ?? 1}. Changes apply only to deals that enter a stage after you publish.`}
      actions={<><Button variant="quiet" disabled={!changes.length} onClick={reset}>Discard draft</Button><Button variant="primary" icon="send" disabled={!changes.length}
        onClick={() => ask({ title: `Publish pipeline v${(latest?.version ?? 1) + 1}?`, description: <span>{changes.length} change{changes.length > 1 ? 's' : ''}: {changes.join('; ')}.</span>, action: 'Validate and publish', done: 'New pipeline version published', run: async (x) => { const r = await api.publishPipeline(draft, x); setDraft(clone({ new_business: PIPELINES.new_business, existing_customer: PIPELINES.existing_customer })); return r; } })}>Publish{changes.length ? ` ${changes.length} change${changes.length > 1 ? 's' : ''}` : ''}</Button></>} />
    {changes.length > 0 && <Banner tone="notice" title="Unpublished draft">{changes.join(' · ')}</Banner>}
    {me?.role === 'admin' && <p className="m-0 text-small text-ink-3">As an Admin you add a reason when you publish. Stage changes that move deals (removing a stage with deals in it) are for the Super admin.</p>}

    <div role="tablist" aria-label="Pipeline" className="inline-flex w-fit rounded-md border border-line-control bg-surface-1 p-0.5">
      {(['new_business', 'existing_customer'] as PipelineKey[]).map((k) => <button key={k} role="tab" aria-selected={p === k} onClick={() => setP(k)} className={clsx('h-8 rounded px-3 text-sm font-semibold', p === k ? 'bg-brand text-brand-on' : 'text-ink-2 hover:text-ink')}>{PIPELINES[k].label}</button>)}
    </div>

    <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
      <ol className="m-0 flex list-none flex-col gap-3 p-0">{draft[p].stages.map((s, i) => <li key={s.key} className="panel flex flex-col gap-3 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <span className="grid h-7 w-7 place-items-center rounded-full bg-brand-soft text-[13px] font-semibold text-brand-ink">{i + 1}</span>
          <input aria-label="Stage name" value={s.label} onChange={(e) => edit(i, { label: e.target.value })} className="h-9 min-w-0 flex-1 rounded-md border border-line-control bg-surface-1 px-2.5 text-body-strong" />
          <label className="flex items-center gap-1.5 text-small text-ink-2">Probability<input type="number" min={0} max={100} aria-label={`${s.label} probability`} value={s.probability} onChange={(e) => edit(i, { probability: Number(e.target.value) })} className="h-9 w-20 rounded-md border border-line-control bg-surface-1 px-2 text-sm" />%</label>
          {s.won && <Badge tone="success" icon="trophy">Won on entry</Badge>}{s.mergedInto && <Badge>Shown inside Revision</Badge>}
        </div>
        <p className="m-0 text-small text-ink-3">{s.meaning}</p>
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0">{s.criteria.map((c, ci) => <li key={c.key + ci} className="flex items-center gap-2">
          <Icon name="check" size={14} className="text-ink-3" />
          <input aria-label="Criterion" value={c.label} onChange={(e) => { const crit = clone(s.criteria); crit[ci].label = e.target.value; edit(i, { criteria: crit }); }} className="h-8 min-w-0 flex-1 rounded-md border border-line bg-surface-1 px-2 text-[13px]" />
          <Badge>{kindLabel[c.proof.kind]}</Badge>
          <Button size="sm" variant="quiet" aria-label={`Remove ${c.label}`} onClick={() => edit(i, { criteria: s.criteria.filter((_, x) => x !== ci) })}>✕</Button>
        </li>)}</ul>
        <Button size="sm" variant="quiet" icon="plus" className="self-start" onClick={() => edit(i, { criteria: [...s.criteria, { key: 'custom_' + Date.now(), label: 'New criterion', proof: { kind: 'fields', fields: [{ key: 'custom_' + Date.now(), label: 'Details', type: 'text' }], explain: 'Write the proof.' } }] })}>Add exit criterion</Button>
      </li>)}</ol>
      <aside className="panel flex flex-col gap-3 p-4 xl:sticky xl:top-[72px]"><h2 className="m-0 text-heading">Versions</h2><p className="m-0 text-xs text-ink-3">Every version is kept. Rollback publishes an earlier version again as a new version.</p>
        <ol className="m-0 flex list-none flex-col gap-3 p-0">{[...(versions.data ?? [])].reverse().map((v, i) => <li key={v.version} className="flex flex-col gap-1 border-t border-line pt-3 first:border-t-0 first:pt-0">
          <span className="flex items-center justify-between gap-2"><span className="text-body-strong">v{v.version}{i === 0 && <span className="ml-2"><Badge tone="success">Live</Badge></span>}</span>{i > 0 && <Button size="sm" variant="quiet" icon="undo" onClick={() => ask({ title: `Roll back to v${v.version}?`, description: 'This publishes v' + v.version + ' again as a new version.', action: 'Roll back', done: `Rolled back to v${v.version}`, run: async (x) => { const r = await api.rollbackPipeline(v.version, x); reset(); setDraft(clone({ new_business: PIPELINES.new_business, existing_customer: PIPELINES.existing_customer })); return r; } })}>Roll back</Button>}</span>
          <span className="text-small text-ink-2">{v.reason}</span><span className="text-xs text-ink-3">{v.by} · {formatDateTimeIST(v.publishedAt)}</span>
        </li>)}</ol>
      </aside>
    </div>
    {element}
  </div>;
}
