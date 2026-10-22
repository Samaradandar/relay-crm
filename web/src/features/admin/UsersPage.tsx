import { useMemo, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type NewUserInput } from '@/api';
import { ApiError, type Role, type User } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { ROLE_LABEL, can, reasonFor } from '@/auth/permissions';
import { PageHeader } from '@/components/layout/PageHeader';
import { DataTable, type Column } from '@/components/ui/DataTable';
import { Avatar, Badge, TeamChip } from '@/components/ui/Badge';
import { Banner, ReasonBox, useToast } from '@/components/ui/Feedback';
import { Button } from '@/components/ui/Button';
import { SelectField, TextField } from '@/components/ui/Field';
import { formatRelativeIST } from '@/lib/format';
import { Dialog } from '@/components/ui/Overlay';
import { useReasonPrompt } from '@/components/ui/ReasonPrompt';

const TONE: Record<Role, 'accent' | 'brand' | 'neutral'> = { super_admin: 'accent', admin: 'brand', team_leader: 'neutral', user: 'neutral' };

export function UsersPage() {
  const { me } = useAuth();
  const users = useQuery({ queryKey: ['users'], queryFn: api.users });
  const teams = useQuery({ queryKey: ['teams'], queryFn: api.teams });
  const [role, setRole] = useState('all'); const [team, setTeam] = useState('all'); const [status, setStatus] = useState('active');
  const [adding, setAdding] = useState(false); const [managing, setManaging] = useState<User | null>(null);
  const teamById = useMemo(() => Object.fromEntries((teams.data ?? []).map((t) => [t.id, t])), [teams.data]);
  const rows = (users.data ?? []).filter((u) => (role === 'all' || u.role === role) && (team === 'all' || u.teamId === team) && (status === 'all' || (status === 'active') === u.active));
  const supers = (users.data ?? []).filter((u) => u.role === 'super_admin' && u.active);

  const columns: Column<User>[] = [
    { key: 'name', label: 'Name', render: (u) => <span className="flex items-center gap-2"><Avatar name={u.name} /><span className="flex flex-col"><span className="font-semibold">{u.name}</span><span className="text-xs text-ink-3">{u.email}</span></span></span> },
    { key: 'role', label: 'Role', render: (u) => <Badge tone={TONE[u.role]}>{ROLE_LABEL[u.role]}</Badge> },
    { key: 'team', label: 'Team', render: (u) => u.teamId && teamById[u.teamId] ? <TeamChip name={teamById[u.teamId].name} color={teamById[u.teamId].color} /> : <span className="text-ink-3">All teams</span> },
    { key: 'status', label: 'Status', render: (u) => <Badge tone={u.active ? 'success' : 'neutral'} dot>{u.active ? 'Active' : 'Deactivated'}</Badge> },
    { key: 'last', label: 'Last sign-in', render: (u) => u.lastSignInAt ? formatRelativeIST(u.lastSignInAt) : <span className="text-ink-3">Never</span> },
    { key: 'manage', label: '', render: (u) => (me?.role === 'super_admin' || (me?.role === 'admin' && (u.role === 'user' || u.role === 'team_leader'))) && u.id !== me?.id ? <Button size="sm" onClick={() => setManaging(u)}>Manage</Button> : null }
  ];
  const counts = (users.data ?? []).reduce<Record<string, number>>((a, u) => { if (u.active) a[u.role] = (a[u.role] ?? 0) + 1; return a; }, {});

  return <>
    <PageHeader title="Users" subtitle={`${counts.user ?? 0} sales reps · ${counts.team_leader ?? 0} Team Leaders · ${counts.admin ?? 0} Admins · ${counts.super_admin ?? 0} Super admins`}
      actions={can(me, 'user.manage') && <Button variant="primary" icon="user" onClick={() => setAdding(true)}>Add user</Button>} />
    <div className="mb-4 flex flex-wrap items-end gap-3">
      <SelectField label="Role" className="w-44" value={role} onChange={(e) => setRole(e.target.value)} options={[{ value: 'all', label: 'All roles' }, ...(['user', 'team_leader', 'admin', 'super_admin'] as Role[]).map((r) => ({ value: r, label: ROLE_LABEL[r] }))]} />
      <SelectField label="Team" className="w-44" value={team} onChange={(e) => setTeam(e.target.value)} options={[{ value: 'all', label: 'All teams' }, ...(teams.data ?? []).map((t) => ({ value: t.id, label: t.name }))]} />
      <SelectField label="Status" className="w-40" value={status} onChange={(e) => setStatus(e.target.value)} options={[{ value: 'active', label: 'Active' }, { value: 'inactive', label: 'Deactivated' }, { value: 'all', label: 'All' }]} />
    </div>
    <div className="flex flex-wrap items-start gap-5">
      <div className="min-w-0 flex-[2_1_620px]"><DataTable columns={columns} rows={rows} footer={<span>{rows.length} of {users.data?.length ?? 0} people</span>} /></div>
      {adding && <AddUser onClose={() => setAdding(false)} teams={(teams.data ?? []).map((t) => ({ value: t.id, label: t.name }))} />}
    </div>
    {managing && <ManageUser u={managing} onClose={() => setManaging(null)} teams={(teams.data ?? []).map((t) => ({ value: t.id, label: t.name }))} />}
    {supers.length === 1 && <div className="mt-5"><Banner tone="info" title={`${supers[0].name} is the only Super admin`}>The last active Super admin cannot be deactivated or demoted. Add a second Super admin first.</Banner></div>}
  </>;
}

