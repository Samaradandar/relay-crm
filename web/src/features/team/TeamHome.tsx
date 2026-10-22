import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError, type ApprovalRequest, type Lead, type VerifyItem } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { Avatar, TeamChip } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Banner, Tabs, useToast } from '@/components/ui/Feedback';
import { Icon } from '@/components/ui/Icon';
import { formatRelativeIST, todayIST } from '@/lib/format';
import { AssignDialog } from '@/features/pool/AssignDialog';
import { PoolTable } from '@/features/pool/PoolTable';
import { TransferList } from '@/features/pool/Distribution';
import { Hero, greeting } from '@/features/home/Hero';
import { ReasonDialog, RepCard, TeamStrip } from './TeamBits';

type Tab = 'assign' | 'verify' | 'approve' | 'proof' | 'transfers';

/** Team Leader home from Phase 3: everything waiting on you in one inbox, then the team and each rep. */
export function TeamHome() {
  const { me } = useAuth(); const nav = useNavigate();
  const n = useQuery({ queryKey: ['needsYou'], queryFn: api.needsYou });
  const o = useQuery({ queryKey: ['teamOverview'], queryFn: api.teamOverview });
  const teams = useQuery({ queryKey: ['teams'], queryFn: api.teams });
  const counts = { assign: n.data?.assign.length ?? 0, verify: n.data?.verify.length ?? 0, approve: n.data?.approvals.length ?? 0, proof: n.data?.proofMissing.length ?? 0, transfers: n.data?.transfers.length ?? 0 };
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const first = (Object.keys(counts) as Tab[]).find((k) => counts[k] > 0) ?? 'assign';
  const [tab, setTab] = useState<Tab | null>(null); const active = tab ?? first;
  const [assigning, setAssigning] = useState<Lead | null>(null);
  if (!me) return null;

  return <div className="flex flex-col gap-5">
    <Hero title={`${greeting()}, ${me.name.split(' ')[0]}`} meta={<>{me.team && <TeamChip name={me.team.name} color={me.team.color} />}<span>{total ? `${total} thing${total > 1 ? 's' : ''} need you` : 'Nothing waiting on you'}</span></>}
      actions={<><Button icon="today" onClick={() => nav('/app/my-today')}>My Today</Button><Button variant="primary" icon="plus" onClick={() => nav('/app/leads/new')}>Add new lead</Button></>} />

    <section className="panel flex flex-col gap-0 overflow-hidden" aria-labelledby="ny-h">
      <div className="flex flex-wrap items-center justify-between gap-2 px-5 pt-4"><h2 id="ny-h" className="m-0 flex items-center gap-2 text-heading">Needs you {total > 0 && <span className="min-w-5 rounded-pill bg-accent px-1.5 text-center text-[11px] font-semibold leading-[18px] text-accent-on">{total}</span>}</h2></div>
      <div className="px-5"><Tabs<Tab> items={[{ id: 'assign', label: 'Assign', count: counts.assign, accent: counts.assign > 0 }, { id: 'verify', label: 'Verify ticks', count: counts.verify, accent: counts.verify > 0 }, { id: 'approve', label: 'Approve', count: counts.approve, accent: counts.approve > 0 }, { id: 'proof', label: 'Proof missing', count: counts.proof, accent: counts.proof > 0 }, { id: 'transfers', label: 'Transfers', count: counts.transfers }]} active={active} onChange={setTab} /></div>
      <div className="p-5 pt-4">
        {!n.data ? <div className="h-32 animate-pulse rounded-md bg-surface-2" /> :
          active === 'assign' ? <PoolTable leads={n.data.assign} teams={teams.data ?? []} onAssign={setAssigning} empty="Every lead in your team’s share of the Pool has an owner." />
          : active === 'verify' ? <VerifyList items={n.data.verify} />
          : active === 'approve' ? <ApproveList items={n.data.approvals} />
          : active === 'proof' ? <ProofMissingList items={n.data.proofMissing} />
          : <TransferList items={n.data.transfers} myTeamId={me.teamId} myId={me.id} teams={teams.data ?? []} />}
      </div>
    </section>

    {o.data && <TeamStrip o={o.data} />}
    <section aria-labelledby="reps-h" className="flex flex-col gap-3">
      <div className="flex items-end justify-between gap-2"><h2 id="reps-h" className="m-0 text-heading">Your reps</h2><Link to="/app/team" className="text-[13px] font-semibold text-brand-ink no-underline hover:underline">My team →</Link></div>
      <div className="grid gap-4 md:grid-cols-2 2xl:grid-cols-3">{(o.data?.reps ?? []).map((r) => <RepCard key={r.id} r={r} />)}</div>
    </section>
    {assigning && <AssignDialog lead={assigning} onClose={() => setAssigning(null)} />}
  </div>;
}

