import clsx from 'clsx';
import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError, type Lead, type ProposalDraft, type ProposalFeedback, type ProposalPrefill, type ProposalRounds, type ProposalTerms } from '@/api/types';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Banner, useToast } from '@/components/ui/Feedback';
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { Icon } from '@/components/ui/Icon';
import { formatDateTimeIST, formatINR } from '@/lib/format';
import { PRICING_MODELS } from '@/lib/pipeline';

type Choice = ProposalFeedback | 'rejected';
const CHOICES: { value: Choice; title: string; text: string }[] = [
  { value: 'accepted', title: 'Accepted', text: 'The customer accepted. You fill in the terms and the proposal is drafted from them.' },
  { value: 'changes_requested', title: 'Revised', text: 'They want changes. Write them and book the next meeting here in Proposal; then record their answer again.' },
  { value: 'objections', title: 'Objection', text: 'They raised concerns. Write them and book the next meeting here in Proposal; then record their answer again.' },
  { value: 'rejected', title: 'Rejected', text: 'They said no. The deal is marked lost, with the reason.' },
];
const FEEDBACK_LABEL: Record<ProposalFeedback, string> = { accepted: 'Accepted', changes_requested: 'Changes requested', objections: 'Objections' };
const err = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

/** The Proposal stage as one loop: hold the walkthrough meeting, record what the customer says;
 * changes or objections close the round and a new meeting revises the proposal; once the customer
 * accepts, the proposal is drafted and emailed. Every round stays on the deal. */
export function ProposalPanel({ d, canAct, onLog, onLost }: { d: Lead; canAct: boolean; onLog: (meeting: 'booked' | 'held') => void; onLost: () => void }) {
  const rounds = useQuery({ queryKey: ['proposalRounds', d.id], queryFn: () => api.proposalRounds(d.id) });
  if (rounds.isError) return <Banner tone="danger" title="The proposal rounds could not be loaded">{err(rounds.error, '')}</Banner>;
  if (!rounds.data) return <div className="panel h-40 animate-pulse" />;
  return <Loaded d={d} r={rounds.data} act={canAct && d.status === 'open'} onLog={onLog} onLost={onLost} />;
}

