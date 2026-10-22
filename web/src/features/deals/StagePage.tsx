import clsx from 'clsx';
import { useMemo, useState } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import { LEAD_SOURCES, type Lead, type StagePageData } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { OwnerFilter, useOwnerFilter } from './OwnerFilter';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { ReasonBox, useToast } from '@/components/ui/Feedback';
import { SelectField } from '@/components/ui/Field';
import { Icon } from '@/components/ui/Icon';
import { Dialog } from '@/components/ui/Overlay';
import { formatDueIST, formatINR, formatRelativeIST } from '@/lib/format';
import { howAssigned } from '@/lib/leads';
import { PIPELINES, stageOf, weightOf, type PipelineKey, type StageColumn } from '@/lib/pipeline';
import { ContactTypeBadge } from './ContactType';
import { MEETING_STATUS_LABEL, type MeetingStatus } from '@/lib/meetings';
import { DueLine, HealthBadge, RoundBadge, daysIn, durationText, healthOf, inStageMs, inStageText, valueText } from './bits';

type Sort = 'due' | 'value' | 'days' | 'company';

/** Feature 5: one page per stage (no sub-views): stage panel, filters, sorting, pagination 10/25/50, scoped export. */
export function StagePage() {
  const { p: slug, key = '' } = useParams(); const p: PipelineKey = slug === 'ec' ? 'existing_customer' : 'new_business';
  const st = stageOf(p, key);
  const { me } = useAuth(); const toast = useToast();
  const { data = [], isLoading } = useQuery({ queryKey: ['deals'], queryFn: api.deals });
  // Live mode: the backend's own columns, exit criteria and cell values for this stage, so the
  // page always matches the published pipeline (mock mode returns null: the static config is used).
  const live = useQuery({ queryKey: ['stagePage', p, key], queryFn: () => api.stagePage(p, key), enabled: !!stageOf(p, key) && !stageOf(p, key)?.mergedInto });
  const [search, setSearch] = useState(''); const [source, setSource] = useState(''); const [hf, setHf] = useState('');
  const [sort, setSort] = useState<Sort>('due'); const owner = useOwnerFilter(); const [size, setSize] = useState(10); const [page, setPage] = useState(0);
  const [exporting, setExporting] = useState(false);
  const keys = key === 'revision' ? ['revision', 'revision_2'] : [key];
  const rows = useMemo(() => {
    const r = data.filter((d) => d.pipeline === p && keys.includes(d.stage!) && owner.match(d) && (st?.won ? d.status === 'won' : d.status === 'open')
      && (!search || `${d.company} ${d.contactName}`.toLowerCase().includes(search.toLowerCase())) && (!source || d.source === source) && (!hf || healthOf(d) === hf));
    const by: Record<Sort, (a: Lead, b: Lead) => number> = { due: (a, b) => a.nextStepDue.localeCompare(b.nextStepDue), value: (a, b) => (b.value ?? 0) - (a.value ?? 0), days: (a, b) => daysIn(b) - daysIn(a), company: (a, b) => a.company.localeCompare(b.company) };
    return r.sort(by[sort]);
  }, [data, p, key, search, source, hf, sort, st, owner]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!st || st.mergedInto) return <Navigate to="/app/board" replace />;
  const pages = Math.max(1, Math.ceil(rows.length / size)); const shown = rows.slice(page * size, page * size + size);
  const total = rows.reduce((s, d) => s + (d.value ?? 0), 0);
  const avgMs = rows.length ? rows.reduce((s, d) => s + inStageMs(d), 0) / rows.length : 0;
  const columns: StageColumn[] = live.data ? live.data.columns.map((c) => ({ key: 'x:' + c.key, label: c.label })) : st.columns;
  const criteria = live.data ? live.data.criteria : st.criteria;
  const extras = live.data?.extras ?? {};
  const idx = PIPELINES[p].stages.filter((s) => !s.mergedInto).findIndex((s) => s.key === key) + 1;

  return <div className="flex min-w-0 flex-col gap-5">
    <PageHeader title={st.label} subtitle={<><span className="text-ink-3">{PIPELINES[p].label} · stage {idx} · {weightOf(p, st.key, st.probability)}%{p === 'new_business' ? ' of the journey' : ''}</span> — {st.meaning}</>}
      actions={<><Link to={`/app/board${p === 'existing_customer' ? '?p=ec' : ''}`} className="inline-flex h-9 items-center gap-2 rounded-md border border-line-control bg-surface-1 px-3.5 text-sm font-semibold text-ink no-underline hover:bg-surface-3"><Icon name="board" />Board</Link><Button icon="file" onClick={() => setExporting(true)} disabled={!rows.length}>Export CSV</Button></>} />

    <section className="panel grid gap-0 overflow-hidden lg:grid-cols-[repeat(3,minmax(0,170px))_minmax(0,1fr)]" aria-label="Stage panel">
      <Cell label={st.won ? 'Won deals here' : 'Deals in this stage'} value={rows.length} />
      <Cell label="Total value" value={total ? formatINR(total, { compact: true }) : '—'} />
      <Cell label="Average time here" value={rows.length ? durationText(avgMs) : '—'} />
      <div className="flex flex-col gap-1.5 border-t border-line p-4 lg:border-l lg:border-t-0"><span className="text-[13px] font-medium text-ink-2">Exit criteria to leave this stage</span>
        <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">{criteria.map((c) => <li key={c.key}><Badge icon="check">{c.label}</Badge></li>)}</ul></div>
    </section>

    <div className="flex flex-wrap items-end gap-3">
      <label className="flex h-9 w-64 items-center gap-2 rounded-md border border-line-control bg-surface-1 px-2.5 text-ink-3"><Icon name="search" /><input value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} placeholder="Search company or contact" aria-label="Search" className="min-w-0 flex-1 border-0 bg-transparent text-sm text-ink outline-none" /></label>
      <OwnerFilter f={owner} />
      <SelectField label="Source" value={source} onChange={(e) => { setSource(e.target.value); setPage(0); }} options={[{ value: '', label: 'All sources' }, ...LEAD_SOURCES.map((s) => ({ value: s, label: s }))]} className="w-40" />
      {!st.won && <SelectField label="Health" value={hf} onChange={(e) => { setHf(e.target.value); setPage(0); }} options={[{ value: '', label: 'Any' }, { value: 'on_track', label: 'On track' }, { value: 'at_risk', label: 'At risk' }, { value: 'stale', label: 'Stale' }]} className="w-36" />}
      <SelectField label="Sort by" value={sort} onChange={(e) => setSort(e.target.value as Sort)} options={[{ value: 'due', label: 'Next step due' }, { value: 'value', label: 'Value (high first)' }, { value: 'days', label: 'Longest in stage' }, { value: 'company', label: 'Company A–Z' }]} className="w-44" />
    </div>

    <div className="overflow-x-auto rounded-lg border border-line bg-surface-1">
      <table className="w-full border-collapse text-sm">
        <thead><tr>{['Company', 'Value', 'Next step', 'In stage', ...columns.map((c) => c.label), 'Health'].map((hd) => <th key={hd} className="whitespace-nowrap border-b border-line bg-surface-2 px-3 py-2.5 text-left text-xs font-semibold text-ink-2">{hd}</th>)}</tr></thead>
        <tbody>
          {isLoading && <tr><td colSpan={5 + columns.length} className="px-3 py-8 text-center text-ink-3">Loading…</td></tr>}
          {!isLoading && shown.length === 0 && <tr><td colSpan={5 + columns.length} className="px-3 py-10 text-center text-ink-2">{data.some((d) => keys.includes(d.stage!)) ? 'No deals match these filters.' : `No deals in ${st.label} right now.`}</td></tr>}
          {shown.map((d) => <tr key={d.id} className="hover:bg-surface-3">
            <td className="min-w-[200px] border-b border-line px-3 py-2.5"><Link to={`/app/deals/${d.id}`} className="flex flex-col no-underline"><span className="text-body-strong text-ink hover:text-brand-ink">{d.company}</span><span className="flex flex-wrap items-center gap-1.5 text-xs text-ink-3">{d.contactName}{d.city ? ` · ${d.city}` : ''}<ContactTypeBadge decisionMaker={d.contactDecisionMaker} /></span></Link></td>
            <td className="whitespace-nowrap border-b border-line px-3 text-body-strong">{valueText(d)}</td>
            <td className="max-w-[280px] border-b border-line px-3"><DueLine d={d} /></td>
            <td className={clsx('whitespace-nowrap border-b border-line px-3', daysIn(d) > 14 ? 'font-semibold text-warning' : 'text-ink-2')}>{inStageText(d)}</td>
            {columns.map((c) => <td key={c.key} className="max-w-[220px] truncate border-b border-line px-3 text-ink-2">{c.key.startsWith('x:') ? extraCell(c.key.slice(2), extras[d.id]?.[c.key.slice(2)]) : cell(d, c)}</td>)}
            <td className="whitespace-nowrap border-b border-line px-3">{st.won ? <Badge tone="success" icon="trophy">Won {d.wonAt ? formatRelativeIST(d.wonAt).split(',')[0] : ''}</Badge> : <HealthBadge d={d} />}</td>
          </tr>)}
        </tbody>
      </table>
      <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-3 py-2 text-[13px] text-ink-2">
        <span>{rows.length ? `${page * size + 1}–${Math.min(rows.length, (page + 1) * size)} of ${rows.length}` : '0 deals'}</span>
        <span className="flex items-center gap-2">Rows <select aria-label="Rows per page" value={size} onChange={(e) => { setSize(Number(e.target.value)); setPage(0); }} className="h-7 rounded-md border border-line-control bg-surface-1 px-1.5 text-[13px]">{[10, 25, 50].map((n) => <option key={n}>{n}</option>)}</select>
          <Button size="sm" variant="quiet" disabled={page === 0} onClick={() => setPage(page - 1)} aria-label="Previous page">‹</Button><span>{page + 1} / {pages}</span><Button size="sm" variant="quiet" disabled={page >= pages - 1} onClick={() => setPage(page + 1)} aria-label="Next page">›</Button></span>
      </div>
    </div>
    {me?.role === 'user' && <p className="m-0 text-xs text-ink-3">Bulk owner change, snooze and close as Lost are for Team Leaders and Admins. You can do each one from the deal page.</p>}
    {exporting && <ExportDialog stage={st.label} stageKey={key} won={!!st.won} rows={rows} columns={columns} extras={extras} onClose={() => setExporting(false)} onDone={(n) => toast({ message: `Exported ${n} deals` })} />}
  </div>;
}

