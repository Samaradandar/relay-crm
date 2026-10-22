import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError, type Team } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { PageHeader } from '@/components/layout/PageHeader';
import { Avatar } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Banner, useToast } from '@/components/ui/Feedback';
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { Dialog } from '@/components/ui/Overlay';
import { formatDateTimeIST } from '@/lib/format';

type Action = null | { kind: 'create' } | { kind: 'edit' | 'move' | 'leader'; team: Team };

export function TeamsPage() {
  const teams = useQuery({ queryKey: ['teams'], queryFn: api.teams });
  const users = useQuery({ queryKey: ['users'], queryFn: api.users });
  const [action, setAction] = useState<Action>(null);
  const name = (id: string | null) => users.data?.find((u) => u.id === id)?.name ?? '—';
  return <>
    <PageHeader title="Teams" subtitle="Each team has exactly one Team Leader. A sales rep belongs to one team at a time." actions={<Button variant="primary" icon="users" onClick={() => setAction({ kind: 'create' })}>Create team</Button>} />
    <div className="grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-4">
      {(teams.data ?? []).map((t) => {
        const reps = t.memberIds.filter((m) => m !== t.teamLeaderId);
        return <section key={t.id} className="panel flex flex-col gap-4 p-5">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2.5"><span className="h-7 w-7 rounded-full shadow-[inset_0_0_0_1px_rgba(0,0,0,.12)]" style={{ background: t.color }} /><h2 className="m-0 text-heading">{t.name}</h2></div>
            <Button size="sm" variant="quiet" onClick={() => setAction({ kind: 'edit', team: t })}>Edit</Button>
          </div>
          <dl className="m-0 grid grid-cols-[110px_minmax(0,1fr)] gap-x-3 gap-y-2 text-[13px]">
            <dt className="text-ink-2">Team Leader</dt><dd className="m-0 flex items-center gap-2 font-semibold"><Avatar name={name(t.teamLeaderId)} size={22} />{name(t.teamLeaderId)}</dd>
            <dt className="text-ink-2">Sales reps</dt><dd className="m-0">{reps.length}</dd>
            <dt className="text-ink-2">Created</dt><dd className="m-0">{formatDateTimeIST(t.createdAt).split(',').slice(0, 1).join('')}</dd>
          </dl>
          <div className="flex flex-wrap gap-1">{reps.map((m) => <Avatar key={m} name={name(m)} size={26} />)}</div>
          <div className="flex flex-wrap gap-2 border-t border-line pt-3"><Button size="sm" disabled={reps.length === 0 || (teams.data ?? []).length < 2} onClick={() => setAction({ kind: 'move', team: t })}>Move a rep</Button><Button size="sm" variant="quiet" disabled={reps.length === 0 && !t.teamLeaderId} onClick={() => setAction({ kind: 'leader', team: t })}>Change Team Leader</Button></div>
        </section>;
      })}
    </div>
    {action?.kind === 'create' && <CreateTeam onClose={() => setAction(null)} />}
    {action?.kind === 'edit' && <EditTeam team={action.team} onClose={() => setAction(null)} />}
    {action?.kind === 'move' && <MoveRep team={action.team} teams={teams.data ?? []} onClose={() => setAction(null)} />}
    {action?.kind === 'leader' && <ChangeLeader team={action.team} onClose={() => setAction(null)} />}
  </>;
}