function Loaded({ d, r, act, onLog, onLost }: { d: Lead; r: ProposalRounds; act: boolean; onLog: (meeting: 'booked' | 'held') => void; onLost: () => void }) {
  const qc = useQueryClient(); const toast = useToast();
  const refresh = () => ['proposalRounds', 'proposals', 'deal', 'deals', 'today', 'stagePage'].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  /** The checklist rows are ticked here as each step completes; a row that is not ready yet can still be ticked below. */
  const tryTick = async (key: string) => { try { await api.tickCriterion(d.id, key, {}); } catch { /* not ready or already ticked: the checklist shows why */ } };
  const closed = r.rounds.filter((x) => x.closedAt);
  const first = r.currentRoundNo === 1;

  return <section className="panel flex flex-col gap-4 p-5" aria-labelledby="prop-h">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 id="prop-h" className="m-0 text-heading">Proposal · round {r.currentRoundNo}</h2>
      {r.accepted ? <Badge tone="success" icon="check">Accepted</Badge> : <Badge>{first ? 'Walkthrough' : 'Revising'}</Badge>}
    </div>

    {closed.length > 0 && <ol className="m-0 flex list-none flex-col gap-2 p-0">
      {closed.map((x) => <li key={x.id} className="flex flex-col gap-0.5 rounded-lg border border-line bg-surface-0 p-3">
        <span className="flex flex-wrap items-center gap-2 text-small"><Badge tone="warning">Round {x.roundNo} closed</Badge><span className="text-body-strong">{FEEDBACK_LABEL[x.feedback]}</span><span className="text-xs text-ink-3">{formatDateTimeIST(x.recordedAt)}{x.recordedByName ? ` · ${x.recordedByName}` : ''}</span></span>
        <span className="text-small text-ink-2">{x.changes}</span>
        {x.meeting && <span className="text-xs text-ink-3">After the meeting on {formatDateTimeIST(x.meeting.occurredAt)}</span>}
      </li>)}
    </ol>}

    <Step n={1} title={first ? 'Proposal walkthrough meeting' : 'Meeting to revise the proposal'} done={!!r.heldMeeting}>
      {r.heldMeeting ? <p className="m-0 text-small text-ink-2">Held on {formatDateTimeIST(r.heldMeeting.occurredAt)} with {r.heldMeeting.attendees}.</p>
        : r.bookedMeeting ? <p className="m-0 text-small text-ink-2">Booked for {formatDateTimeIST(r.bookedMeeting.occurredAt)}. When it has happened, mark it held in the Meetings panel.</p>
          : <div className="flex flex-col gap-2"><p className="m-0 text-small text-ink-2">{first ? 'Walk the customer through the proposal and log the meeting that was held.' : 'The next meeting was not booked. Book it, or log it if it already happened.'}</p>
            {act && <span className="flex flex-wrap gap-2"><Button size="sm" icon="calendar" onClick={() => onLog('booked')}>Book the meeting</Button><Button size="sm" icon="file" onClick={() => onLog('held')}>Log the meeting that was held</Button></span>}</div>}
    </Step>

    <Step n={2} title="What the customer said" done={r.accepted}>
      {r.accepted ? <AcceptedAgain d={d} r={r} act={act} onLost={onLost} onSaved={async () => { refresh(); toast({ message: 'Round closed and the next meeting is booked. Hold it, log it, then record the answer again' }); }} />
        : <Feedback d={d} enabled={act && !!r.heldMeeting} hasMeeting={!!r.heldMeeting} onLost={onLost} onSaved={async (choice) => { if (choice === 'accepted') { await tryTick('walkthrough_logged'); await tryTick('feedback_recorded'); } refresh(); toast({ message: choice === 'accepted' ? 'Accepted. Now fill in the terms and draft the proposal' : 'Round closed and the next meeting is booked. Hold it, log it, then record the answer again' }); }} />}
    </Step>

    <Step n={3} title="Proposal terms, draft and sending" done={false} locked={!r.accepted}>
      {r.accepted ? <ProposalBuilder d={d} act={act} onSent={async () => { await tryTick('proposal_emailed'); refresh(); toast({ message: 'Proposal sent' }); }} /> : <p className="m-0 text-small text-ink-3">Opens once the customer has accepted.</p>}
    </Step>
  </section>;
}

function Step({ n, title, done, locked, children }: { n: number; title: string; done: boolean; locked?: boolean; children: React.ReactNode }) {
  return <div className={clsx('grid grid-cols-[28px_minmax(0,1fr)] gap-3', locked && 'opacity-60')}>
    <span className={clsx('grid h-7 w-7 place-items-center rounded-full text-[13px] font-semibold', done ? 'bg-brand text-brand-on' : 'bg-surface-2 text-ink-2')}>{done ? <Icon name="check" size={14} /> : n}</span>
    <div className="flex min-w-0 flex-col gap-2"><span className="text-body-strong">{title}</span>{children}</div>
  </div>;
}

