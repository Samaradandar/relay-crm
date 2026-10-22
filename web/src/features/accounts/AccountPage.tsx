/* Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta. */
import { useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError, REQUEST_TYPE_LABEL, type RequestType } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Banner, Tabs, useToast } from '@/components/ui/Feedback';
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { Dialog } from '@/components/ui/Overlay';
import { formatDateTimeIST } from '@/lib/format';
import { SubscriptionPanel } from './SubscriptionPanel';

type Tab = 'overview' | 'timeline' | 'subscription';
const REQUEST_TYPES: RequestType[] = ['upsell_top_up', 'renewal', 'plan_change', 'other_product', 'increase', 'decrease', 'return'];

/** Account page: Overview + Timeline (Feature 3 history chain) + Subscription (Phase 4/5 actions). */
export function AccountPage() {
  const { id = '' } = useParams(); const { me } = useAuth(); const qc = useQueryClient(); const toast = useToast(); const nav = useNavigate();
  const [sp] = useSearchParams();
  const [tab, setTab] = useState<Tab>('overview');
  const [newRequest, setNewRequest] = useState(sp.get('action') === 'new-request');
  const q = useQuery({ queryKey: ['account', id], queryFn: () => api.account(id) });
  const timeline = useQuery({ queryKey: ['accountTimeline', id], queryFn: () => api.accountTimeline(id), enabled: tab === 'timeline' });
  const customers = useQuery({ queryKey: ['customers'], queryFn: api.customers });
  const [error, setError] = useState<string | null>(null);
  const createRequest = useMutation({
    mutationFn: (v: { accountId: string; requestType: RequestType; requirement: string; reason?: string }) => api.createRequest(v.accountId, v),
    onSuccess: (r) => { toast({ message: 'Request created' }); qc.invalidateQueries({ queryKey: ['deals'] }); nav(`/app/deals/${r.id}`); },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not create the request.')
  });

  if (q.isError) return <Banner tone="danger" title="This account is not available">{(q.error as Error).message}</Banner>;
  if (!q.data) return <div className="panel h-64 animate-pulse" />;
  const a = q.data;
  const customer = customers.data?.find((c) => c.accountId === a.id);
  const canCreateRequest = me?.role === 'admin' || me?.role === 'super_admin' || me?.role === 'team_leader' || a.accountOwnerId === me?.id;

  return <div className="flex min-w-0 flex-col gap-5">
    <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-[13px] text-ink-3"><Link to="/app/accounts" className="text-ink-2 no-underline hover:text-ink">Accounts</Link><span>/</span><span className="text-ink">{a.name}</span></nav>

    <header className="panel flex flex-col gap-3 p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-2"><h1 className="m-0 text-display">{a.name}</h1>
            {customer && (customer.status === 'churned' ? <Badge tone="danger" icon="x">Churned</Badge> : <Badge tone="success" icon="check">Active customer</Badge>)}</div>
          <p className="m-0 flex flex-wrap items-center gap-x-2 text-ink-2">{a.industry && <span>{a.industry}</span>}{a.city && <><span>·</span><span>{a.city}</span></>}{a.website && <><span>·</span><a href={`https://${a.website.replace(/^https?:\/\//, '')}`} target="_blank" rel="noreferrer" className="text-brand-ink">{a.website}</a></>}</p>
        </div>
        {customer && canCreateRequest && <Button variant="primary" icon="plus" onClick={() => setNewRequest(true)}>New request</Button>}
      </div>
      {error && <Banner tone="danger" title={error} />}
    </header>

    <Tabs<Tab> items={[{ id: 'overview', label: 'Overview' }, { id: 'timeline', label: 'Timeline' }, ...(customer ? [{ id: 'subscription' as Tab, label: 'Subscription' }] : [])]} active={tab} onChange={setTab} />

    {tab === 'overview' && <div className="grid gap-5 lg:grid-cols-2">
      <section className="panel flex flex-col gap-3 p-5">
        <h2 className="m-0 text-heading">Contacts</h2>
        {a.contacts.length === 0 && <p className="m-0 text-small text-ink-2">No contacts on file.</p>}
        {a.contacts.map((c) => <div key={c.id} className="flex flex-col gap-0.5 border-b border-line pb-2 last:border-0 last:pb-0"><p className="m-0 font-semibold text-ink">{c.name}{c.decisionMaker && <Badge tone="brand">Decision maker</Badge>}</p><p className="m-0 text-small text-ink-2">{c.role}{c.phone ? ` · ${c.phone}` : ''}{c.email ? ` · ${c.email}` : ''}</p></div>)}
      </section>
      <section className="panel flex flex-col gap-3 p-5">
        <h2 className="m-0 text-heading">Deals</h2>
        {a.deals.length === 0 && <p className="m-0 text-small text-ink-2">No deals on this account yet.</p>}
        {a.deals.map((d) => <Link key={d.id} to={`/app/deals/${d.id}`} className="flex items-center justify-between gap-2 border-b border-line py-2 text-sm no-underline last:border-0 hover:bg-surface-3"><span className="text-ink">{d.leadSource} · {d.stageKey}</span><span className="text-ink-2">{d.ownerName}</span></Link>)}
        {a.lockedDeals.map((d, i) => <p key={i} className="m-0 text-small text-ink-3">{d.label}</p>)}
      </section>
    </div>}

    {tab === 'timeline' && <section className="panel flex flex-col gap-3 p-5">
      <h2 className="m-0 text-heading">History</h2>
      {!timeline.data ? <p className="m-0 text-small text-ink-2">Loading…</p> : timeline.data.events.length === 0 ? <p className="m-0 text-small text-ink-2">No history yet.</p> :
        timeline.data.events.map((e, i) => <div key={i} className="flex items-start justify-between gap-2 border-b border-line py-2 text-sm last:border-0"><div><p className="m-0 text-ink">{e.title}</p>{e.detail && <p className="m-0 text-ink-2">{e.detail}</p>}</div><span className="whitespace-nowrap text-ink-3">{formatDateTimeIST(e.at)}</span></div>)}
    </section>}

    {tab === 'subscription' && customer && <SubscriptionPanel customerId={customer.id} />}

    {newRequest && <NewRequestDialog onClose={() => setNewRequest(false)} busy={createRequest.isPending} onSubmit={(v) => createRequest.mutate({ ...v, accountId: a.id })} />}
  </div>;
}

