import clsx from 'clsx';
import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError, type Lead } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { Dialog, CapMeter } from '@/components/ui/Overlay';
import { Button } from '@/components/ui/Button';
import { Banner, ReasonBox, useToast } from '@/components/ui/Feedback';
import { Avatar } from '@/components/ui/Badge';
import { formatRelativeIST } from '@/lib/format';

/**
 * Manual assign from the Pool (P2-06). The rep list is limited to the person's scope and shows
 * each rep's active leads against their cap. Going over a cap is allowed, with a warning, and recorded.
 */
export function AssignDialog({ lead, onClose }: { lead: Lead; onClose: () => void }) {
  const { me } = useAuth(); const qc = useQueryClient(); const toast = useToast();
  const reps = useQuery({ queryKey: ['teamLoad'], queryFn: api.teamLoad });
  const teams = useQuery({ queryKey: ['teams'], queryFn: api.teams });
  const [pick, setPick] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const policy = me?.role === 'super_admin' ? 'optional' : 'required';

  // TLs pick from their own team (plus themselves -- a Team Leader may keep a lead for their own
  // pipeline); Admins see the routed team first, then everyone else.
  const self = me?.role === 'team_leader'
    ? [{ id: me.id, name: me.name, teamId: me.teamId, active: true, cap: Infinity, activeLeads: 0, assignedThisWeek: 0, lastAssignedAt: null }]
    : [];
  const list = [...self, ...(reps.data ?? []).filter((r) => r.active && (me?.role !== 'team_leader' || r.teamId === me.teamId))]
    .sort((a, b) => Number(b.teamId === lead.teamId) - Number(a.teamId === lead.teamId) || (a.activeLeads / a.cap) - (b.activeLeads / b.cap));
  const chosen = list.find((r) => r.id === pick);
  const overCap = chosen ? chosen.activeLeads >= chosen.cap : false;

  const invalidate = () => ['pool', 'teamLoad', 'recentAssignments', 'poolSummary', 'myLeads'].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));

  const save = useMutation({
    mutationFn: () => api.assignFromPool(lead.id, pick!, reason),
    onSuccess: (l) => { invalidate(); toast({ message: `${l.company} assigned to ${l.ownerName}` }); onClose(); },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not assign the lead.')
  });
  const giveToCreator = useMutation({
    mutationFn: () => api.decidePoolLead(lead.id, 'give_creator', undefined, reason),
    onSuccess: () => { invalidate(); toast({ message: `${lead.company} given back to ${lead.generatedByName}` }); onClose(); },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not give it back to them.')
  });
  const roundRobinNow = useMutation({
    mutationFn: () => api.decidePoolLead(lead.id, 'auto', undefined, reason),
    onSuccess: () => { invalidate(); toast({ message: `${lead.company} sent to round robin` }); onClose(); },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not send it to round robin.')
  });
  const teamName = (id: string | null) => teams.data?.find((t) => t.id === id)?.name ?? '';

  return <Dialog title={`Assign ${lead.company}`} description={`${lead.source} · added by ${lead.generatedByName} · waiting since ${formatRelativeIST(lead.pooledAt)}`} onClose={onClose} wide
    footer={<><Button variant="quiet" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!pick || (policy === 'required' && !reason.trim())} loading={save.isPending} onClick={() => save.mutate()}>{chosen ? `Assign to ${chosen.name.split(' ')[0]}` : 'Assign'}</Button></>}>
    {lead.repHold && <div className="mb-3 flex flex-wrap gap-2 rounded-lg border border-line bg-surface-2 p-3">
      {lead.creatorAssignable && <Button variant="secondary" loading={giveToCreator.isPending} onClick={() => giveToCreator.mutate()}>Give back to {lead.generatedByName.split(' ')[0]}</Button>}
      <Button variant="secondary" loading={roundRobinNow.isPending} onClick={() => roundRobinNow.mutate()}>Round robin now</Button>
      <span className="self-center text-xs text-ink-3">{lead.autoAssignAt ? `Otherwise falls back to round robin ${formatRelativeIST(lead.autoAssignAt)}` : 'Otherwise, this falls back to round robin automatically'}</span>
    </div>}
    <fieldset className="m-0 flex flex-col gap-1.5 border-0 p-0">
      <legend className="mb-1.5 text-[13px] font-semibold">Choose a rep<span aria-hidden="true" className="ml-0.5 text-danger">*</span></legend>
      <div className="flex max-h-[280px] flex-col overflow-y-auto rounded-lg border border-line">
        {list.map((r) => <label key={r.id} className={clsx('grid cursor-pointer grid-cols-[18px_28px_minmax(0,1fr)_170px] items-center gap-3 border-t border-line px-3 py-2.5 first:border-t-0', pick === r.id ? 'bg-brand-soft' : 'hover:bg-surface-3')}>
          <input type="radio" name="rep" value={r.id} checked={pick === r.id} onChange={() => setPick(r.id)} className="accent-[var(--brand)]" />
          <Avatar name={r.name} size={28} />
          <span className="flex min-w-0 flex-col"><span className="text-body-strong">{r.name}</span><span className="text-xs text-ink-3">{me?.role !== 'team_leader' && `${teamName(r.teamId)} · `}{r.assignedThisWeek} this week{r.lastAssignedAt ? ` · last ${formatRelativeIST(r.lastAssignedAt)}` : ''}</span></span>
          <CapMeter active={r.activeLeads} cap={r.cap} compact />
        </label>)}
        {list.length === 0 && <p className="m-0 p-6 text-center text-ink-2">No active sales reps you can assign to.</p>}
      </div>
    </fieldset>
    {overCap && chosen && <Banner tone="warning" title={`${chosen.name} is at their cap (${chosen.activeLeads}/${chosen.cap})`}>You can still assign. It will be recorded as an over-cap assignment.</Banner>}
    <ReasonBox id="assign-reason" policy={policy} value={reason} onChange={setReason} placeholder="e.g. Priya already knows the owner; F&B group is full this week" />
    {error && <Banner tone="danger" title={error} />}
  </Dialog>;
}