function Feedback({ d, enabled, hasMeeting, again, onLost, onSaved }: { d: Lead; enabled: boolean; hasMeeting: boolean; again?: boolean; onLost: () => void; onSaved: (c: ProposalFeedback) => void | Promise<void> }) {
  const [choice, setChoice] = useState<Choice | ''>(''); const [text, setText] = useState(''); const [when, setWhen] = useState(''); const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: () => api.recordProposalFeedback(d.id, choice as ProposalFeedback, text, needsText && when ? new Date(when).toISOString() : undefined),
    onSuccess: async () => { const c = choice as ProposalFeedback; setChoice(''); setText(''); setWhen(''); setError(null); await onSaved(c); },
    onError: (e) => setError(err(e, 'Could not save.')),
  });
  const needsText = choice === 'changes_requested' || choice === 'objections';
  const ready = enabled && !!choice && choice !== 'rejected' && (!needsText || (text.trim().length >= 3 && !!when && new Date(when).getTime() > Date.now()));
  return <div className="flex flex-col gap-3">
    {!hasMeeting && <p className="m-0 text-small text-ink-3">Log the held meeting first: the customer's answer is about that meeting.</p>}
    <fieldset disabled={!enabled} className="m-0 grid gap-2 border-0 p-0 sm:grid-cols-2">
      {CHOICES.filter((c) => !again || c.value !== 'accepted').map((c) => <label key={c.value} className={clsx('flex cursor-pointer flex-col gap-0.5 rounded-lg border p-3', choice === c.value ? 'border-brand bg-brand-soft' : 'border-line-control hover:bg-surface-3', !enabled && 'cursor-not-allowed')}>
        <span className="flex items-center gap-2"><input type="radio" name="proposal-feedback" checked={choice === c.value} onChange={() => setChoice(c.value)} className="accent-[var(--brand)]" /><span className="text-[14px] font-semibold">{c.title}</span></span>
        <span className="pl-6 text-xs text-ink-2">{c.text}</span>
      </label>)}
    </fieldset>
    {needsText && <TextAreaField label={choice === 'changes_requested' ? 'What changes does the customer want?' : 'What are their objections?'} required rows={3} value={text} onChange={(e) => setText(e.target.value)} placeholder={choice === 'changes_requested' ? 'e.g. Wants quarterly billing and 25,000 minutes' : 'e.g. Thinks the per-minute price is too high'} />}
    {needsText && <TextField label="Next meeting: date and time" required type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} error={when && new Date(when).getTime() <= Date.now() ? 'Pick a time in the future.' : undefined} hint="The next meeting to discuss it. It is booked for you in this stage; log it as held when it happens." />}
    {error && <Banner tone="danger" title={error} />}
    <span className="flex flex-wrap gap-2">
      {choice === 'rejected' ? <Button variant="danger" onClick={onLost}>Mark the deal as lost…</Button> : <Button variant="primary" icon="check" disabled={!ready} loading={save.isPending} onClick={() => { setError(null); save.mutate(); }}>{choice === 'accepted' ? 'Save: customer accepted' : needsText ? 'Save, close this round and book the meeting' : 'Save'}</Button>}
    </span>
  </div>;
}

/** The customer accepted, then changed their mind: the rep can reopen the loop (write the changes, book the meeting). */
function AcceptedAgain({ d, r, act, onLost, onSaved }: { d: Lead; r: ProposalRounds; act: boolean; onLost: () => void; onSaved: () => void | Promise<void> }) {
  const [open, setOpen] = useState(false);
  return <div className="flex flex-col gap-2">
    <p className="m-0 text-small text-ink-2">The customer accepted in round {r.currentRoundNo}.</p>
    {act && !open && <span><Button size="sm" variant="quiet" onClick={() => setOpen(true)}>The customer wants more changes</Button></span>}
    {open && <Feedback d={d} enabled={act} hasMeeting again onLost={onLost} onSaved={async () => { setOpen(false); await onSaved(); }} />}
  </div>;
}

const MODEL_FREQUENCIES: Record<string, string[]> = { 'Per-minute (managed)': ['Monthly', 'Quarterly'], 'SaaS (customer infrastructure)': ['Monthly', 'Quarterly', 'Yearly'] };
const frequenciesFor = (model: string) => MODEL_FREQUENCIES[model] ?? ['Monthly', 'Quarterly', 'Yearly'];
type FormLine = { productId?: string; description: string; quantity: string; price: string };
const rupees = (paise: number) => String(paise / 100);
const toPaise = (v: string) => Math.round(Number(v) * 100);

