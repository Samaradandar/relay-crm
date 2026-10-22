import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import type { Lead } from '@/api/types';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { SelectField } from '@/components/ui/Field';
import { formatINR, formatRelativeIST } from '@/lib/format';
import { stageOf } from '@/lib/pipeline';
import { useLostReasons } from '@/lib/useLostReasons';
import { valueText } from './bits';
import { OwnerFilter, useOwnerFilter } from './OwnerFilter';

const days = (a?: string, b?: string) => (a && b ? Math.max(0, Math.round((Date.parse(b) - Date.parse(a)) / 86_400_000)) : 0);
const month = (iso?: string) => (iso ? iso.slice(0, 7) : '');

/** Feature 5: Won page — won date, product, value, time taken. Both pipelines. */
export function WonPage() {
  const { data = [] } = useQuery({ queryKey: ['deals'], queryFn: api.deals }); const owner = useOwnerFilter(); const [m, setM] = useState('');
  const rows = useMemo(() => data.filter((d) => d.status === 'won' && owner.match(d) && (!m || month(d.wonAt) === m)).sort((a, b) => (b.wonAt ?? '').localeCompare(a.wonAt ?? '')), [data, owner, m]);
  const months = [...new Set(data.filter((d) => d.status === 'won').map((d) => month(d.wonAt)))].sort().reverse();
  return <div className="flex flex-col gap-4">
    <PageHeader title="Won" subtitle={`${rows.length} deals · ${formatINR(rows.reduce((s, d) => s + (d.value ?? 0), 0), { compact: true })} annual contract value`} />
    <div className="flex flex-wrap items-end gap-3"><OwnerFilter f={owner} /><SelectField label="Month" value={m} onChange={(e) => setM(e.target.value)} options={[{ value: '', label: 'All time' }, ...months.map((x) => ({ value: x, label: new Date(x + '-01T00:00:00Z').toLocaleDateString('en-IN', { month: 'long', year: 'numeric', timeZone: 'UTC' }) }))]} className="w-44" /></div>
    <Table rows={rows} cols={['Company', 'Pipeline', 'Won', 'Plan', 'Value', 'Time taken', 'Owner']} cell={(d) => [<Link key="c" to={`/app/deals/${d.id}`} className="font-semibold text-ink no-underline hover:text-brand-ink">{d.company}</Link>, d.pipeline === 'existing_customer' ? 'Existing customer' : 'New business', d.wonAt ? formatRelativeIST(d.wonAt).split(',')[0] : '—', d.fields?.plan ?? '—', <b key="v">{valueText(d)}</b>, `${days(d.createdAt, d.wonAt)} days`, d.ownerName]} empty="No won deals yet." />
  </div>;
}

/** Feature 5: Lost page — reason, notes, stage lost at, lost date. Team Leaders and Admins reopen from the deal. */
export function LostPage() {
  const { data = [] } = useQuery({ queryKey: ['deals'], queryFn: api.deals }); const owner = useOwnerFilter(); const [r, setR] = useState('');
  const lost = data.filter((d) => d.status === 'lost' && owner.match(d));
  // the list an Admin maintains, plus any older reason a lost deal already carries
  const reasons = [...new Set([...useLostReasons(), ...lost.map((d) => d.lostReason).filter((x): x is string => !!x)])];
  const rows = lost.filter((d) => !r || d.lostReason === r).sort((a, b) => (b.lostAt ?? '').localeCompare(a.lostAt ?? ''));
  const top = reasons.map((x) => [x, lost.filter((d) => d.lostReason === x).length] as const).filter(([, n]) => n).sort((a, b) => b[1] - a[1]);
  return <div className="flex flex-col gap-4">
    <PageHeader title="Lost" subtitle={`${lost.length} deals. The full history of every lost deal is kept.`} />
    {top.length > 0 && <div className="flex flex-wrap gap-2">{top.map(([x, n]) => <button key={x} onClick={() => setR(r === x ? '' : x)} aria-pressed={r === x} className={'rounded-pill border px-3 py-1 text-[13px] ' + (r === x ? 'border-danger bg-danger-soft text-danger' : 'border-line-control bg-surface-1 text-ink-2')}>{x} <b>{n}</b></button>)}</div>}
    <div className="flex flex-wrap items-end gap-3"><OwnerFilter f={owner} /><SelectField label="Reason" value={r} onChange={(e) => setR(e.target.value)} options={[{ value: '', label: 'Any reason' }, ...reasons.map((x) => ({ value: x, label: x }))]} className="w-56" /></div>
    <Table rows={rows} cols={['Company', 'Lost reason', 'Notes', 'Stage lost at', 'Lost', 'Owner']} cell={(d) => [<Link key="c" to={`/app/deals/${d.id}`} className="font-semibold text-ink no-underline hover:text-brand-ink">{d.company}</Link>, <Badge key="r" tone="danger">{d.lostReason}</Badge>, <span key="n" className="block max-w-[320px] truncate text-ink-2" title={d.lostNotes}>{d.lostNotes || '—'}</span>, stageOf(d.pipeline!, d.lostFromStage ?? 'lead')?.label, d.lostAt ? formatRelativeIST(d.lostAt).split(',')[0] : '—', d.ownerName]} empty="No lost deals." />
  </div>;
}

function Table({ rows, cols, cell, empty }: { rows: Lead[]; cols: string[]; cell: (d: Lead) => React.ReactNode[]; empty: string }) {
  return <div className="overflow-x-auto rounded-lg border border-line bg-surface-1"><table className="w-full border-collapse text-sm">
    <thead><tr>{cols.map((h) => <th key={h} className="whitespace-nowrap border-b border-line bg-surface-2 px-3 py-2.5 text-left text-xs font-semibold text-ink-2">{h}</th>)}</tr></thead>
    <tbody>{rows.length === 0 && <tr><td colSpan={cols.length} className="px-3 py-10 text-center text-ink-2">{empty}</td></tr>}{rows.map((d) => <tr key={d.id} className="hover:bg-surface-3">{cell(d).map((c, i) => <td key={i} className="whitespace-nowrap border-b border-line px-3 py-2.5">{c}</td>)}</tr>)}</tbody>
  </table></div>;
}
