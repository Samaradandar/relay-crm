import clsx from 'clsx';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import type { LeaderboardRow } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { PageHeader } from '@/components/layout/PageHeader';
import { TeamChip } from '@/components/ui/Badge';
import { SelectField } from '@/components/ui/Field';
import { Column, DataTable } from '@/components/ui/DataTable';
import { formatINR } from '@/lib/format';

const thisMonth = () => new Date().toISOString().slice(0, 7);

/** PRD Feature 13: one company-wide ranking, visible to everyone. No incentive figure ever
 * appears here (incentives are private, Personal sales dashboard only). */
export function LeaderboardPage() {
  const { me } = useAuth();
  const [month, setMonth] = useState(thisMonth());
  const [teamId, setTeamId] = useState('');
  const teamsQ = useQuery({ queryKey: ['teams'], queryFn: api.teams });
  const q = useQuery({ queryKey: ['leaderboard', month, teamId], queryFn: () => api.leaderboard(month, teamId || undefined) });

  const months = Array.from({ length: 12 }, (_, i) => {
    const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() - i);
    return d.toISOString().slice(0, 7);
  });

  const columns: Column<LeaderboardRow & { id: string }>[] = [
    { key: 'rank', label: 'Rank', render: (r) => <RankBadge rank={r.rank} /> },
    { key: 'team', label: 'Team', render: (r) => r.teamName ? <TeamChip name={r.teamName} color={r.teamColour ?? 'var(--brand)'} /> : <span className="text-ink-3">—</span> },
    { key: 'name', label: 'Name', render: (r) => <span className={clsx(r.isYou && 'font-semibold text-gold-ink')}>{r.name}{r.isYou && ' (you)'}</span> },
    { key: 'active', label: 'Active deals', numeric: true, render: (r) => r.activeDeals },
    { key: 'won', label: 'Won deals', numeric: true, render: (r) => r.wonDeals },
    { key: 'best', label: 'Revenue: Best', numeric: true, render: (r) => formatINR(r.revenueBestPaise, { compact: true }) },
    { key: 'current', label: 'Revenue: Current', numeric: true, render: (r) => formatINR(r.revenueCurrentPaise, { compact: true }) },
    { key: 'projected', label: 'Revenue: Projected', numeric: true, render: (r) => formatINR(r.revenueProjectedPaise, { compact: true }) },
    { key: 'target', label: 'Target', numeric: true, render: (r) => r.targetPaise != null ? formatINR(r.targetPaise, { compact: true }) : <span className="text-ink-3">Not set</span> },
  ];

  return <div className="flex flex-col gap-5">
    <PageHeader title="Leaderboard" subtitle="Company-wide ranking of sales reps. Past months stay visible to everyone." actions={<>
      <SelectField label="Month" value={month} onChange={(e) => setMonth(e.target.value)} options={months.map((m) => ({ value: m, label: m }))} />
      <SelectField label="Team" value={teamId} onChange={(e) => setTeamId(e.target.value)} options={[{ value: '', label: 'All teams' }, ...(teamsQ.data ?? []).map((t) => ({ value: t.id, label: t.name }))]} />
    </>} />
    {!q.data ? <div className="panel h-64 animate-pulse" /> : <DataTable
      columns={columns}
      rows={q.data.rows.map((r) => ({ ...r, id: r.userId }))}
      empty="No ranked reps for this month."
      footer={<span>{q.data.rows.length} reps ranked</span>}
    />}
    {me?.role === 'user' && <p className="m-0 text-[13px] text-ink-3">Your own row is highlighted. Incentives are never shown here — see My dashboard.</p>}
  </div>;
}

function RankBadge({ rank }: { rank: number | null }) {
  if (rank == null) return <span className="text-ink-3">—</span>;
  if (rank <= 3) return <span className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-gold text-[13px] font-bold text-gold-on">{rank}</span>;
  return <span className="text-ink-2">{rank}</span>;
}