const refreshKeys = ['needsYou', 'teamOverview', 'deals', 'deal', 'approvals', 'repProfile'];
function useRefresh() { const qc = useQueryClient(); return () => refreshKeys.forEach((k) => qc.invalidateQueries({ queryKey: [k] })); }
const Empty = ({ children }: { children: React.ReactNode }) => <div className="flex items-center gap-3 rounded-lg border border-dashed border-line p-5 text-ink-2"><span className="grid h-8 w-8 place-items-center rounded-full bg-success-soft text-success"><Icon name="check" /></span>{children}</div>;

/** PRD 10 verification: Verified, or Rejected with a reason (the rep is told; past stages get flagged). */
function VerifyList({ items }: { items: VerifyItem[] }) {
  const toast = useToast(); const refresh = useRefresh(); const [rejecting, setRejecting] = useState<VerifyItem | null>(null);
  const m = useMutation({ mutationFn: (v: { i: VerifyItem; d: 'verified' | 'rejected'; reason?: string }) => api.verifyTick(v.i.dealId, v.i.criterionKey, v.d, v.reason), onSuccess: (_x, v) => { refresh(); setRejecting(null); toast({ message: v.d === 'verified' ? `Verified: ${v.i.label}` : `Rejected. ${v.i.ownerName.split(' ')[0]} has been told.` }); } });
  if (!items.length) return <Empty>No ticks waiting for review.</Empty>;
  return <>
    <ul className="m-0 flex list-none flex-col divide-y divide-line rounded-lg border border-line p-0">{items.map((i) => <li key={i.dealId + i.criterionKey} className="flex flex-wrap items-center gap-3 px-3 py-3">
      <Avatar name={i.ownerName} size={28} />
      <span className="flex min-w-[260px] flex-1 flex-col"><span className="text-[14px]"><b>{i.ownerName.split(' ')[0]}</b> ticked “{i.label}” on <Link to={`/app/deals/${i.dealId}`} className="font-semibold text-ink no-underline hover:text-brand-ink">{i.company}</Link></span>
        <span className="flex flex-wrap items-center gap-1.5 text-small text-ink-2"><Icon name="clip" size={14} />{i.proof}<span className="text-ink-3">· {i.stageLabel}{i.pastStage ? ' (deal has moved on)' : ''}{i.at ? ` · ${formatRelativeIST(i.at)}` : ''}</span></span></span>
      <span className="flex gap-2"><Button size="sm" variant="quiet" className="!text-danger" icon="x" onClick={() => setRejecting(i)}>Reject</Button><Button size="sm" icon="check" disabled={m.isPending} onClick={() => m.mutate({ i, d: 'verified' })}>Verify</Button></span>
    </li>)}</ul>
    {rejecting && <ReasonDialog title={`Reject “${rejecting.label}”?`} description={`The tick is removed and ${rejecting.ownerName} is notified.${rejecting.pastStage ? ' The deal has already moved past this stage, so it will be flagged for review.' : ''}`} action="Reject tick" danger busy={m.isPending} onClose={() => setRejecting(null)} onConfirm={(reason) => m.mutate({ i: rejecting, d: 'rejected', reason })} />}
  </>;
}

