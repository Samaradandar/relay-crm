import clsx from 'clsx';
import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError, LEAD_SOURCES, type CreateLeadResult, type LeadSource, type NewLeadInput } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/Button';
import { Banner, ReasonBox, useToast } from '@/components/ui/Feedback';
import { SelectField, TextField } from '@/components/ui/Field';
import { Icon } from '@/components/ui/Icon';
import { WAITING_LABEL, howAssigned } from '@/lib/leads';
import { todayIST } from '@/lib/format';

const INDUSTRIES = ['', 'Food & beverage', 'Healthcare', 'Hospitality', 'Retail', 'Automotive', 'Logistics', 'Real estate', 'Education', 'Finance', 'Fitness', 'Events', 'Construction', 'Other'];
const EMPTY: NewLeadInput = { company: '', website: '', industry: '', city: '', contactName: '', contactRole: '', phone: '', email: '', source: '' as LeadSource, nextStep: '', firstContactDate: '', contactDecisionMaker: false };

/** PRD Feature 1: manual lead entry. Saving places the lead in the Lead Pool; the engine picks the owner. */
export function NewLeadPage() {
  const { me } = useAuth(); const nav = useNavigate(); const qc = useQueryClient(); const toast = useToast();
  const [f, setF] = useState<NewLeadInput>(EMPTY);
  const [touched, setTouched] = useState(false);
  const [anyway, setAnyway] = useState(false);
  const [reason, setReason] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<CreateLeadResult | null>(null);
  const set = (k: keyof NewLeadInput) => (e: { target: { value: string } }) => setF((x) => ({ ...x, [k]: e.target.value }));

  // live duplicate check, debounced
  const [probe, setProbe] = useState<Partial<NewLeadInput>>({});
  useEffect(() => { const t = window.setTimeout(() => setProbe({ company: f.company, website: f.website, phone: f.phone, email: f.email }), 350); return () => window.clearTimeout(t); }, [f.company, f.website, f.phone, f.email]);
  const hasProbe = !!(probe.company?.trim() || probe.website || probe.phone || probe.email);
  const dups = useQuery({ queryKey: ['dups', probe], queryFn: () => api.checkDuplicates(probe), enabled: hasProbe });
  const matches = hasProbe ? dups.data ?? [] : [];
  const blocked = matches.find((m) => m.kind === 'exact') && me?.role !== 'super_admin';
  const warned = !blocked && matches.length > 0;
  useEffect(() => { if (!warned) setAnyway(false); }, [warned]);

  const reasonPolicy = me?.role === 'super_admin' ? 'optional' : (me?.role === 'admin' || anyway) ? 'required' : 'none';
  const errs = {
    company: !f.company.trim() && 'Enter the company name.', contactName: !f.contactName.trim() && 'Enter the contact’s name.',
    reach: !f.phone?.trim() && !f.email?.trim() && 'Add a phone number or an email.',
    source: !f.source && 'Choose where the lead came from.', nextStep: !f.nextStep.trim() && 'Every lead needs a next step.',
    email: !!f.email && !/^\S+@\S+\.\S+$/.test(f.email) && 'Check the email address.'
  };
  const invalid = Object.values(errs).some(Boolean);
  const e = (k: keyof typeof errs): string | undefined => { const v = errs[k]; return touched && typeof v === 'string' ? v : undefined; };

  const save = useMutation({
    mutationFn: () => api.createLead({ ...f, company: f.company.trim() }, { reason, createAnyway: anyway }),
    onSuccess: (r) => {
      ['myLeads', 'pool', 'poolSummary', 'teamLoad', 'recentAssignments', 'notifications'].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      setDone(r); toast({ message: r.outcome.kind === 'assigned' ? `${r.lead.company} added and assigned to ${r.outcome.toYou ? 'you' : r.outcome.ownerName}` : `${r.lead.company} added to the Lead Pool` });
    },
    onError: (x) => setError(x instanceof ApiError ? x.message : 'Could not save the lead.')
  });
  const submit = (ev: FormEvent) => { ev.preventDefault(); setTouched(true); setError(null); if (invalid || blocked || (warned && !anyway) || (reasonPolicy === 'required' && !reason.trim())) return; save.mutate(); };

  if (done) return <Saved r={done} onAnother={() => { setDone(null); setF(EMPTY); setTouched(false); setReason(''); setAnyway(false); }} onHome={() => nav('/app')} />;

  return <>
    <PageHeader title="Add new lead" subtitle="Saving puts the lead in the Lead Pool. Relay assigns it by rule or round-robin, and you are recorded as the person who generated it." />
    <form onSubmit={submit} noValidate className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
      <div className="flex flex-col gap-5">
        <Section title="Company">
          <TextField label="Company name" required value={f.company} onChange={set('company')} error={e('company')} placeholder="e.g. Sharma Sweets & Bakers" autoFocus className="sm:col-span-2" />
          <TextField label="Website" optionalNote="(recommended)" value={f.website} onChange={set('website')} placeholder="sharmasweets.in" hint="Same website = same company. It is the strongest duplicate check." />
          <SelectField label="Industry" optionalNote="(optional)" value={f.industry} onChange={set('industry')} options={INDUSTRIES.map((i) => ({ value: i, label: i || 'Choose…' }))} />
          <TextField label="City" optionalNote="(optional)" value={f.city} onChange={set('city')} placeholder="Delhi" />
        </Section>
        <Section title="Contact">
          <TextField label="Contact name" required value={f.contactName} onChange={set('contactName')} error={e('contactName')} />
          <TextField label="Role" optionalNote="(optional)" value={f.contactRole} onChange={set('contactRole')} list="roles" placeholder="Owner, Ops Head…" />
          <datalist id="roles">{['Owner', 'Ops Head', 'CEO', 'Finance'].map((r) => <option key={r} value={r} />)}</datalist>
          <TextField label="Phone" type="tel" value={f.phone} onChange={set('phone')} placeholder="+91 98110 22345" error={e('reach')} hint="Phone or email: at least one." />
          <TextField label="Email" type="email" value={f.email} onChange={set('email')} placeholder="name@company.in" error={e('email')} />
          <SelectField label="Contact type" required value={f.contactDecisionMaker ? 'decision_maker' : 'influencer'} onChange={(ev) => setF((x) => ({ ...x, contactDecisionMaker: ev.target.value === 'decision_maker' }))}
            options={[{ value: 'influencer', label: 'Influencer' }, { value: 'decision_maker', label: 'Decision maker' }]} hint="Decision maker signs or approves the deal. You can change this at any stage." />
        </Section>
        <Section title="Where it came from and what happens next">
          <SelectField label="Lead source" required value={f.source} onChange={set('source')} error={e('source')} options={[{ value: '', label: 'Choose…' }, ...LEAD_SOURCES.map((s) => ({ value: s, label: s }))]} />
          <TextField label="First contact date" optionalNote="(if you are entering it late)" type="date" max={todayIST()} value={f.firstContactDate} onChange={set('firstContactDate')} />
          <TextField label="Next step" required value={f.nextStep} onChange={set('nextStep')} error={e('nextStep')} placeholder="e.g. Intro call to understand call volume" className="sm:col-span-2" />
        </Section>
        {warned && <label className="flex items-start gap-2.5 rounded-lg border border-line-control bg-surface-1 p-3 text-sm">
          <input type="checkbox" className="mt-0.5 accent-[var(--brand)]" checked={anyway} onChange={(x) => setAnyway(x.target.checked)} />
          <span><span className="text-body-strong">This is a different company. Create it anyway.</span><span className="block text-ink-2">The reason is saved in history{matches.some((m) => !m.inScope) ? ' and Admins are told, because the match belongs to another team' : ''}.</span></span>
        </label>}
        <ReasonBox id="lead-reason" policy={reasonPolicy} value={reason} onChange={setReason} label={anyway ? 'Why create it anyway?' : 'Reason'} placeholder={anyway ? 'e.g. Different branch, separate owner and GST number' : 'e.g. Lead from the Pune expo, entered for Neha'} />
        {error && <Banner tone="danger" title={error} />}
        <div className="flex flex-wrap items-center justify-end gap-2 border-t border-line pt-4">
          <Link to="/app" className="mr-auto text-sm text-ink-2 hover:text-ink">Cancel</Link>
          <Button type="submit" variant="primary" icon="send" loading={save.isPending} disabled={!!blocked}>Save to Lead Pool</Button>
        </div>
      </div>
      <DuplicatePanel loading={hasProbe && dups.isFetching} matches={matches} empty={!hasProbe} />
    </form>
  </>;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <section className="panel flex flex-col gap-4 p-5"><h2 className="m-0 text-heading">{title}</h2><div className="grid gap-4 sm:grid-cols-2">{children}</div></section>;
}