function NewRequestDialog({ onClose, onSubmit, busy }: { onClose: () => void; onSubmit: (v: { requestType: RequestType; requirement: string; reason?: string }) => void; busy: boolean }) {
  const { me } = useAuth();
  const [requestType, setRequestType] = useState<RequestType>('upsell_top_up');
  const [requirement, setRequirement] = useState('');
  const [reason, setReason] = useState('');
  const needsReason = me?.role === 'admin';
  return <Dialog title="New request" description="Starts a deal in the Existing customer pipeline at Request, under the account owner. It skips the Lead Pool." onClose={onClose}
    footer={<><Button variant="quiet" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!requirement.trim() || (needsReason && !reason.trim())} loading={busy} onClick={() => onSubmit({ requestType, requirement, reason: reason.trim() || undefined })}>Create request</Button></>}>
    <SelectField label="Request type" required value={requestType} onChange={(e) => setRequestType(e.target.value as RequestType)} options={REQUEST_TYPES.map((t) => ({ value: t, label: REQUEST_TYPE_LABEL[t] }))} />
    <TextAreaField label="Requirement" required rows={3} value={requirement} onChange={(e) => setRequirement(e.target.value)} placeholder="What the customer asked for" />
    <TextField label="Reason" required={needsReason} optionalNote={!needsReason ? '(optional)' : undefined} value={reason} onChange={(e) => setReason(e.target.value)} />
  </Dialog>;
}