function AddUser({ onClose, teams }: { onClose: () => void; teams: { value: string; label: string }[] }) {
  const { me } = useAuth();
  const qc = useQueryClient(); const toast = useToast();
  const [f, setF] = useState<NewUserInput>({ name: '', email: '', role: 'user', teamId: teams[0]?.value ?? null });
  const [reason, setReason] = useState(''); const [error, setError] = useState<string | null>(null);
  const policy = reasonFor(me, 'user.manage');
  const roles: Role[] = me?.role === 'super_admin' ? ['user', 'team_leader', 'admin', 'super_admin'] : ['user', 'team_leader'];
  const save = useMutation({
    mutationFn: () => api.createUser({ ...f, teamId: f.role === 'admin' || f.role === 'super_admin' ? null : f.teamId }, reason),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ['users'] }); qc.invalidateQueries({ queryKey: ['approvals'] }); toast({ message: 'approvalId' in r ? 'Request sent to the Admins' : `${f.name} added. Invite email sent.` }); onClose(); },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not add the user.')
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!f.name.trim() || !/^\S+@\S+\.\S+$/.test(f.email)) return setError('Enter a full name and a valid work email.');
    if (policy === 'required' && !reason.trim()) return setError('Add a reason before saving.');
    save.mutate();
  };
  return (
    <form onSubmit={submit} aria-label="Add user" className="panel flex min-w-0 flex-[1_1_320px] flex-col gap-4 p-5" noValidate>
      <div><h2 className="m-0 text-heading">Add user</h2><p className="m-0 mt-1 text-small text-ink-2">They sign in with Google or set a password from the invite email.</p></div>
      <TextField id="n-name" label="Full name" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
      <TextField id="n-email" label="Work email" type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} placeholder="name@adandar.com" />
      <SelectField id="n-role" label="Role" required value={f.role} onChange={(e) => setF({ ...f, role: e.target.value as Role })} options={roles.map((r) => ({ value: r, label: ROLE_LABEL[r] }))} hint={me?.role !== 'super_admin' ? 'Only a Super admin can create Admins and Super admins.' : undefined} />
      {(f.role === 'user' || f.role === 'team_leader') && <SelectField id="n-team" label="Team" required value={f.teamId ?? ''} onChange={(e) => setF({ ...f, teamId: e.target.value })} options={teams} hint="A sales rep must be in a team before first sign-in." />}
      <ReasonBox id="n-reason" policy={policy} value={reason} onChange={setReason} placeholder="e.g. Joining 8 Oct to cover Pune accounts" />
      {error && <Banner tone="danger" title={error} />}
      <div className="flex justify-end gap-2"><Button variant="quiet" onClick={onClose}>Cancel</Button><Button type="submit" variant="primary" loading={save.isPending}>Create and send invite</Button></div>
    </form>
  );
}

/** PRD 4.4 L: Admins manage Users and Team Leaders (reason); only a Super admin touches Admins and Super admins. */
function ManageUser({ u, onClose, teams }: { u: User; onClose: () => void; teams: { value: string; label: string }[] }) {
  const { me } = useAuth(); const qc = useQueryClient();
  const [role, setRole] = useState<Role>(u.role); const [teamId, setTeamId] = useState(u.teamId ?? teams[0]?.value ?? '');
  const { ask, element } = useReasonPrompt(() => { ['users', 'teams', 'teamOverview', 'companyOverview', 'assignmentConfig'].forEach((k) => qc.invalidateQueries({ queryKey: [k] })); onClose(); });
  const roles: Role[] = me?.role === 'super_admin' ? ['user', 'team_leader', 'admin', 'super_admin'] : ['user', 'team_leader'];
  const changed = role !== u.role || (role !== 'admin' && role !== 'super_admin' && teamId !== u.teamId);
  return <Dialog title={`Manage ${u.name}`} description={`${ROLE_LABEL[u.role]} · ${u.email}`} onClose={onClose} footer={<>
    <Button variant={u.active ? 'danger' : 'secondary'} className="mr-auto" onClick={() => ask({ title: `${u.active ? 'Deactivate' : 'Reactivate'} ${u.name}?`, description: u.active ? 'They can no longer sign in. Their records stay in their team; waiting leads are re-run without them.' : 'They can sign in again.', action: u.active ? 'Deactivate' : 'Reactivate', danger: u.active, done: `${u.name} ${u.active ? 'deactivated' : 'reactivated'}`, run: (x) => api.updateUser(u.id, { active: !u.active }, x) })}>{u.active ? 'Deactivate' : 'Reactivate'}</Button>
    <Button variant="quiet" onClick={() => ask({ title: `Send ${u.name} a password reset?`, description: 'They get an email with a link to choose a new password.', action: 'Send', done: 'Password reset sent', run: (x) => api.resetPassword(u.id, x) })}>Send password reset</Button>
    <Button variant="quiet" onClick={onClose}>Cancel</Button>
    <Button variant="primary" disabled={!changed} onClick={() => ask({ title: `Change ${u.name}?`, description: `${ROLE_LABEL[u.role]} → ${ROLE_LABEL[role]}${role === 'admin' || role === 'super_admin' ? '' : ' · ' + (teams.find((t) => t.value === teamId)?.label ?? '')}. Their open deals move with them.`, done: 'Saved', run: (x) => api.updateUser(u.id, { role, teamId: role === 'admin' || role === 'super_admin' ? null : teamId }, x) })}>Save changes</Button></>}>
    <SelectField label="Role" value={role} onChange={(e) => setRole(e.target.value as Role)} options={roles.map((r) => ({ value: r, label: ROLE_LABEL[r] }))} hint={me?.role !== 'super_admin' ? 'Only a Super admin can make someone an Admin.' : undefined} />
    {(role === 'user' || role === 'team_leader') && <SelectField label="Team" value={teamId} onChange={(e) => setTeamId(e.target.value)} options={teams} />}
    {element}
  </Dialog>;
}