function DuplicatePanel({ matches, loading, empty }: { matches: Awaited<ReturnType<typeof api.checkDuplicates>>; loading: boolean; empty: boolean }) {
  const exact = matches.filter((m) => m.kind === 'exact'); const similar = matches.filter((m) => m.kind === 'similar');
  return <aside aria-live="polite" className="panel sticky top-[72px] flex flex-col gap-3 p-5">
    <h2 className="m-0 flex items-center gap-2 text-heading"><Icon name="search" />Duplicate check</h2>
    {empty ? <p className="m-0 text-small text-ink-2">Start typing the company, website, phone or email. Relay checks every lead in the company and shows only what you are allowed to see.</p>
      : loading && matches.length === 0 ? <p className="m-0 text-small text-ink-3">Checking…</p>
      : matches.length === 0 ? <p className="m-0 flex items-center gap-2 text-small text-success"><Icon name="check" />No matching company found.</p> : null}
    {exact.map((m, i) => m.isExistingCustomer && m.accountId
      ? <div key={'e' + i} className="rounded-md border border-brand bg-brand-soft p-3 text-small">
          <p className="m-0 font-semibold text-brand-ink">This is an existing customer: create a request instead</p>
          <p className="m-0 mt-1 text-ink-2">{m.company} is already a customer. Requests skip the Lead Pool and go straight to the account owner.</p>
          <Link to={`/app/accounts/${m.accountId}?action=new-request`} className="mt-2 inline-flex h-8 items-center gap-1.5 rounded-md bg-brand px-3 text-[13px] font-semibold text-brand-on no-underline hover:bg-brand-hover">Create a request</Link>
        </div>
      : <div key={'e' + i} className="rounded-md border border-danger bg-danger-soft p-3 text-small">
          <p className="m-0 font-semibold text-danger">Same {m.field} as {m.company}</p>
          <p className="m-0 mt-1 text-ink-2">{m.inScope ? 'This lead is already yours or your team’s. Open it instead of adding a new one.' : `Company already exists, owner: ${m.ownerName ?? 'waiting in the Pool'}${m.teamName ? ` (${m.teamName})` : ''}. Contact your Team Leader.`}</p>
        </div>)}
    {similar.length > 0 && <div className="flex flex-col gap-2 rounded-md border border-warning bg-warning-soft p-3 text-small">
      <p className="m-0 font-semibold text-warning">{similar.length === 1 ? 'A similar company exists' : `${similar.length} similar companies exist`}</p>
      <ul className="m-0 flex list-none flex-col gap-1 p-0">{similar.map((m, i) => <li key={i} className="text-ink"><span className="font-semibold">{m.company}</span><span className="text-ink-2"> · {m.ownerName ?? 'In the Pool'}{m.teamName ? `, ${m.teamName}` : ''}</span></li>)}</ul>
      <p className="m-0 text-ink-2">If it is the same business, ask the owner. If not, tick “Create it anyway” and add a reason.</p>
    </div>}
  </aside>;
}

