/* Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta. */
import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import type { Subscription } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { Badge, type Tone } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Banner } from '@/components/ui/Feedback';
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { Dialog } from '@/components/ui/Overlay';
import { useReasonPrompt } from '@/components/ui/ReasonPrompt';
import { formatINR } from '@/lib/format';

const RENEWAL_TONE: Record<Subscription['renewalStatus'], [Tone, string]> = { upcoming: ['accent', 'Upcoming'], renewed: ['success', 'Renewed'], not_renewing: ['danger', 'Not renewing'] };
const CANCEL_TONE: Record<Subscription['cancellationStatus'], [Tone, string]> = { none: ['neutral', 'No cancellation'], requested: ['warning', 'Cancellation requested'], cancelled: ['danger', 'Cancelled'] };

/** Phase 5 P5-02/03/06: subscription detail, pending changes, renewal, cancellation, churn. */
export function SubscriptionPanel({ customerId }: { customerId: string }) {
  const { me } = useAuth(); const qc = useQueryClient();
  const sub = useQuery({ queryKey: ['subscription', customerId], queryFn: () => api.customerSubscription(customerId) });
  const changes = useQuery({ queryKey: ['subscriptionChanges', sub.data?.id], queryFn: () => api.subscriptionChanges(sub.data!.id), enabled: !!sub.data });
  const reasons = useQuery({ queryKey: ['churnReasons'], queryFn: api.churnReasons });
  const [churnOpen, setChurnOpen] = useState(false);
  const refresh = () => { qc.invalidateQueries({ queryKey: ['subscription', customerId] }); qc.invalidateQueries({ queryKey: ['subscriptionChanges'] }); qc.invalidateQueries({ queryKey: ['customers'] }); qc.invalidateQueries({ queryKey: ['atRiskCustomers'] }); qc.invalidateQueries({ queryKey: ['churnHistory'] }); };
  const { ask, element } = useReasonPrompt(refresh);

  const canConfirm = me?.role === 'team_leader' || me?.role === 'admin' || me?.role === 'super_admin';
  const canAct = me?.role === 'team_leader' || me?.role === 'admin' || me?.role === 'super_admin';
  const canChurn = me?.role === 'admin' || me?.role === 'super_admin' || canAct;

  if (sub.isError) return <Banner tone="info" title="No subscription on this account yet" />;
  if (!sub.data) return <div className="panel h-40 animate-pulse" />;
  const s = sub.data;
  const [rTone, rLabel] = RENEWAL_TONE[s.renewalStatus];
  const [cTone, cLabel] = CANCEL_TONE[s.cancellationStatus];
  const pending = (changes.data ?? []).filter((c) => c.status === 'pending');
  const churned = s.endedAt != null;

  return <div className="flex flex-col gap-5">
    {s.cancellationStatus === 'requested' && <Banner tone="warning" title="Cancellation requested">Admins and Super admins have been alerted.</Banner>}
    {churned && <Banner tone="danger" title="This subscription has ended">{s.endReason ?? 'No end reason recorded.'}</Banner>}

    <section className="panel flex flex-col gap-3 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3"><h2 className="m-0 text-heading">Plan</h2><div className="flex gap-2"><Badge tone={rTone}>{rLabel}</Badge><Badge tone={cTone}>{cLabel}</Badge></div></div>
      <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
        <Field label="Pricing model" value={s.pricingModel ?? '—'} />
        <Field label="Fee" value={s.feePaise != null ? `${formatINR(s.feePaise)} / ${s.billingFrequency}` : '—'} />
        <Field label="Contract end" value={s.contractEndDate ?? '—'} />
        <Field label="Renewal date" value={s.renewalDate ?? '—'} />
        <Field label="Start date" value={s.startDate ?? '—'} />
        <Field label="Version" value={String(s.version)} />
      </dl>
      {canAct && !churned && <div className="flex flex-wrap gap-2 border-t border-line pt-3">
        {s.renewalStatus !== 'renewed' && <Button size="sm" onClick={() => ask({ title: 'Record renewal outcome', action: 'Mark renewed', done: 'Renewal recorded', run: (reason) => api.recordRenewal(s.id, 'renewed', reason) })}>Mark renewed</Button>}
        {s.renewalStatus !== 'not_renewing' && <Button size="sm" variant="quiet" onClick={() => ask({ title: 'Record renewal outcome', action: 'Mark not renewing', done: 'Renewal recorded', run: (reason) => api.recordRenewal(s.id, 'not_renewing', reason) })}>Mark not renewing</Button>}
        {s.cancellationStatus === 'none' && <Button size="sm" variant="danger" onClick={() => ask({ title: 'Request cancellation', danger: true, action: 'Request cancellation', done: 'Cancellation requested', run: (reason) => api.recordCancellation(s.id, 'request', undefined, reason) })}>Request cancellation</Button>}
        {s.cancellationStatus === 'requested' && <>
          <Button size="sm" variant="danger" onClick={() => ask({ title: 'Cancel subscription', danger: true, action: 'Cancel', done: 'Subscription cancelled', run: (reason) => api.recordCancellation(s.id, 'cancel', s.contractEndDate ?? undefined, reason) })}>Cancel now</Button>
          <Button size="sm" variant="quiet" onClick={() => ask({ title: 'Withdraw cancellation', action: 'Withdraw', done: 'Cancellation withdrawn', run: (reason) => api.recordCancellation(s.id, 'revert', undefined, reason) })}>Changed their mind</Button>
        </>}
      </div>}
    </section>

    {pending.length > 0 && <section className="panel flex flex-col gap-3 p-5">
      <h2 className="m-0 text-heading">Pending changes</h2>
      {pending.map((c) => <div key={c.id} className="flex flex-wrap items-center justify-between gap-2 border-b border-line py-2 text-sm last:border-0">
        <span className="text-ink">{c.changeType} · effective {c.effectiveDate ?? '—'}</span>
        {canConfirm && <div className="flex gap-2">
          <Button size="sm" variant="primary" onClick={() => ask({ title: 'Confirm this change', action: 'Confirm', done: 'Change confirmed', run: (reason) => api.confirmSubscriptionChange(c.id, reason) })}>Confirm</Button>
          <Button size="sm" variant="quiet" onClick={() => ask({ title: 'Reject this change', action: 'Reject', done: 'Change rejected', run: (reason) => api.rejectSubscriptionChange(c.id, reason) })}>Reject</Button>
        </div>}
      </div>)}
    </section>}

    {canChurn && !churned && <section className="panel flex flex-col gap-3 p-5">
      <h2 className="m-0 text-heading">Churn</h2>
      <p className="m-0 text-small text-ink-2">Recording churn ends the subscription on the churn date and marks the customer Churned.</p>
      <Button size="sm" variant="danger" onClick={() => setChurnOpen(true)}>Record churn…</Button>
    </section>}

    {element}
    {churnOpen && <ChurnDialog customerId={customerId} reasons={reasons.data ?? []} onClose={() => setChurnOpen(false)} onDone={refresh} />}
  </div>;
}

