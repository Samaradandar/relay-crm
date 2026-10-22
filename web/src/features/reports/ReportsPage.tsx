import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import type { ReportName } from '@/api/types';
import { PageHeader } from '@/components/layout/PageHeader';
import { SelectField } from '@/components/ui/Field';
import { formatINR } from '@/lib/format';

const REPORTS: { value: ReportName; label: string }[] = [
  { value: 'rep_performance', label: 'Rep performance' },
  { value: 'won_lost', label: 'Won and lost' },
  { value: 'conversion', label: 'Conversion' },
  { value: 'leads', label: 'Leads' },
  { value: 'revenue_billing', label: 'Revenue and billing' },
  { value: 'renewals_churn', label: 'Renewals and churn' },
  { value: 'measures', label: 'Measures we track' },
];

/** PRD Feature 12: scoped reports and analytics. Renders each report's metrics as labelled
 * tiles/lists rather than a bespoke chart per report, until the dedicated chart work lands. */
export function ReportsPage() {
  const [name, setName] = useState<ReportName>('rep_performance');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');
  const q = useQuery({
    queryKey: ['report', name, dateFrom, dateTo],
    queryFn: () => api.report(name, { dateFrom: dateFrom || undefined, dateTo: dateTo || undefined }),
  });

  return <div className="flex flex-col gap-5">
    <PageHeader title="Reports" subtitle="Scoped to what you can see: your own, your team's, or everyone's." actions={<>
      <SelectField label="Report" value={name} onChange={(e) => setName(e.target.value as ReportName)} options={REPORTS} />
      <label className="flex items-center gap-1.5 text-[13px] text-ink-2">From<input type="date" className="h-9 rounded-md border border-line-control bg-surface-1 px-2 text-sm" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} /></label>
      <label className="flex items-center gap-1.5 text-[13px] text-ink-2">To<input type="date" className="h-9 rounded-md border border-line-control bg-surface-1 px-2 text-sm" value={dateTo} onChange={(e) => setDateTo(e.target.value)} /></label>
    </>} />
    {!q.data ? <div className="panel h-64 animate-pulse" /> : <ReportBody data={q.data} />}
  </div>;
}

/** A report's shape varies (counts, breakdowns, rates): scalars become tiles, lists of records
 * become tables and nested groups become labelled rows, all with money, rates and empties formatted. */
function ReportBody({ data }: { data: Record<string, unknown> }) {
  const scalars = Object.entries(data).filter(([, v]) => typeof v !== 'object' || v === null);
  const rest = Object.entries(data).filter(([, v]) => typeof v === 'object' && v !== null);
  return <div className="flex flex-col gap-4">
    {scalars.length > 0 && <div className="grid gap-4 sm:grid-cols-3 lg:grid-cols-4">
      {scalars.map(([k, v]) => <div key={k} className="panel flex flex-col gap-0.5 p-4">
        <span className="text-[13px] font-medium text-ink-2">{label(k)}</span>
        <span className="text-metric">{show(k, v)}</span>
      </div>)}
    </div>}
    {rest.map(([k, v]) => <section key={k} className="panel flex flex-col gap-2 p-4">
      <h2 className="m-0 text-subheading">{label(k)}</h2>
      <Value k={k} v={v} />
    </section>)}
  </div>;
}

const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v);

function Value({ k, v }: { k: string; v: unknown }): React.ReactNode {
  if (Array.isArray(v)) {
    if (v.length === 0) return <p className="m-0 text-small text-ink-3">Nothing in this period.</p>;
    if (!v.every(isRecord)) return <span>{v.map((x) => show(k, x)).join(', ')}</span>;
    const cols = [...new Set(v.flatMap((r) => Object.keys(r)))].filter((c) => c !== 'id' && !c.endsWith('_id'));
    return <div className="overflow-x-auto"><table className="w-full border-collapse text-sm">
      <thead><tr>{cols.map((c) => <th key={c} className="whitespace-nowrap border-b border-line bg-surface-2 px-3 py-2 text-left text-xs font-semibold text-ink-2">{label(c)}</th>)}</tr></thead>
      <tbody>{v.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c} className="whitespace-nowrap border-b border-line px-3 py-2">{isRecord(r[c]) || Array.isArray(r[c]) ? JSON.stringify(r[c]) : show(c, r[c])}</td>)}</tr>)}</tbody>
    </table></div>;
  }
  if (isRecord(v)) {
    const entries = Object.entries(v);
    if (entries.length === 0) return <p className="m-0 text-small text-ink-3">Nothing in this period.</p>;
    return <dl className="m-0 grid gap-x-6 gap-y-2 sm:grid-cols-2">{entries.map(([ck, cv]) => <div key={ck} className="flex flex-col gap-0.5">
      <dt className="text-xs font-semibold text-ink-3">{label(ck)}</dt>
      <dd className="m-0">{typeof cv === 'object' && cv !== null ? <Value k={ck} v={cv} /> : show(ck, cv)}</dd>
    </div>)}</dl>;
  }
  return <span>{show(k, v)}</span>;
}

/** Money is integer paise, rates are percentages; an empty value reads as a dash. */
function show(key: string, v: unknown): string {
  if (v === null || v === undefined || v === '') return '—';
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  if (typeof v === 'number') {
    if (/_paise$/.test(key)) return formatINR(v);
    if (/_pct$|rate/.test(key)) return `${Math.round(v * 10) / 10}%`;
    return v.toLocaleString('en-IN');
  }
  return String(v);
}

function label(key: string): string {
  return key.replace(/_paise$/, '').replace(/_pct$/, ' %').replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}
