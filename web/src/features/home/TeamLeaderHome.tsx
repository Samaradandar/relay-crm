import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import type { Lead } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { TeamChip } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { formatWaiting } from '@/lib/format';
import { AssignDialog } from '@/features/pool/AssignDialog';
import { PoolTable, useNow } from '@/features/pool/PoolTable';
import { RecentAssignments, TeamLoad, TransferList } from '@/features/pool/Distribution';
import { Hero, greeting } from './Hero';

/** Team Leader home, Phase 2: leads waiting in the team's share of the Pool come first, then team load and transfers. */
export function TeamLeaderHome() {
  const { me } = useAuth(); const nav = useNavigate(); const now = useNow();
  const pool = useQuery({ queryKey: ['pool'], queryFn: api.pool });
  const load = useQuery({ queryKey: ['teamLoad'], queryFn: api.teamLoad });
  const recent = useQuery({ queryKey: ['recentAssignments'], queryFn: api.recentAssignments });
  const approvals = useQuery({ queryKey: ['approvals'], queryFn: api.approvals });
  const teams = useQuery({ queryKey: ['teams'], queryFn: api.teams });
  const [assigning, setAssigning] = useState<Lead | null>(null);
  if (!me) return null;

  const waiting = pool.data ?? []; const reps = (load.data ?? []).filter((r) => r.active && r.id !== me.id);
  const atCap = reps.filter((r) => r.activeLeads >= r.cap).length;
  const free = reps.reduce((n, r) => n + Math.max(0, r.cap - r.activeLeads), 0);
  const today = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(new Date());
  const todays = (recent.data ?? []).filter((l) => new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata' }).format(new Date(l.assignedAt!)) === today);
  const transfers = (approvals.data ?? []).filter((a) => a.type === 'deal.team_transfer');
  const oldest = waiting[0];
  const line = waiting.length ? `${waiting.length} lead${waiting.length > 1 ? 's' : ''} waiting for you to assign · oldest ${formatWaiting(oldest.pooledAt, now)}` : 'Nothing waiting in your Pool';

  return <div className="flex flex-col gap-6">
    <Hero title={`${greeting()}, ${me.name.split(' ')[0]}`} meta={<>{me.team && <TeamChip name={me.team.name} color={me.team.color} />}<span>{line}</span></>}
      actions={<><Button icon="pool" onClick={() => nav('/app/pool')}>Open Lead Pool</Button><Button variant="primary" icon="plus" onClick={() => nav('/app/leads/new')}>Add new lead</Button></>} />

    <section aria-labelledby="wait-h" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h2 id="wait-h" className="m-0 text-heading">Waiting for you to assign</h2><p className="m-0 text-small text-ink-2">Relay could not place these automatically. Assign one by hand, or raise a rep’s cap in settings.</p></div>
        {waiting.length > 5 && <Link to="/app/pool" className="text-[13px] font-semibold text-brand-ink">See all {waiting.length}</Link>}
      </div>
      <PoolTable leads={waiting.slice(0, 5)} teams={teams.data ?? []} onAssign={setAssigning} empty="Every lead in your team’s share of the Pool has an owner." />
    </section>

    <div className="grid gap-4 sm:grid-cols-3">
      <Tile label="Assigned today" value={todays.length} hint={`${todays.filter((l) => l.method === 'rule').length} by rule · ${todays.filter((l) => l.method === 'round_robin').length} round-robin · ${todays.filter((l) => l.method === 'manual').length} by hand`} />
      <Tile label="Reps at their cap" value={<>{atCap}<span className="text-base font-medium text-ink-3"> of {reps.length}</span></>} hint={atCap ? 'Round-robin skips them until a lead closes or the cap goes up' : 'Everyone can take new leads'} warn={atCap > 0 && atCap === reps.length} />
      <Tile label="Room left in the team" value={free} hint="Leads the team can still take before every rep is at cap" />
    </div>

    <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
      <section aria-labelledby="load-h" className="flex min-w-0 flex-col gap-3">
        <h2 id="load-h" className="m-0 text-heading">Team load</h2>
        <TeamLoad reps={(load.data ?? []).filter((r) => r.id !== me.id)} teams={teams.data ?? []} />
        <p className="m-0 text-xs text-ink-3">Team Leaders never get round-robin leads. You can still take a lead yourself: pick your own name when you assign it.</p>
      </section>
      <aside className="flex flex-col gap-4">
        <section className="panel flex flex-col gap-3 p-5"><h2 className="m-0 text-heading">Transfer requests</h2><TransferList items={transfers} myTeamId={me.teamId} myId={me.id} teams={teams.data ?? []} /></section>
        <section className="panel flex flex-col gap-3 p-5"><h2 className="m-0 text-heading">Recently assigned</h2><RecentAssignments leads={recent.data ?? []} /></section>
      </aside>
    </div>
    {assigning && <AssignDialog lead={assigning} onClose={() => setAssigning(null)} />}
  </div>;
}

function Tile({ label, value, hint, warn }: { label: string; value: React.ReactNode; hint: string; warn?: boolean }) {
  return <div className={`panel flex flex-col gap-1 p-4 ${warn ? '!border-warning' : ''}`}><span className="text-[13px] font-medium text-ink-2">{label}</span><span className="text-metric">{value}</span><span className={`text-xs ${warn ? 'font-medium text-warning' : 'text-ink-3'}`}>{hint}</span></div>;
}
