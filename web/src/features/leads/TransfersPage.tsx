import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError, type Transfer, type TransferLookup } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Banner, useToast } from '@/components/ui/Feedback';
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { Dialog } from '@/components/ui/Overlay';
import { formatRelativeIST } from '@/lib/format';

const err = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
const TONE: Record<string, 'brand' | 'success' | 'warning' | 'danger' | 'neutral'> = { open: 'warning', approved: 'success', rejected: 'danger', cancelled: 'neutral' };

/** A Team Leader asks to give a lead to another team, or to take one from another team. An Admin
 * decides in Approvals. The other Team Leader is told and can leave a note for the Admin. */
export function TransfersPage() {
  const { me } = useAuth(); const qc = useQueryClient(); const toast = useToast();
  const q = useQuery({ queryKey: ['transfers'], queryFn: () => api.transfers() });
  const [asking, setAsking] = useState(false); const [note, setNote] = useState<Transfer | null>(null);
  const isTl = me?.role === 'team_leader';
  const done = (message: string) => { ['transfers', 'approvals'].forEach((k) => qc.invalidateQueries({ queryKey: [k] })); toast({ message }); };
  return <div className="flex flex-col gap-5">
    <PageHeader title="Transfers" subtitle="Moving a lead between teams. A Team Leader asks; an Admin decides in Approvals; the lead and its history move together."
      actions={isTl && <Button variant="primary" icon="plus" onClick={() => setAsking(true)}>Request a transfer</Button>} />
    {q.isError && <Banner tone="danger" title="The transfers could not be loaded">{err(q.error, '')}</Banner>}
    {q.data && q.data.length === 0 && <div className="panel p-6 text-ink-2">No transfer requests yet.</div>}
    <ul className="m-0 flex list-none flex-col gap-3 p-0">
      {(q.data ?? []).map((t) => <li key={t.id} className="panel flex flex-col gap-2 p-4">
        <div className="flex flex-wrap items-center gap-2"><Link to={`/app/deals/${t.dealId}`} className="text-heading text-ink no-underline hover:text-brand-ink">{t.company ?? 'A lead'}</Link><Badge tone={TONE[t.status] ?? 'neutral'}>{t.status}</Badge><Badge>{t.direction === 'give' ? 'Give' : 'Take'}</Badge><span className="text-xs text-ink-3">{formatRelativeIST(t.createdAt)}</span></div>
        <p className="m-0 text-small text-ink-2">{t.fromTeam ?? '—'} → {t.toTeam ?? '—'}{t.proposedOwner ? ` · to ${t.proposedOwner}` : ''} · asked by {t.requestedBy}</p>
        <p className="m-0 text-small">{t.reason}</p>
        {t.otherTlComment && <p className="m-0 rounded-md bg-surface-2 p-2 text-small text-ink-2">The other Team Leader says: {t.otherTlComment}</p>}
        {isTl && t.status === 'open' && <span><Button size="sm" variant="quiet" onClick={() => setNote(t)}>Add a note for the Admin</Button></span>}
      </li>)}
    </ul>
    {asking && <RequestDialog onClose={() => setAsking(false)} onDone={() => { setAsking(false); done('Request sent to an Admin'); }} />}
    {note && <NoteDialog t={note} onClose={() => setNote(null)} onDone={() => { setNote(null); done('Note saved'); }} />}
  </div>;
}

function RequestDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const { me } = useAuth();
  const teams = useQuery({ queryKey: ['teams'], queryFn: api.teams }); const users = useQuery({ queryKey: ['users'], queryFn: api.users }); const deals = useQuery({ queryKey: ['deals'], queryFn: api.deals });
  const [dir, setDir] = useState<'give' | 'take'>('give'); const [deal, setDeal] = useState(''); const [toTeam, setToTeam] = useState(''); const [owner, setOwner] = useState('');
  const [find, setFind] = useState(''); const [reason, setReason] = useState(''); const [error, setError] = useState<string | null>(null);
  const lookup = useQuery({ queryKey: ['transferLookup', find], queryFn: () => api.transferLookup(find), enabled: dir === 'take' && find.trim().length >= 2 });
  const mine = (deals.data ?? []).filter((d) => (d.status === 'open' || d.status === 'pooled') && d.teamId === me?.teamId);
  const reps = (users.data ?? []).filter((u) => u.role === 'user' && u.active && u.teamId === me?.teamId);
  const send = useMutation({ mutationFn: () => api.requestTransfer({ direction: dir, dealId: deal, toTeamId: dir === 'give' ? toTeam : undefined, ownerId: dir === 'take' && owner ? owner : undefined, reason }), onSuccess: onDone, onError: (e) => setError(err(e, 'Could not send the request.')) });
  const ready = !!deal && reason.trim().length >= 3 && (dir === 'give' ? !!toTeam : true);
  return <Dialog title="Request a transfer" description="An Admin decides. Until then the lead stays where it is." onClose={onClose}
    footer={<><Button variant="quiet" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!ready} loading={send.isPending} onClick={() => { setError(null); send.mutate(); }}>Send the request</Button></>}>
    <div className="flex flex-col gap-3">
      <SelectField label="What do you want?" value={dir} onChange={(e) => { setDir(e.target.value as 'give' | 'take'); setDeal(''); }} options={[{ value: 'give', label: 'Give one of my team’s leads to another team' }, { value: 'take', label: 'Take a lead from another team' }]} />
      {dir === 'give' ? <>
        <SelectField label="Which lead" required value={deal} onChange={(e) => setDeal(e.target.value)} options={[{ value: '', label: 'Choose…' }, ...mine.map((d) => ({ value: d.id, label: `${d.company}${d.ownerName ? ` · ${d.ownerName}` : ''}` }))]} />
        <SelectField label="Give it to" required value={toTeam} onChange={(e) => setToTeam(e.target.value)} options={[{ value: '', label: 'Choose a team…' }, ...(teams.data ?? []).filter((t) => t.id !== me?.teamId).map((t) => ({ value: t.id, label: t.name }))]} />
      </> : <>
        <TextField label="Find the lead" required value={find} onChange={(e) => { setFind(e.target.value); setDeal(''); }} placeholder="Start typing the company name" hint="You only see the company and its team." />
        {find.trim().length >= 2 && <ul className="m-0 flex list-none flex-col gap-1 p-0">{(lookup.data ?? []).map((r: TransferLookup) => <li key={r.dealId}><button type="button" onClick={() => setDeal(r.dealId)} aria-pressed={deal === r.dealId} className={'w-full rounded-md border px-3 py-2 text-left text-small ' + (deal === r.dealId ? 'border-brand bg-brand-soft' : 'border-line-control hover:bg-surface-3')}><b>{r.company}</b>{r.teamName ? ` · ${r.teamName}` : ''}</button></li>)}{lookup.data?.length === 0 && <li className="text-small text-ink-3">No match in other teams.</li>}</ul>}
        <SelectField label="Who in your team should get it" optionalNote="(optional)" value={owner} onChange={(e) => setOwner(e.target.value)} options={[{ value: '', label: 'Let the assignment rules decide' }, ...reps.map((u) => ({ value: u.id, label: u.name }))]} />
      </>}
      <TextAreaField label="Why" required rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. The customer is in Kochi, our team covers the south" />
      {error && <Banner tone="danger" title={error} />}
    </div>
  </Dialog>;
}

function NoteDialog({ t, onClose, onDone }: { t: Transfer; onClose: () => void; onDone: () => void }) {
  const [text, setText] = useState(''); const [error, setError] = useState<string | null>(null);
  const save = useMutation({ mutationFn: () => api.commentOnTransfer(t.id, text.trim()), onSuccess: onDone, onError: (e) => setError(err(e, 'Could not save the note.')) });
  return <Dialog title={`A note on ${t.company ?? 'this transfer'}`} description="The Admin who decides will see it. Only the other team's Team Leader can add one." onClose={onClose}
    footer={<><Button variant="quiet" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!text.trim()} loading={save.isPending} onClick={() => { setError(null); save.mutate(); }}>Save the note</Button></>}>
    <div className="flex flex-col gap-3"><TextAreaField label="Note" required rows={3} value={text} onChange={(e) => setText(e.target.value)} />{error && <Banner tone="danger" title={error} />}</div>
  </Dialog>;
}