/** Team-level requests from your own reps (PRD 4.5). A reason is required either way. */
function ApproveList({ items }: { items: ApprovalRequest[] }) {
  const toast = useToast(); const refresh = useRefresh(); const [deciding, setDeciding] = useState<{ a: ApprovalRequest; d: 'approved' | 'rejected' } | null>(null); const [err, setErr] = useState<string | null>(null);
  const m = useMutation({ mutationFn: (v: { id: string; d: 'approved' | 'rejected'; reason: string }) => api.decide(v.id, v.d, v.reason), onSuccess: (a) => { refresh(); setDeciding(null); toast({ message: `${a.status === 'approved' ? 'Approved' : 'Rejected'}: ${a.title}` }); }, onError: (e) => setErr(e instanceof ApiError ? e.message : 'Could not save.') });
  if (!items.length) return <Empty>No requests from your reps.</Empty>;
  return <>
    <ul className="m-0 flex list-none flex-col gap-3 p-0">{items.map((a) => <li key={a.id} className="flex flex-col gap-2 rounded-lg border border-line p-4">
      <div className="flex flex-wrap items-start justify-between gap-2"><span className="flex flex-col"><span className="text-body-strong">{a.title}</span><span className="text-small text-ink-2">{a.summary}</span></span><span className="text-xs text-ink-3">{a.requestedByName} · {formatRelativeIST(a.requestedAt)}</span></div>
      <div className="flex flex-wrap gap-2">{a.changes.map((c) => <span key={c.label} className="rounded-md bg-surface-2 px-2.5 py-1 text-small"><span className="text-ink-3">{c.label}: </span>{c.from && <s className="text-ink-3">{c.from}</s>} → <b>{c.to}</b></span>)}</div>
      <p className="m-0 rounded-md bg-surface-0 px-3 py-2 text-small text-ink-2">“{a.reason}”</p>
      <div className="flex justify-end gap-2"><Button size="sm" variant="quiet" className="!text-danger" onClick={() => setDeciding({ a, d: 'rejected' })}>Reject</Button><Button size="sm" variant="primary" icon="check" onClick={() => setDeciding({ a, d: 'approved' })}>Approve</Button></div>
    </li>)}</ul>
    {err && <Banner tone="danger" title={err} />}
    {deciding && <ReasonDialog title={`${deciding.d === 'approved' ? 'Approve' : 'Reject'}: ${deciding.a.title}`} description={deciding.a.effect} action={deciding.d === 'approved' ? 'Approve' : 'Reject'} danger={deciding.d === 'rejected'} busy={m.isPending} onClose={() => setDeciding(null)} onConfirm={(reason) => m.mutate({ id: deciding.a.id, d: deciding.d, reason })} />}
  </>;
}

/** Proof removed after a tick: the tick stays, the deal shows a warning, the TL is told (PRD 10). */
function ProofMissingList({ items }: { items: VerifyItem[] }) {
  const toast = useToast(); const refresh = useRefresh();
  const m = useMutation({ mutationFn: (i: VerifyItem) => { const due = new Date(Date.now() + 86_400_000).toISOString().slice(0, 10); return api.assignTask(i.dealId, { text: `Restore the proof for “${i.label}”`, due: due < todayIST() ? todayIST() : due }); }, onSuccess: (_x, i) => { refresh(); toast({ message: `Task sent to ${i.ownerName.split(' ')[0]}` }); } });
  if (!items.length) return <Empty>No proof missing on your team’s deals.</Empty>;
  return <ul className="m-0 flex list-none flex-col divide-y divide-line rounded-lg border border-line p-0">{items.map((i) => <li key={i.dealId + i.criterionKey} className="flex flex-wrap items-center gap-3 px-3 py-3">
    <span className="grid h-7 w-7 place-items-center rounded-full bg-danger-soft text-danger"><Icon name="alert" size={14} /></span>
    <span className="flex min-w-[260px] flex-1 flex-col"><span className="text-[14px]"><Link to={`/app/deals/${i.dealId}`} className="font-semibold text-ink no-underline hover:text-brand-ink">{i.company}</Link> · “{i.label}” ({i.stageLabel})</span><span className="text-small text-danger">{i.proof} · owner {i.ownerName}</span></span>
    <Button size="sm" icon="send" loading={m.isPending} onClick={() => m.mutate(i)}>Ask {i.ownerName.split(' ')[0]} to restore it</Button>
  </li>)}</ul>;
}
