import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/Button';
import { Column, DataTable } from '@/components/ui/DataTable';
import { useReasonPrompt } from '@/components/ui/ReasonPrompt';
import { formatINR } from '@/lib/format';

const thisMonth = () => new Date().toISOString().slice(0, 7);

/** PRD P6-04: a Team Leader sets each rep's monthly target. Past months are read-only here
 * (Admin/SA corrections happen elsewhere); the current month is always editable. */
export function TargetsPage() {
  const [month] = useState(thisMonth());
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const qc = useQueryClient();
  const o = useQuery({ queryKey: ['teamOverview'], queryFn: api.teamOverview });
  const { ask, element } = useReasonPrompt(() => qc.invalidateQueries({ queryKey: ['teamOverview'] }));

  const columns: Column<{ id: string; name: string; target: number; revenueMonth: number }>[] = [
    { key: 'name', label: 'Rep', render: (r) => r.name },
    { key: 'current', label: 'Revenue: Current', numeric: true, render: (r) => formatINR(r.revenueMonth, { compact: true }) },
    { key: 'target', label: `Target (${month})`, numeric: true, render: (r) => formatINR(r.target, { compact: true }) },
    {
      key: 'edit', label: 'New target (₹)', render: (r) => <div className="flex items-center justify-end gap-2">
        <input
          type="number" min={0} placeholder={String(Math.round(r.target / 100))}
          className="h-8 w-28 rounded-md border border-line-control bg-surface-1 px-2 text-right text-sm"
          value={drafts[r.id] ?? ''} onChange={(e) => setDrafts((d) => ({ ...d, [r.id]: e.target.value }))}
        />
        <Button size="sm" disabled={!drafts[r.id]} onClick={() => ask({
          title: `Set ${r.name}'s target for ${month}`,
          description: `New target: ${formatINR(Number(drafts[r.id]) * 100, { compact: true })}`,
          action: 'Set target', done: 'Target saved.',
          run: (reason) => api.setTarget(r.id, month, Math.round(Number(drafts[r.id]) * 100), reason),
        })}>Save</Button>
      </div>,
    },
  ];

  return <div className="flex flex-col gap-5">
    <PageHeader title="Targets" subtitle="Set each rep's monthly revenue target. Visible to everyone on the Leaderboard." />
    {!o.data ? <div className="panel h-64 animate-pulse" /> : <DataTable columns={columns} rows={o.data.reps.map((r) => ({ id: r.id, name: r.name, target: r.target, revenueMonth: r.revenueMonth }))} empty="No reps in your team." />}
    {element}
  </div>;
}
