import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import { LEAD_SOURCES, type Lead } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/Button';
import { SelectField } from '@/components/ui/Field';
import { WAITING_SHORT } from '@/lib/leads';
import { AssignDialog } from './AssignDialog';
import { PoolTable } from './PoolTable';
import { RecentAssignments, TeamLoad } from './Distribution';

/** PRD 6 / Feature 5. TLs see their team's share; Admins and Super admins see the whole Pool. Reps have no access. */
export function PoolPage() {
  const { me } = useAuth(); const nav = useNavigate();
  const pool = useQuery({ queryKey: ['pool'], queryFn: api.pool });
  const load = useQuery({ queryKey: ['teamLoad'], queryFn: api.teamLoad });
  const recent = useQuery({ queryKey: ['recentAssignments'], queryFn: api.recentAssignments });
  const teams = useQuery({ queryKey: ['teams'], queryFn: api.teams });
  const [source, setSource] = useState(''); const [team, setTeam] = useState(''); const [reason, setReason] = useState('');
  const [assigning, setAssigning] = useState<Lead | null>(null);
  const all = me?.role !== 'team_leader';
  const rows = (pool.data ?? []).filter((l) => (!source || l.source === source) && (!team || l.teamId === team) && (!reason || l.waitingReason === reason));
  return <>
    <PageHeader title="Lead Pool" subtitle={all ? 'Every lead not yet assigned, across all teams. Oldest first.' : `Your team’s share of the Pool${me?.team ? ` (${me.team.name})` : ''}. Oldest first.`} actions={<Button variant="primary" icon="plus" onClick={() => nav('/app/leads/new')}>Add new lead</Button>} />
    <div className="mb-4 flex flex-wrap items-end gap-3">
      <SelectField label="Source" value={source} onChange={(e) => setSource(e.target.value)} options={[{ value: '', label: 'All sources' }, ...LEAD_SOURCES.map((s) => ({ value: s, label: s }))]} className="w-44" />
      {all && <SelectField label="Team" value={team} onChange={(e) => setTeam(e.target.value)} options={[{ value: '', label: 'All teams' }, ...(teams.data ?? []).map((t) => ({ value: t.id, label: t.name }))]} className="w-44" />}
      <SelectField label="Why it is waiting" value={reason} onChange={(e) => setReason(e.target.value)} options={[{ value: '', label: 'Any reason' }, ...Object.entries(WAITING_SHORT).map(([v, l]) => ({ value: v, label: l }))]} className="w-48" />
      <span className="pb-2 text-small text-ink-3">{rows.length} of {pool.data?.length ?? 0} leads</span>
    </div>
    <PoolTable leads={rows} teams={teams.data ?? []} showTeam={all} onAssign={setAssigning} empty={pool.data?.length ? 'No leads match these filters.' : 'The Pool is empty. Every lead has an owner.'} />
    <div className="mt-8 grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
      <section className="flex min-w-0 flex-col gap-3"><h2 className="m-0 text-heading">Distribution</h2><TeamLoad reps={(load.data ?? []).filter((r) => r.id !== me?.id)} teams={teams.data ?? []} showTeam={all} /></section>
      <section className="panel flex flex-col gap-3 p-5"><h2 className="m-0 text-heading">Recently assigned</h2><RecentAssignments leads={recent.data ?? []} /></section>
    </div>
    {assigning && <AssignDialog lead={assigning} onClose={() => setAssigning(null)} />}
  </>;
}