function Field({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-caption text-ink-2">{label}</dt><dd className="m-0 text-ink">{value}</dd></div>;
}

function ChurnDialog({ customerId, reasons, onClose, onDone }: { customerId: string; reasons: { label: string; active: boolean }[]; onClose: () => void; onDone: () => void }) {
  const [reasonCode, setReasonCode] = useState('');
  const [churnDate, setChurnDate] = useState(new Date().toISOString().slice(0, 10));
  const [notes, setNotes] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const active = reasons.filter((r) => r.active);
  const submit = async () => {
    setBusy(true); setErr(null);
    try { await api.recordChurn(customerId, churnDate, reasonCode, notes); onDone(); onClose(); }
    catch (e) { setErr(e instanceof Error ? e.message : 'Could not record churn.'); }
    finally { setBusy(false); }
  };
  return <Dialog title="Record churn" description="This ends the subscription and marks the customer Churned." onClose={onClose}
    footer={<><Button variant="quiet" onClick={onClose}>Cancel</Button><Button variant="danger" disabled={!reasonCode || !notes.trim()} loading={busy} onClick={submit}>Record churn</Button></>}>
    <TextField label="Churn date" type="date" required value={churnDate} onChange={(e) => setChurnDate(e.target.value)} />
    <SelectField label="Reason" required value={reasonCode} onChange={(e) => setReasonCode(e.target.value)} options={[{ value: '', label: 'Choose…' }, ...active.map((r) => ({ value: r.label, label: r.label }))]} />
    <TextAreaField label="Notes" required rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} hint="Saved in the audit log; also doubles as the required reason." />
    {err && <Banner tone="danger" title={err} />}
  </Dialog>;
}