/** A backend stage-page value: yes/no, money in paise, dates, people, or plain text. */
function extraValue(k: string, v: unknown): string {
  if (v === null || v === undefined || v === '') return '';
  if (k === 'meeting_status') return MEETING_STATUS_LABEL[v as MeetingStatus] ?? String(v);
  if (typeof v === 'boolean') return v ? 'Yes' : 'Not yet';
  if (typeof v === 'number') return /price|budget|amount|value/.test(k) ? formatINR(v) : v.toLocaleString('en-IN');
  if (Array.isArray(v)) return v.map((x) => extraValue(k, x)).join(', ');
  if (typeof v === 'object') { const o = v as Record<string, unknown>; return String(o.name ?? o.label ?? o.status ?? JSON.stringify(o)); }
  const s = String(v);
  if (/^\d{4}-\d{2}-\d{2}T/.test(s)) return formatRelativeIST(s);
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return formatDueIST(s).text;
  return s;
}
function extraCell(k: string, v: unknown): React.ReactNode {
  if (typeof v === 'boolean') return v ? <Badge tone="success" icon="check">Yes</Badge> : <Badge>Not yet</Badge>;
  const s = extraValue(k, v);
  return s ? s : <span className="text-ink-3">—</span>;
}

function cell(d: Lead, c: StageColumn): React.ReactNode {
  if (c.key === 'source') return d.source;
  if (c.key === 'generatedBy') return d.generatedByName;
  if (c.key === 'assignedBy') return howAssigned(d);
  if (c.key === 'round') return <span className="inline-flex items-center gap-1.5">R{d.round ?? 1}<RoundBadge round={d.round} /></span>;
  if (c.key.startsWith('c:')) { const k = c.key.slice(2); const s = d.criteria?.[k]; const ok = s && ['ticked', 'verified', 'overridden'].includes(s.state);
    if (k === 'contact_verified' || k === 'company_identified' || k === 'fit' || k === 'meeting_done' || k === 'onboarding_info' || k === 'first_invoice') {
      const auto = k === 'company_identified' && !!(d.website || d.industry);
      return ok || auto ? <Badge tone="success" icon="check">Yes</Badge> : <Badge>Not yet</Badge>; } }
  const v = d.fields?.[c.key.slice(2)];
  if (!v) return <span className="text-ink-3">—</span>;
  if (/At$/.test(c.key) && v.includes('T')) return formatRelativeIST(v);
  if (/^\d{4}-\d{2}-\d{2}$/.test(v)) return formatDueIST(v).text;
  if (c.key === 'f:budget' || c.key === 'f:finalPrice') return `${formatINR(Number(v) * 100)}/mo`;
  if (c.key === 'f:minutes') return Number(v).toLocaleString('en-IN');
  return v;
}

