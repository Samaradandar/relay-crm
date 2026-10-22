/* Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta. */
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import type { ChurnHistoryRow } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Column, DataTable } from '@/components/ui/DataTable';
import { StatTile } from '@/components/ui/Feedback';
import { SelectField, TextField } from '@/components/ui/Field';
import { useReasonPrompt } from '@/components/ui/ReasonPrompt';
import { formatINR } from '@/lib/format';

type Sort = 'churn_date' | 'value_lost' | 'time_as_customer';
const PAGE_SIZES = [10, 25, 50];

/** PRD Feature 9a: nothing is entered here — every churn recorded elsewhere shows up automatically. */
export function ChurnHistoryPage() {
  const { me } = useAuth();
  const [dateFrom, setDateFrom] = useState(''); const [dateTo, setDateTo] = useState(''); const [reason, setReason] = useState('');
  const [sort, setSort] = useState<Sort>('churn_date'); const [desc, setDesc] = useState(true);
  const [page, setPage] = useState(1); const [pageSize, setPageSize] = useState(25);
  const { ask, element: exportDialog } = useReasonPrompt();

  const summary = useQuery({ queryKey: ['churnHistorySummary', 'reason'], queryFn: () => api.churnHistorySummary('reason') });
  const repBreakdown = useQuery({ queryKey: ['churnHistorySummary', 'rep'], queryFn: () => api.churnHistorySummary('rep'), enabled: me?.role === 'team_leader' || me?.role === 'admin' || me?.role === 'super_admin' });
  const history = useQuery({ queryKey: ['churnHistory', { dateFrom, dateTo, reason, sort, desc, page, pageSize }], queryFn: () => api.churnHistory({ dateFrom: dateFrom || undefined, dateTo: dateTo || undefined, reason: reason || undefined, sort, desc, page, pageSize }) });

  const doExport = () => ask({ title: 'Export Churn History to CSV', action: 'Export', done: 'Export logged', run: (r) => api.logExport('Churn History', history.data?.rows.length ?? 0, r) });

  const columns: Column<ChurnHistoryRow>[] = [
    { key: 'account', label: 'Customer', render: (r) => <span className="flex items-center gap-2"><Link to={`/app/accounts/${r.accountId}`} className="font-semibold text-brand-ink no-underline hover:underline">{r.accountName}</Link>{r.returned && <Badge tone="success">Returned</Badge>}{r.reversedAt && <Badge tone="neutral">Reversed</Badge>}</span> },
    { key: 'owner', label: 'Account owner', render: (r) => r.accountOwnerName ?? '—' },
    { key: 'date', label: 'Churn date', render: (r) => r.churnDate },
    { key: 'reason', label: 'Reason', render: (r) => r.reason },
    { key: 'notes', label: 'Notes', render: (r) => r.notes ?? '—' },
    { key: 'value', label: 'Monthly value lost', numeric: true, render: (r) => formatINR(r.monthlyValueLostPaise) },
    { key: 'start', label: 'Subscription start', render: (r) => r.subscriptionStart },
    { key: 'tenure', label: 'Time as customer', numeric: true, render: (r) => `${r.timeAsCustomerDays}d` },
    { key: 'renewal', label: 'Last renewal', render: (r) => r.lastRenewalDate ?? '—' },
    { key: 'by', label: 'Recorded by', render: (r) => r.recordedByName ?? '—' }
  ];

  const s = summary.data;
  return <div className="flex flex-col gap-5">
    <PageHeader title="Churn History" subtitle="Every customer who has churned, company-wide and scoped to what you can see." actions={<Button icon="file" onClick={doExport} disabled={!history.data?.rows.length}>Export CSV</Button>} />

    {s && <div className="flex flex-wrap gap-3">
      <StatTile label="Churned this month" value={s.churnedThisMonth} />
      <StatTile label="Churned this quarter" value={s.churnedThisQuarter} />
      <StatTile label="Churned all time" value={s.churnedAllTime} />
      <StatTile label="Churn rate (this month)" value={s.churnRateThisMonth ?? '—'} />
      <StatTile label="Total monthly value lost" value={formatINR(s.totalMonthlyValueLostPaise)} />
    </div>}

    {s && s.topReasons.length > 0 && <section className="panel flex flex-col gap-2 p-5">
      <h2 className="m-0 text-heading">Top reasons</h2>
      <div className="flex flex-wrap gap-2">{s.topReasons.map((r) => <Badge key={r.reason} tone="warning">{r.reason} · {r.count}</Badge>)}</div>
    </section>}

    {repBreakdown.data?.byRep && <section className="panel flex flex-col gap-2 p-5">
      <h2 className="m-0 text-heading">By rep</h2>
      <div className="flex flex-wrap gap-2">{repBreakdown.data.byRep.map((r, i) => <Badge key={i} tone="neutral">{r.ownerName ?? 'Unassigned'} · {r.count} · {formatINR(r.valueLostPaise)}</Badge>)}</div>
    </section>}

    <section className="panel flex flex-wrap items-end gap-3 p-4">
      <TextField label="From" type="date" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setPage(1); }} />
      <TextField label="To" type="date" value={dateTo} onChange={(e) => { setDateTo(e.target.value); setPage(1); }} />
      <TextField label="Reason" optionalNote="(exact match)" value={reason} onChange={(e) => { setReason(e.target.value); setPage(1); }} className="max-w-[200px]" />
      <SelectField label="Sort by" value={sort} onChange={(e) => setSort(e.target.value as Sort)} options={[{ value: 'churn_date', label: 'Churn date' }, { value: 'value_lost', label: 'Value lost' }, { value: 'time_as_customer', label: 'Time as customer' }]} />
      <Button variant="quiet" onClick={() => setDesc((d) => !d)}>{desc ? 'Descending' : 'Ascending'}</Button>
      <SelectField label="Page size" value={String(pageSize)} onChange={(e) => { setPageSize(Number(e.target.value)); setPage(1); }} options={PAGE_SIZES.map((n) => ({ value: String(n), label: String(n) }))} />
    </section>

    <DataTable columns={columns} rows={history.data?.rows ?? []} empty={history.isLoading ? 'Loading…' : 'No churn recorded.'}
      footer={history.data && <>
        <span>{history.data.total} total</span>
        <div className="flex gap-2"><Button size="sm" variant="quiet" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>Previous</Button><Button size="sm" variant="quiet" disabled={page * pageSize >= history.data.total} onClick={() => setPage((p) => p + 1)}>Next</Button></div>
      </>} />
    {exportDialog}
  </div>;
}
