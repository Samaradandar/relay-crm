import clsx from 'clsx';
import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError, type ApprovalRequest } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { can, reasonFor } from '@/auth/permissions';
import { PageHeader } from '@/components/layout/PageHeader';
import { Banner, ReasonBox, Tabs, useToast } from '@/components/ui/Feedback';
import { ApprovalBadge, Avatar } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { formatDateTimeIST, formatRelativeIST } from '@/lib/format';
import { ENV } from '@/config/env';
import { PoolSummaryStrip } from '@/features/home/PoolSummaryStrip';

type Tab = 'pending' | 'decided' | 'mine';

export function ApprovalsPage() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const toast = useToast();
  const deciders = !!can(me, 'approval.decide') && (me?.role === 'admin' || me?.role === 'super_admin');
  const [tab, setTab] = useState<Tab>(deciders ? 'pending' : 'mine');
  const [sel, setSel] = useState<string | null>(null);
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const { data = [], isLoading } = useQuery({ queryKey: ['approvals'], queryFn: api.approvals });

  const lists = useMemo(() => ({
    pending: data.filter((a) => a.status === 'pending').sort((a, b) => a.requestedAt.localeCompare(b.requestedAt)),
    decided: data.filter((a) => a.status !== 'pending'),
    mine: data.filter((a) => a.requestedBy === me?.id || (me?.role === 'team_leader' && !!me.teamId && !!a.teamIds?.includes(me.teamId)))
  }), [data, me]);
  const list = lists[tab];
  const current = list.find((a) => a.id === sel) ?? list[0];
  const policy = reasonFor(me, 'approval.decide');

  const decide = useMutation({
    mutationFn: ({ id, d }: { id: string; d: 'approved' | 'rejected' }) => api.decide(id, d, reason),
    onSuccess: (a) => { setReason(''); setError(null); qc.invalidateQueries({ queryKey: ['approvals'] }); toast({ message: `${a.status === 'approved' ? 'Approved' : 'Rejected'}: ${a.title}` }); },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not save the decision.')
  });

  const tabs = [
    ...(deciders ? [{ id: 'pending' as Tab, label: 'Pending', count: lists.pending.length, accent: lists.pending.length > 0 }, { id: 'decided' as Tab, label: 'Decided' }] : []),
    { id: 'mine' as Tab, label: 'My requests', count: lists.mine.length }
  ];

  return <>
    {deciders && ENV.releasePhase >= 2 && <PoolSummaryStrip />}
    <PageHeader title="Approvals" subtitle="Requests never expire. Approvers get a reminder at 09:30 IST each day until they answer." />
    <Tabs items={tabs} active={tab} onChange={(t) => { setTab(t); setSel(null); setError(null); }} />
    <div className="mt-5 flex flex-wrap items-start gap-5">
      <section aria-label="Requests" className="panel min-w-0 flex-[1_1_360px] overflow-hidden">
        {isLoading && <p className="p-4 text-ink-3">Loading…</p>}
        {!isLoading && list.length === 0 && <p className="p-8 text-center text-ink-2">{tab === 'pending' ? 'Nothing is waiting for you.' : 'No requests here.'}</p>}
        {list.map((a) => (
          <button key={a.id} onClick={() => { setSel(a.id); setError(null); }} className={clsx('flex w-full flex-col gap-1.5 border-t border-line px-4 py-3.5 text-left first:border-t-0 hover:bg-surface-3', current?.id === a.id && 'bg-brand-soft')}>
            <span className="flex items-center justify-between gap-2"><span className="text-body-strong">{a.title}</span><ApprovalBadge status={a.status} /></span>
            <span className="text-small text-ink-2">{a.summary}</span>
            <span className="text-caption text-ink-3">{a.requestedByName} · {formatRelativeIST(a.requestedAt)}</span>
          </button>
        ))}
      </section>
      {current && <Detail a={current} canDecide={tab === 'pending' && deciders} policy={policy} reason={reason} setReason={setReason} error={error}
        onDecide={(d) => { if (policy === 'required' && !reason.trim()) { setError('Add a reason before you decide.'); return; } decide.mutate({ id: current.id, d }); }} busy={decide.isPending} selfRequest={current.requestedBy === me?.id && me?.role !== 'super_admin'} />}
    </div>
  </>;
}

function Detail({ a, canDecide, policy, reason, setReason, error, onDecide, busy, selfRequest }: { a: ApprovalRequest; canDecide: boolean; policy: 'required' | 'optional' | 'none'; reason: string; setReason: (s: string) => void; error: string | null; onDecide: (d: 'approved' | 'rejected') => void; busy: boolean; selfRequest: boolean }) {
  return (
    <section aria-label="Request detail" className="panel flex min-w-0 flex-[1.4_1_440px] flex-col gap-5 p-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div><p className="m-0 text-overline text-ink-3">{a.type === 'user.create' ? 'New user request' : a.type === 'user.change_role' ? 'Role change request' : a.type}</p><h2 className="m-0 text-title">{a.title}</h2></div>
        <ApprovalBadge status={a.status} />
      </div>
      <dl className="m-0 grid grid-cols-[160px_minmax(0,1fr)] gap-x-4 gap-y-2.5">
        <dt className="text-ink-2">Requested by</dt><dd className="m-0 flex items-center gap-2"><Avatar name={a.requestedByName} size={22} />{a.requestedByName}</dd>
        <dt className="text-ink-2">Raised</dt><dd className="m-0">{formatDateTimeIST(a.requestedAt)}</dd>
        <dt className="text-ink-2">If approved</dt><dd className="m-0">{a.effect}</dd>
      </dl>
      <div className="flex flex-col gap-2">
        <span className="text-[13px] font-semibold">Change requested</span>
        <div className="overflow-hidden rounded-md border border-line">
          {a.changes.map((c) => <div key={c.label} className="grid grid-cols-[140px_minmax(0,1fr)_minmax(0,1fr)] gap-3 border-t border-line px-3 py-2.5 text-[13px] first:border-t-0">
            <span className="text-ink-2">{c.label}</span><span className={clsx('text-ink-3', c.from && 'line-through')}>{c.from ?? '—'}</span><span className="font-semibold">{c.to}</span>
          </div>)}
        </div>
      </div>
      <div className="flex flex-col gap-1.5"><span className="text-[13px] font-semibold">Their reason</span><p className="m-0 rounded-sm bg-surface-2 px-3 py-2.5 text-ink-2">{a.reason}</p></div>
      {a.status !== 'pending' && a.decidedAt && <Banner tone={a.status === 'approved' ? 'success' : 'danger'} title={`${a.status === 'approved' ? 'Approved' : 'Rejected'} by ${(a.decidedByName ?? 'an approver')} · ${formatDateTimeIST(a.decidedAt)}`}>{a.decisionReason || 'No reason given.'}</Banner>}
      {canDecide && (selfRequest ? <Banner tone="notice" title="You raised this request">Another Admin or a Super admin has to decide it.</Banner> :
        <div className="flex flex-col gap-3 border-t border-line pt-4">
          <ReasonBox id="decision-reason" policy={policy} value={reason} onChange={setReason} label="Your reason" placeholder="e.g. Confirmed headcount with the sales head" />
          {error && <Banner tone="danger" title={error} />}
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="danger" disabled={busy} onClick={() => onDecide('rejected')}>Reject</Button>
            <Button variant="primary" icon="check" loading={busy} onClick={() => onDecide('approved')}>Approve</Button>
          </div>
        </div>)}
    </section>
  );
}