/** The terms the customer accepted. They give the proposal its structure; the draft is built from them. */
function TermsForm({ d, prefill, busy, error, label, onSubmit }: { d: Lead; prefill: ProposalPrefill; busy: boolean; error: string | null; label: string; onSubmit: (recipient: { name: string; email: string }, terms: ProposalTerms) => void }) {
  const dmc = d.contacts?.find((c) => c.decisionMaker) ?? d.contacts?.[0];
  const last = prefill.lastTerms;
  const [name, setName] = useState(dmc?.name ?? ''); const [email, setEmail] = useState(dmc?.email ?? '');
  const [lines, setLines] = useState<FormLine[]>(() => (last?.lines ?? prefill.lines).map((l) => ({ productId: l.productId, description: l.description, quantity: String(l.quantity), price: rupees(l.unitPricePaise) })));
  const [model, setModel] = useState(last?.businessModel ?? (PRICING_MODELS.includes(prefill.businessModel ?? '') ? prefill.businessModel! : ''));
  const [freq, setFreq] = useState(last?.billingFrequency ?? '');
  const [minutes, setMinutes] = useState(String(last?.minCommittedMinutes ?? prefill.minCommittedMinutes ?? ''));
  const [conc, setConc] = useState(String(last?.concurrency ?? ''));
  const [tax, setTax] = useState(String(last?.taxPercent ?? 18));
  const [scope, setScope] = useState(last?.scopeOfWork ?? prefill.scopeOfWork ?? '');
  const [tnc, setTnc] = useState(last?.termsAndConditions ?? '');
  const [company, setCompany] = useState(last?.companyName ?? prefill.companyName);
  const [details, setDetails] = useState(last?.companyDetails ?? '');
  const setLine = (i: number, patch: Partial<FormLine>) => setLines((ls) => ls.map((l, k) => (k === i ? { ...l, ...patch } : l)));
  const freqs = frequenciesFor(model);
  const perMinute = model.startsWith('Per-minute');
  const emailOk = /^\S+@\S+\.\S+$/.test(email.trim());
  const linesOk = lines.length > 0 && lines.every((l) => l.description.trim() && Number(l.quantity) >= 1 && l.price !== '' && Number(l.price) >= 0);
  const ready = !!name.trim() && emailOk && linesOk && !!model && freqs.includes(freq) && (!perMinute || Number(minutes) >= 0 && minutes !== '') && Number(conc) >= 1
    && tax !== '' && Number(tax) >= 0 && Number(tax) <= 100 && scope.trim().length >= 3 && tnc.trim().length >= 3 && !!company.trim();
  const submit = () => onSubmit({ name: name.trim(), email: email.trim() }, {
    lines: lines.map((l) => ({ productId: l.productId, description: l.description.trim(), quantity: Number(l.quantity), unitPricePaise: toPaise(l.price) })),
    businessModel: model, billingFrequency: freq, minCommittedMinutes: minutes === '' ? undefined : Number(minutes), concurrency: Number(conc), taxPercent: Number(tax),
    termsAndConditions: tnc.trim(), scopeOfWork: scope.trim(), companyName: company.trim(), companyDetails: details.trim() || undefined,
  });
  return <div className="flex flex-col gap-4">
    <p className="m-0 text-small text-ink-2">Fill in the terms the customer accepted. The proposal is drafted from them, in this order: who it is for, scope of work, commercials, products and pricing, then terms and conditions. You can still edit the text before it goes out.</p>
    <div className="grid gap-3 sm:grid-cols-2">
      <TextField label="Company name" required value={company} onChange={(e) => setCompany(e.target.value)} />
      <TextAreaField label="Company details" optionalNote="(address, GST number…)" rows={2} value={details} onChange={(e) => setDetails(e.target.value)} />
      <TextField label="Recipient name" required value={name} onChange={(e) => setName(e.target.value)} />
      <TextField label="Recipient email" required type="email" value={email} onChange={(e) => setEmail(e.target.value)} error={email && !emailOk ? 'Check the email address.' : undefined} />
    </div>
    <fieldset className="m-0 flex flex-col gap-2 border-0 p-0">
      <legend className="mb-1 text-[13px] font-semibold">Products: description and price per unit (₹)</legend>
      {lines.map((l, i) => <div key={i} className="grid items-end gap-2 sm:grid-cols-[minmax(0,1fr)_90px_130px_auto]">
        <TextField label="Product description" required value={l.description} onChange={(e) => setLine(i, { description: e.target.value })} />
        <TextField label="Quantity" required type="number" min={1} value={l.quantity} onChange={(e) => setLine(i, { quantity: e.target.value })} />
        <TextField label="Price per unit (₹)" required type="number" min={0} value={l.price} onChange={(e) => setLine(i, { price: e.target.value })} />
        <Button size="sm" variant="quiet" disabled={lines.length < 2} onClick={() => setLines((ls) => ls.filter((_, k) => k !== i))}>Remove</Button>
      </div>)}
      <span><Button size="sm" variant="quiet" icon="plus" onClick={() => setLines((ls) => [...ls, { description: '', quantity: '1', price: '' }])}>Add a line</Button></span>
      <p className="m-0 text-xs text-ink-3">A price lower than the approved price for a product needs a discount approval on the deal first.</p>
    </fieldset>
    <div className="grid gap-3 sm:grid-cols-2">
      <SelectField label="Business model" required value={model} onChange={(e) => { setModel(e.target.value); setFreq(''); }} options={[{ value: '', label: 'Choose…' }, ...PRICING_MODELS.map((m) => ({ value: m, label: m }))]} />
      <SelectField label="Billing frequency" required value={freq} onChange={(e) => setFreq(e.target.value)} hint={model ? `Billing options for ${model}.` : 'Choose the business model first.'} options={[{ value: '', label: 'Choose…' }, ...freqs.map((f) => ({ value: f, label: f }))]} />
      <TextField label="Minimum committed minutes per month" required={perMinute} optionalNote={perMinute ? undefined : '(if any)'} type="number" min={0} value={minutes} onChange={(e) => setMinutes(e.target.value)} />
      <TextField label="Concurrency (calls at the same time)" required type="number" min={1} value={conc} onChange={(e) => setConc(e.target.value)} />
      <TextField label="Tax percentage" required type="number" min={0} max={100} value={tax} onChange={(e) => setTax(e.target.value)} />
    </div>
    <TextAreaField label="Scope of work" required rows={4} value={scope} onChange={(e) => setScope(e.target.value)} />
    <TextAreaField label="Terms and conditions" required rows={4} value={tnc} onChange={(e) => setTnc(e.target.value)} />
    {error && <Banner tone="danger" title={error} />}
    <span><Button variant="primary" icon="file" disabled={!ready} loading={busy} onClick={submit}>{label}</Button></span>
  </div>;
}