function ExportDialog({ stage, stageKey, won, rows, columns, extras, onClose, onDone }: { stage: string; stageKey: string; won: boolean; rows: Lead[]; columns: StageColumn[]; extras: StagePageData['extras']; onClose: () => void; onDone: (n: number) => void }) {
  const { me } = useAuth(); const [reason, setReason] = useState('');
  const policy = me?.role === 'super_admin' ? 'optional' : 'required';
  const run = useMutation({ mutationFn: () => api.logExport(`${stage} stage page`, rows.length, reason, { stage_key: stageKey, status: won ? 'won' : 'open' }), onSuccess: () => {
    const head = ['Company', 'Contact', 'Value (₹)', 'Next step', 'Due', 'Days in stage', ...columns.map((c) => c.label)];
    const esc = (x: unknown) => `"${String(x ?? '').replace(/"/g, '""')}"`;
    const body = rows.map((d) => [d.company, d.contactName, d.value ? d.value / 100 : '', d.nextStep, d.nextStepDue, daysIn(d), ...columns.map((c) => c.key.startsWith('x:') ? extraValue(c.key.slice(2), extras[d.id]?.[c.key.slice(2)]) : c.key.startsWith('f:') ? d.fields?.[c.key.slice(2)] : c.key === 'source' ? d.source : c.key === 'generatedBy' ? d.generatedByName : c.key === 'assignedBy' ? howAssigned(d) : c.key === 'round' ? `R${d.round ?? 1}` : d.criteria?.[c.key.slice(2)]?.state ?? 'open')].map(esc).join(','));
    const url = URL.createObjectURL(new Blob(['﻿' + [head.map(esc).join(','), ...body].join('\n')], { type: 'text/csv' }));
    const a = document.createElement('a'); a.href = url; a.download = `relay-${stage.toLowerCase().replace(/\s+/g, '-')}.csv`; a.click(); URL.revokeObjectURL(url);
    onDone(rows.length); onClose();
  } });
  return <Dialog title={`Export ${rows.length} deals from ${stage}`} description="Exports include only your own deals and are logged with your reason." onClose={onClose}
    footer={<><Button variant="quiet" onClick={onClose}>Cancel</Button><Button variant="primary" icon="file" disabled={policy === 'required' && !reason.trim()} loading={run.isPending} onClick={() => run.mutate()}>Download CSV</Button></>}>
    <ReasonBox id="export-reason" policy={policy} value={reason} onChange={setReason} placeholder="e.g. Weekly review with my Team Leader" />
  </Dialog>;
}

function Cell({ label, value }: { label: string; value: React.ReactNode }) {
  return <div className="flex flex-col gap-0.5 border-t border-line p-4 first:border-t-0 lg:border-l lg:border-t-0 lg:first:border-l-0"><span className="text-[13px] font-medium text-ink-2">{label}</span><span className="text-metric">{value}</span></div>;
}
