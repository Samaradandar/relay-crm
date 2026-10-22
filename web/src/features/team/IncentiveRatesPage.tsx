import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/Button';
import { Column, DataTable } from '@/components/ui/DataTable';
import { useReasonPrompt } from '@/components/ui/ReasonPrompt';
import { formatDateTimeIST, formatINR } from '@/lib/format';

/** PRD P6-05: "Set incentive rates" queue — this team's Won deals with no rate set yet. Private;
 * only visible to the deal's Team Leader, Admins and Super admins (incentive_rate.set). */
export function IncentiveRatesPage() {
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['missingRates'], queryFn: api.missingIncentiveRates });
  const { ask, element } = useReasonPrompt(() => qc.invalidateQueries({ queryKey: ['missingRates'] }));

  const columns: Column<{ id: string; wonValuePaise: number; wonAt: string }>[] = [
    { key: 'won', label: 'Won value', numeric: true, render: (r) => formatINR(r.wonValuePaise, { compact: true }) },
    { key: 'at', label: 'Won on', render: (r) => formatDateTimeIST(r.wonAt) },
    {
      key: 'rate', label: 'Incentive rate (%)', render: (r) => <div className="flex items-center justify-end gap-2">
        <input
          type="number" min={0} max={100} step={0.5} placeholder="e.g. 5"
          className="h-8 w-20 rounded-md border border-line-control bg-surface-1 px-2 text-right text-sm"
          value={drafts[r.id] ?? ''} onChange={(e) => setDrafts((d) => ({ ...d, [r.id]: e.target.value }))}
        />
        <Button size="sm" disabled={!drafts[r.id]} onClick={() => ask({
          title: 'Set the incentive rate on this deal',
          description: `${drafts[r.id]}% of money received, once fully paid.`,
          action: 'Set rate', done: 'Incentive rate saved.',
          run: (reason) => api.setIncentiveRate(r.id, Math.round(Number(drafts[r.id]) * 100), reason),
        })}>Save</Button>
      </div>,
    },
  ];

  return <div className="flex flex-col gap-5">
    <PageHeader title="Set incentive rates" subtitle="Your team's Won deals with no incentive % set yet." />
    {!q.data ? <div className="panel h-64 animate-pulse" /> : <DataTable columns={columns} rows={q.data.map((r) => ({ id: r.dealId, wonValuePaise: r.wonValuePaise, wonAt: r.wonAt }))} empty="Nothing waiting — every Won deal has a rate." />}
    {element}
  </div>;
}
