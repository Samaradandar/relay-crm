import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import type { IncentiveRow } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { useReasonPrompt } from '@/components/ui/ReasonPrompt';
import { formatDateTimeIST, formatINR } from '@/lib/format';

type Tone = 'success' | 'warning' | 'danger' | 'brand' | 'neutral';
/** Where an incentive stands, in plain words. */
function statusOf(r: IncentiveRow): { label: string; tone: Tone; note?: string } {
  const s = r.incentive?.status;
  if (s === 'earned') return { label: 'Earned', tone: 'success', note: 'The customer has paid in full, so the incentive is provided.' };
  if (s === 'approved') return { label: 'Approved', tone: 'success', note: 'Provided as the customer’s payments come in.' };
  if (s === 'approved_pending_closure') return { label: 'Approved pledge', tone: 'brand', note: 'Applies when the deal is won.' };
  if (s === 'pending_approval' || r.request?.status === 'pending') return { label: 'Waiting for approval', tone: 'warning', note: 'An Admin or Super admin has to approve it first.' };
  if (r.request?.status === 'rejected') return { label: 'Rejected', tone: 'danger', note: r.request.decisionReason ? `Reason: ${r.request.decisionReason}` : undefined };
  if (!r.won) return { label: 'Deal still open', tone: 'neutral' };
  return r.onboardingDone ? { label: 'Waiting for the Team Leader', tone: 'warning', note: 'Onboarding is done: the Team Leader decides the incentive.' } : { label: 'Onboarding in progress', tone: 'neutral', note: 'The Team Leader sets the incentive once onboarding is done.' };
}

const pct = (bp: number | null | undefined) => (bp == null ? '—' : `${bp / 100}%`);