/** Step 3 of Proposal, and the same terms again in Revision: fill the terms, draft the proposal, edit it, send it. */
export function ProposalBuilder({ d, act, revising, onSent }: { d: Lead; act: boolean; revising?: boolean; onSent: () => void | Promise<void> }) {
  const qc = useQueryClient();
  const drafts = useQuery({ queryKey: ['proposals', d.id], queryFn: () => api.proposalDrafts(d.id) });
  const prefill = useQuery({ queryKey: ['proposalPrefill', d.id], queryFn: () => api.proposalPrefill(d.id), enabled: act });
  const latest: ProposalDraft | undefined = [...(drafts.data ?? [])].sort((a, b) => b.versionNo - a.versionNo)[0];
  const [again, setAgain] = useState(false);
  const [name, setName] = useState(''); const [email, setEmail] = useState('');
  const [body, setBody] = useState(''); const [terms, setTerms] = useState(''); const [error, setError] = useState<string | null>(null);
  useEffect(() => { if (latest) { setBody(latest.bodyHtml); setTerms(latest.termsHtml); setName(latest.recipientName ?? ''); setEmail(latest.recipientEmail ?? ''); } }, [latest?.id]); // eslint-disable-line react-hooks/exhaustive-deps
  const done = () => ['proposals', 'proposalPrefill'].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const create = useMutation({ mutationFn: (v: { r: { name: string; email: string }; t: ProposalTerms }) => api.createProposalDraft(d.id, { ...v.r, terms: v.t }), onSuccess: () => { setAgain(false); setError(null); done(); }, onError: (e) => setError(err(e, 'Could not create the draft.')) });
  const save = useMutation({ mutationFn: () => api.saveProposalDraft(latest!.id, { bodyHtml: body, termsHtml: terms }), onSuccess: done, onError: (e) => setError(err(e, 'Could not save.')) });
  const send = useMutation({
    mutationFn: async () => { if (body !== latest!.bodyHtml || terms !== latest!.termsHtml) await api.saveProposalDraft(latest!.id, { bodyHtml: body, termsHtml: terms }); return api.sendProposal(latest!.id, { email: email.trim(), name: name.trim() }); },
    onSuccess: async () => { done(); await onSent(); }, onError: (e) => setError(err(e, 'Could not send the proposal.')),
  });
  const emailOk = /^\S+@\S+\.\S+$/.test(email.trim()); const who = !!name.trim() && emailOk;
  if (drafts.isLoading) return <div className="h-16 animate-pulse rounded-md bg-surface-2" />;
  const sent = !!latest && (latest.status === 'sent' || latest.status === 'accepted');

  if (!latest || again) {
    if (!act) return <p className="m-0 text-small text-ink-3">No proposal drafted yet.</p>;
    if (!prefill.data) return <div className="h-16 animate-pulse rounded-md bg-surface-2" />;
    return <TermsForm d={d} prefill={prefill.data} busy={create.isPending} error={error} label={latest ? 'Create the revised proposal draft' : 'Create the proposal draft'} onSubmit={(r, t) => { setError(null); create.mutate({ r, t }); }} />;
  }
  if (sent) {
    return <div className="flex flex-col gap-2 rounded-lg border border-line bg-surface-0 p-3 text-small"><span className="flex items-center gap-2"><Icon name="check" className="text-success" /><span className="text-body-strong">Proposal v{latest.versionNo} sent</span></span>
      <span className="text-ink-2">To {latest.recipientName || 'the customer'} &lt;{latest.sentTo}&gt; on {latest.sentAt ? formatDateTimeIST(latest.sentAt) : ''}, with the PDF attached.{revising ? '' : ' Next: set the decision date below, then move to Revision.'}</span>
      {act && revising && <span><Button size="sm" icon="file" onClick={() => setAgain(true)}>Revise the terms and draft a new version</Button></span>}</div>;
  }
  return <div className="flex flex-col gap-3">
    <div className="grid gap-3 sm:grid-cols-2">
      <TextField label="Recipient name" required value={name} onChange={(e) => setName(e.target.value)} disabled={!act} />
      <TextField label="Recipient email" required type="email" value={email} onChange={(e) => setEmail(e.target.value)} error={email && !emailOk ? 'Check the email address.' : undefined} disabled={!act} />
    </div>
    <div className="overflow-x-auto rounded-lg border border-line"><table className="w-full border-collapse text-small">
      <thead><tr>{['Product', 'Qty', 'Price per unit', 'Amount'].map((h) => <th key={h} className="border-b border-line bg-surface-2 px-3 py-2 text-left text-xs font-semibold text-ink-2">{h}</th>)}</tr></thead>
      <tbody>{latest.items.map((i) => <tr key={i.id}><td className="border-b border-line px-3 py-2">{i.description}</td><td className="border-b border-line px-3">{i.quantity}</td><td className="border-b border-line px-3">{formatINR(i.unitPricePaise)}</td><td className="border-b border-line px-3 font-semibold">{formatINR(i.lineTotalPaise)}</td></tr>)}</tbody></table></div>
    <TextAreaField label="Proposal text (HTML)" rows={8} value={body} onChange={(e) => setBody(e.target.value)} disabled={!act} className="font-mono" />
    <TextAreaField label="Terms and conditions (HTML)" rows={3} value={terms} onChange={(e) => setTerms(e.target.value)} disabled={!act} className="font-mono" />
    <div className="flex flex-col gap-1.5"><span className="text-[13px] font-semibold">Preview</span>
      <iframe title="Proposal preview" sandbox="" srcDoc={`<body style="font-family:Arial,sans-serif;font-size:14px;padding:12px">${body}<hr>${terms}</body>`} className="h-72 w-full rounded-lg border border-line bg-white" /></div>
    {error && <Banner tone="danger" title={error} />}
    {act && <span className="flex flex-wrap gap-2"><Button icon="file" loading={save.isPending} onClick={() => { setError(null); save.mutate(); }}>Save changes</Button><Button variant="quiet" onClick={() => setAgain(true)}>Change the terms</Button><Button variant="primary" icon="send" disabled={!who} loading={send.isPending} onClick={() => { setError(null); send.mutate(); }}>Send the proposal by email</Button></span>}
  </div>;
}

/** Revision defines the same terms as Proposal: start from the latest draft's terms, draft a new version, send it again. */
export function RevisionTerms({ d, canAct }: { d: Lead; canAct: boolean }) {
  const qc = useQueryClient(); const toast = useToast();
  return <section className="panel flex flex-col gap-4 p-5" aria-labelledby="rev-h">
    <h2 id="rev-h" className="m-0 text-heading">Proposal terms</h2>
    <p className="m-0 text-small text-ink-2">The same terms as in Proposal: products and prices, business model, billing frequency, minimum committed minutes, concurrency, tax, scope of work, terms and conditions, and the company. Change them here, draft the revised proposal and send it again.</p>
    <ProposalBuilder d={d} act={canAct} revising onSent={() => { ['proposals', 'deal', 'deals'].forEach((k) => qc.invalidateQueries({ queryKey: [k] })); toast({ message: 'Revised proposal sent' }); }} />
  </section>;
}