/** The shell every team change shares: its fields, a reason (required for an Admin), and the refresh. */
function Change({ title, description, action, ready, run, done, onClose, children, extra }: { title: string; description?: string; action: string; ready: boolean; run: (reason: string) => Promise<unknown>; done: string; onClose: () => void; children: React.ReactNode; extra?: React.ReactNode }) {
  const { me } = useAuth(); const qc = useQueryClient(); const toast = useToast();
  const [reason, setReason] = useState(''); const [error, setError] = useState<string | null>(null);
  const need = me?.role !== 'super_admin';
  const m = useMutation({ mutationFn: () => run(reason.trim()), onSuccess: () => { ['teams', 'users', 'deals', 'pool'].forEach((k) => qc.invalidateQueries({ queryKey: [k] })); toast({ message: done }); onClose(); }, onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not save.') });
  return <Dialog title={title} description={description} onClose={onClose} footer={<><Button variant="quiet" onClick={onClose}>Cancel</Button>{extra}<Button variant="primary" disabled={!ready || (need && !reason.trim())} loading={m.isPending} onClick={() => { setError(null); m.mutate(); }}>{action}</Button></>}>
    <div className="flex flex-col gap-3">{children}
      <TextAreaField label="Reason" required={need} optionalNote={need ? undefined : '(optional)'} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} hint="Saved in the audit log." />
      {error && <Banner tone="danger" title={error} />}
    </div>
  </Dialog>;
}

function CreateTeam({ onClose }: { onClose: () => void }) {
  const [n, setN] = useState('');
  return <Change title="Create a team" description="It starts empty. Add reps from Users, then choose its Team Leader." action="Create team" ready={!!n.trim()} run={(r) => api.createTeam(n.trim(), r)} done="Team created" onClose={onClose}>
    <TextField label="Team name" required value={n} onChange={(e) => setN(e.target.value)} autoFocus />
  </Change>;
}

function EditTeam({ team, onClose }: { team: Team; onClose: () => void }) {
  const [n, setN] = useState(team.name); const toast = useToast(); const qc = useQueryClient();
  const colour = useMutation({ mutationFn: () => api.regenerateTeamColour(team.id), onSuccess: () => { qc.invalidateQueries({ queryKey: ['teams'] }); toast({ message: 'New colour picked' }); } });
  return <Change title={`Edit ${team.name}`} action="Save" ready={!!n.trim() && n.trim() !== team.name} run={(r) => api.renameTeam(team.id, n.trim(), r)} done="Team renamed" onClose={onClose}
    extra={<Button variant="quiet" loading={colour.isPending} onClick={() => colour.mutate()}>New colour</Button>}>
    <TextField label="Team name" required value={n} onChange={(e) => setN(e.target.value)} />
  </Change>;
}

function MoveRep({ team, teams, onClose }: { team: Team; teams: Team[]; onClose: () => void }) {
  const users = useQuery({ queryKey: ['users'], queryFn: api.users });
  const reps = (users.data ?? []).filter((u) => team.memberIds.includes(u.id) && u.id !== team.teamLeaderId && u.active);
  const [rep, setRep] = useState(''); const [to, setTo] = useState(''); const [open, setOpen] = useState<'move' | 'return_to_pool'>('move');
  return <Change title={`Move a rep out of ${team.name}`} description="A rep belongs to one team at a time." action="Move" ready={!!rep && !!to} run={(r) => api.moveRep(rep, to, open, r)} done="Rep moved" onClose={onClose}>
    <SelectField label="Rep" required value={rep} onChange={(e) => setRep(e.target.value)} options={[{ value: '', label: 'Choose…' }, ...reps.map((u) => ({ value: u.id, label: u.name }))]} />
    <SelectField label="Move to" required value={to} onChange={(e) => setTo(e.target.value)} options={[{ value: '', label: 'Choose…' }, ...teams.filter((t) => t.id !== team.id).map((t) => ({ value: t.id, label: t.name }))]} />
    <SelectField label="Their open deals" value={open} onChange={(e) => setOpen(e.target.value as 'move' | 'return_to_pool')} options={[{ value: 'move', label: 'Go with them to the new team' }, { value: 'return_to_pool', label: 'Go back to the Lead Pool' }]} />
  </Change>;
}

function ChangeLeader({ team, onClose }: { team: Team; onClose: () => void }) {
  const users = useQuery({ queryKey: ['users'], queryFn: api.users });
  const reps = (users.data ?? []).filter((u) => team.memberIds.includes(u.id) && u.id !== team.teamLeaderId && u.active && u.role === 'user');
  const [next, setNext] = useState('');
  return <Change title={`Change the Team Leader of ${team.name}`} description="The current Team Leader becomes a sales rep in the team, and the person you choose becomes the Team Leader." action="Change Team Leader" ready={!!next} run={(r) => api.changeTeamLeader(team.id, next, team.teamLeaderId, r)} done="Team Leader changed" onClose={onClose}>
    <SelectField label="New Team Leader" required value={next} onChange={(e) => setNext(e.target.value)} options={[{ value: '', label: 'Choose a rep in this team…' }, ...reps.map((u) => ({ value: u.id, label: u.name }))]} />
  </Change>;
}
