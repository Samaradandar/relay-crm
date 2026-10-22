import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import { useAuth } from '@/auth/AuthProvider';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Banner } from '@/components/ui/Feedback';
import { Column, DataTable } from '@/components/ui/DataTable';
import { TargetBar } from '@/features/team/TeamBits';
import { formatDateTimeIST, formatINR } from '@/lib/format';

const STATUS: Record<string, [React.ComponentProps<typeof Badge>['tone'], string]> = {
  no_rate: ['neutral', 'Rate not set'], pending: ['accent', 'Pending'], earned: ['success', 'Earned'],
};

/** PRD P6-07: a sales rep's own rank, numbers, target progress and private incentives. */
export function MyDashboardPage() {
  const { me } = useAuth();
  const numbers = useQuery({ queryKey: ['myNumbers'], queryFn: api.myNumbers });
  const incentives = useQuery({ queryKey: ['myIncentives', me?.id], queryFn: () => api.myIncentives(me!.id), enabled: !!me });

  if (numbers.isError) return <Banner tone="danger" title="This dashboard is for sales reps only." />;
  if (!numbers.data) return <div className="panel h-64 animate-pulse" />;
  const n = numbers.data;

  const columns: Column<{ id: string; rateBp: number | null; expectedTotalPaise: number | null; receivedPaise: number; pendingAmountPaise: number; earnedAmountPaise: number; status: string; earnedAt: string | null }>[] = [
    { key: 'rate', label: 'Rate', render: (r) => r.rateBp != null ? `${(r.rateBp / 100).toFixed(1)}%` : <span className="text-ink-3">Not set</span> },
    { key: 'expected', label: 'Expected total', numeric: true, render: (r) => r.expectedTotalPaise != null ? formatINR(r.expectedTotalPaise, { compact: true }) : '—' },
    { key: 'received', label: 'Received so far', numeric: true, render: (r) => formatINR(r.receivedPaise, { compact: true }) },
    { key: 'status', label: 'Status', render: (r) => { const [tone, label] = STATUS[r.status] ?? ['neutral', r.status]; return <Badge tone={tone}>{label}</Badge>; } },
    { key: 'amount', label: 'Pending / Earned', numeric: true, render: (r) => r.status === 'earned' ? formatINR(r.earnedAmountPaise, { compact: true }) : formatINR(r.pendingAmountPaise, { compact: true }) },
    { key: 'earnedAt', label: 'Earned at', render: (r) => r.earnedAt ? formatDateTimeIST(r.earnedAt) : '—' },
  ];

  const totalEarned = (incentives.data ?? []).reduce((s, i) => s + i.earnedAmountPaise, 0);

  return <div className="flex flex-col gap-5">
    <PageHeader title="My dashboard" subtitle="Your rank, this month's numbers and your private incentives." />
    <div className="grid gap-4 md:grid-cols-[minmax(0,1.4fr)_repeat(3,minmax(0,1fr))]">
      <section className="panel p-4"><span className="text-[13px] font-medium text-ink-2">Revenue: Current vs target</span><TargetBar value={n.revenueMonth} target={n.target} /></section>
      <Tile label="Rank" value={n.rank ? `#${n.rank} of ${n.reps}` : '—'} hint={!n.rank ? 'No revenue yet this month' : n.behindNext != null ? `${formatINR(n.behindNext, { compact: true })} behind next` : 'You are #1'} />
      <Tile label="Revenue: Best" value={formatINR(n.best, { compact: true })} hint="Your best month, including this one" />
      <Tile label="Revenue: Projected" value={formatINR(n.projected, { compact: true })} hint="Current + weighted open deals" />
    </div>

    <section className="panel flex flex-col gap-3 p-4">
      <h2 className="m-0 text-subheading">My incentives (private)</h2>
      <p className="m-0 text-[13px] text-ink-2">Only you, your Team Leader, Admins and Super admins can see this. Earned once a deal is fully paid; frozen afterwards even after a refund or churn.</p>
      {!incentives.data ? <div className="h-24 animate-pulse rounded-lg bg-surface-2" /> : <DataTable columns={columns} rows={incentives.data.map((i) => ({ ...i, id: i.dealId }))} empty="No Won deals yet." footer={<span>Earned this month and all-time: <b className="text-ink">{formatINR(totalEarned, { compact: true })}</b></span>} />}
    </section>
  </div>;
}

function Tile({ label, value, hint }: { label: string; value: string; hint: string }) {
  return <div className="panel flex flex-col gap-0.5 p-4"><span className="text-[13px] font-medium text-ink-2">{label}</span><span className="text-metric">{value}</span><span className="text-xs text-ink-3">{hint}</span></div>;
}