/** PRD: the Team Leader decides the incentive, an Admin or Super admin approves it, and only then is it provided to the rep. */
export function IncentivesPage() {
  const { me } = useAuth(); const qc = useQueryClient();
  const q = useQuery({ queryKey: ['incentives'], queryFn: api.incentives });
  const { ask, element } = useReasonPrompt(() => ['incentives', 'approvals', 'notifications', 'myIncentives', 'missingRates'].forEach((k) => qc.invalidateQueries({ queryKey: [k] })));
  const [draft, setDraft] = useState<Record<string, string>>({});
  const role = me?.role;
  const rows = q.data ?? [];
  const isLead = role === 'team_leader'; const isAdmin = role === 'admin' || role === 'super_admin';
  const toApprove = rows.filter((r) => isAdmin && r.approvalId);
  const toSet = rows.filter((r) => isLead && r.won && r.onboardingDone && !r.request && (!r.incentive || r.incentive.status === 'no_rate'));
  const rest = rows.filter((r) => !toApprove.includes(r) && !toSet.includes(r));
  const sum = (f: (r: IncentiveRow) => number) => rows.reduce((t, r) => t + f(r), 0);

  const request = (r: IncentiveRow) => {
    const v = Number(draft[r.dealId]);
    ask({
      title: `Ask for ${v}% incentive on ${r.company}`,
      description: `${formatINR(Math.round(r.dealValuePaise * v / 100))} for ${r.rep.name ?? 'the rep'}. It goes to an Admin or Super admin for approval and is only provided after that.`,
      action: 'Send for approval', done: 'Sent for approval.',
      run: (reason) => api.requestIncentive(r.dealId, Math.round(v * 100), reason),
    });
  };
  const decide = (r: IncentiveRow, decision: 'approved' | 'rejected') => ask({
    title: `${decision === 'approved' ? 'Approve' : 'Reject'} the incentive on ${r.company}`,
    description: `${pct(r.request?.rateBp)} of ${formatINR(r.dealValuePaise)}, requested by ${r.request?.requestedBy ?? 'the Team Leader'}.`,
    action: decision === 'approved' ? 'Approve' : 'Reject', danger: decision === 'rejected',
    done: decision === 'approved' ? 'Approved. The rep has been told.' : 'Rejected.',
    run: (reason) => api.decide(r.approvalId!, decision, reason),
  });

  return <div className="flex flex-col gap-5">
    <PageHeader title="Incentives" subtitle={isAdmin ? 'Approve the incentives your Team Leaders propose. Nothing is provided before you do.' : isLead ? 'Once onboarding is done, decide the incentive for your rep. An Admin approves it.' : 'Your incentives. They are private to you.'} />
    {!q.data ? <div className="panel h-64 animate-pulse" /> : <>
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat label={isAdmin ? 'Waiting for your approval' : isLead ? 'Waiting for you' : 'Deals with an incentive'} value={String(isAdmin ? toApprove.length : isLead ? toSet.length : rows.filter((r) => r.incentive?.rateBp != null).length)} />
        <Stat label="Approved, still being earned" value={formatINR(sum((r) => r.incentive?.status === 'approved' ? r.incentive.pendingAmountPaise : 0))} />
        <Stat label="Earned and provided" value={formatINR(sum((r) => r.incentive?.earnedAmountPaise ?? 0))} />
      </div>

      {toApprove.length > 0 && <Section title="Waiting for your approval">
        {toApprove.map((r) => <Card key={r.dealId} r={r}>
          <p className="m-0 text-small text-ink-2">{r.request?.requestedBy ?? 'The Team Leader'} asks for <b className="text-ink">{pct(r.request?.rateBp)}</b> = <b className="text-ink">{formatINR(Math.round(r.dealValuePaise * (r.request?.rateBp ?? 0) / 10000))}</b>. “{r.request?.reason}”</p>
          <span className="flex gap-2"><Button size="sm" variant="primary" icon="check" onClick={() => decide(r, 'approved')}>Approve</Button><Button size="sm" variant="quiet" className="!text-danger" onClick={() => decide(r, 'rejected')}>Reject</Button></span>
        </Card>)}
      </Section>}

      {toSet.length > 0 && <Section title="Onboarding is done: set the incentive">
        {toSet.map((r) => <Card key={r.dealId} r={r}>
          <div className="flex flex-wrap items-center gap-2">
            <input type="number" min={0} max={100} step={0.5} placeholder="% of deal value" aria-label={`Incentive percent for ${r.company}`} className="h-9 w-36 rounded-md border border-line-control bg-surface-1 px-2 text-right text-sm"
              value={draft[r.dealId] ?? ''} onChange={(e) => setDraft((d) => ({ ...d, [r.dealId]: e.target.value }))} />
            {Number(draft[r.dealId]) > 0 && <span className="text-small text-ink-2">= {formatINR(Math.round(r.dealValuePaise * Number(draft[r.dealId]) / 100))}</span>}
            <Button size="sm" variant="primary" disabled={!(Number(draft[r.dealId]) > 0 && Number(draft[r.dealId]) <= 100)} onClick={() => request(r)}>Send for approval</Button>
          </div>
        </Card>)}
      </Section>}

      <Section title={isAdmin || isLead ? 'All incentives' : 'My incentives'}>
        {rest.length === 0 && <p className="panel m-0 p-5 text-small text-ink-2">{rows.length === 0 ? 'Nothing yet. A deal appears here once it is won.' : 'Nothing else.'}</p>}
        {rest.map((r) => { const st = statusOf(r); return <Card key={r.dealId} r={r} status={st}>
          <div className="grid gap-x-6 gap-y-1 text-small sm:grid-cols-4">
            <Fact label="Deal value" value={formatINR(r.dealValuePaise)} />
            <Fact label="Incentive" value={pct(r.incentive?.rateBp)} />
            <Fact label="Total if paid in full" value={r.incentive?.expectedTotalPaise != null ? formatINR(r.incentive.expectedTotalPaise) : '—'} />
            <Fact label="Earned so far" value={r.incentive ? formatINR(r.incentive.status === 'earned' ? r.incentive.earnedAmountPaise : r.incentive.pendingAmountPaise) : '—'} />
          </div>
          {st.note && <p className="m-0 text-small text-ink-3">{st.note}</p>}
        </Card>; })}
      </Section>
    </>}
    {element}
  </div>;
}

function Stat({ label, value }: { label: string; value: string }) {
  return <div className="panel flex flex-col gap-1 p-4"><span className="text-overline text-ink-3">{label}</span><span className="text-title">{value}</span></div>;
}
function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="flex flex-col gap-3"><h2 className="m-0 text-heading">{title}</h2>{children}</section>;
}
function Fact({ label, value }: { label: string; value: string }) {
  return <span className="flex flex-col"><span className="text-xs text-ink-3">{label}</span><span className="text-body-strong">{value}</span></span>;
}
function Card({ r, status, children }: { r: IncentiveRow; status?: { label: string; tone: Tone }; children: React.ReactNode }) {
  return <article className="panel flex flex-col gap-3 p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="flex flex-col"><span className="text-heading">{r.company}</span><span className="text-small text-ink-2">{r.rep.name ?? ''}{r.wonAt ? ` · won ${formatDateTimeIST(r.wonAt).split(',')[0]}` : ''}</span></span>
      {status && <Badge tone={status.tone}>{status.label}</Badge>}
    </div>
    {children}
  </article>;
}