function Saved({ r, onAnother, onHome }: { r: CreateLeadResult; onAnother: () => void; onHome: () => void }) {
  const o = r.outcome;
  return <div className="mx-auto flex max-w-[620px] flex-col items-center gap-5 py-10 text-center">
    <span className={clsx('grid h-14 w-14 place-items-center rounded-full', o.kind === 'assigned' ? 'bg-success-soft text-success' : 'bg-accent-soft text-accent-ink')}><Icon name={o.kind === 'assigned' ? 'check' : 'pool'} size={28} /></span>
    <div className="flex flex-col gap-1.5">
      <h1 className="m-0 text-display">{r.lead.company} is in Relay</h1>
      {o.kind === 'assigned'
        ? <p className="m-0 text-ink-2">{o.toYou ? 'Assigned to you' : <>Assigned to <b className="text-ink">{o.ownerName}</b></>} · {howAssigned({ method: o.method, ruleName: o.ruleName })}. {o.toYou ? 'It is on your home page now.' : 'You stay recorded as the person who generated it.'}</p>
        : <p className="m-0 text-ink-2">Waiting in the Lead Pool for {o.teamName}: {WAITING_LABEL[o.reason].toLowerCase()}. Their Team Leader has been told and can assign it by hand.</p>}
    </div>
    <div className="flex gap-2"><Button onClick={onHome}>Back to home</Button><Button variant="primary" icon="plus" onClick={onAnother}>Add another lead</Button></div>
  </div>;
}
