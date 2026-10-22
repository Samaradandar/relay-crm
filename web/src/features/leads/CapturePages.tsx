import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { API_URL } from '@/api/client';
import { ApiError, LEAD_SOURCES, type CaptureAction, type CaptureReview, type CaptureSource } from '@/api/types';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Banner, useToast } from '@/components/ui/Feedback';
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { useReasonPrompt } from '@/components/ui/ReasonPrompt';
import { formatRelativeIST } from '@/lib/format';

const err = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);
const KIND: Record<string, string> = { website: 'Website form', email: 'Email', csv: 'CSV file', csv_template: 'CSV template' };

/** Leads that came in on their own (a website form, an email) and look like a company we already
 * know. A person decides: create it anyway, add it to the lead we already have, or throw it away. */
export function CaptureReviewPage() {
  const qc = useQueryClient(); const toast = useToast();
  const q = useQuery({ queryKey: ['captureReviews'], queryFn: () => api.captureReviews() });
  const [error, setError] = useState<string | null>(null);
  const { ask, element } = useReasonPrompt(() => ['captureReviews', 'deals', 'pool', 'poolSummary'].forEach((k) => qc.invalidateQueries({ queryKey: [k] })));
  const decide = useMutation({ mutationFn: (v: { id: string; action: CaptureAction }) => api.decideCaptureReview(v.id, v.action), onSuccess: () => { ['captureReviews', 'deals', 'pool', 'poolSummary'].forEach((k) => qc.invalidateQueries({ queryKey: [k] })); toast({ message: 'Done' }); }, onError: (e) => setError(err(e, 'Could not save.')) });
  const run = (r: CaptureReview, action: CaptureAction, title: string) => ask({ title, description: `${r.company ?? 'This lead'} from ${KIND[r.sourceKind] ?? r.sourceKind}.`, action: 'Confirm', danger: action === 'discard', done: 'Done', run: (reason) => api.decideCaptureReview(r.id, action, reason) });
  return <div className="flex flex-col gap-5">
    <PageHeader title="Capture review" subtitle="Leads that arrived on their own and look like a company we already have. Someone decides what happens to each." />
    {error && <Banner tone="danger" title={error} />}
    {q.isError && <Banner tone="danger" title="The review queue could not be loaded">{err(q.error, '')}</Banner>}
    {q.data && q.data.length === 0 && <div className="panel p-6 text-ink-2">Nothing waiting. New website and email leads that look like duplicates will appear here.</div>}
    <ul className="m-0 flex list-none flex-col gap-3 p-0">
      {(q.data ?? []).map((r) => <li key={r.id} className="panel flex flex-col gap-3 p-4">
        <div className="flex flex-wrap items-center gap-2"><span className="text-heading">{r.company ?? 'Unnamed company'}</span><Badge>{KIND[r.sourceKind] ?? r.sourceKind}</Badge><span className="text-xs text-ink-3">{formatRelativeIST(r.createdAt)}</span></div>
        <dl className="m-0 grid gap-x-6 gap-y-1 text-small sm:grid-cols-2">{Object.entries(r.preview).filter(([, v]) => v !== null && v !== '' && typeof v !== 'object').map(([k, v]) => <div key={k} className="flex gap-2"><dt className="min-w-28 text-ink-3">{k.replace(/_/g, ' ')}</dt><dd className="m-0 break-words">{String(v)}</dd></div>)}</dl>
        {r.matchedDealId && <p className="m-0 text-small text-ink-2">Looks like an existing lead: <Link to={`/app/deals/${r.matchedDealId}`} className="text-brand-ink">open it</Link>.</p>}
        <span className="flex flex-wrap gap-2">
          <Button size="sm" variant="primary" onClick={() => run(r, 'create_anyway', 'Create this as a new lead?')}>Create anyway</Button>
          {r.matchedDealId && <Button size="sm" onClick={() => run(r, 'add_to_existing', 'Add this to the existing lead?')}>Add to the existing lead</Button>}
          <Button size="sm" variant="quiet" className="!text-danger" loading={decide.isPending} onClick={() => run(r, 'discard', 'Throw this one away?')}>Discard</Button>
        </span>
      </li>)}
    </ul>
    {element}
  </div>;
}

/** Website forms that send leads straight into Relay. Each source has its own key; the form posts to
 * the address shown. Rotating the key stops the old form working. */
export function CaptureSourcesPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['captureSources'], queryFn: () => api.captureSources() });
  const [name, setName] = useState(''); const [origins, setOrigins] = useState(''); const [lsrc, setLsrc] = useState(''); const [step, setStep] = useState('');
  const [error, setError] = useState<string | null>(null); const [shown, setShown] = useState<{ id: string; key: string } | null>(null);
  const { ask, element } = useReasonPrompt(() => qc.invalidateQueries({ queryKey: ['captureSources'] }));
  const originList = origins.split(',').map((o) => o.trim()).filter(Boolean);
  const create = () => ask({ title: 'Create the website form source?', action: 'Create', done: 'Source created', run: async (reason) => { const r = await api.createCaptureSource({ name: name.trim(), allowedOrigins: originList, defaultLeadSource: lsrc, defaultNextStep: step }, reason); setShown({ id: r.id, key: r.sourceKey }); setName(''); setOrigins(''); setStep(''); setLsrc(''); } });
  return <div className="flex flex-col gap-5">
    <PageHeader title="Lead capture" subtitle="Website forms that send leads straight into Relay, with the duplicate check, the assignment rules and the Lead Pool all applying as for any new lead." />
    {error && <Banner tone="danger" title={error} />}
    {shown && <Banner tone="success" title="Source created">The key for this form is <code className="text-mono">{shown.key}</code>. Point the form at <code className="text-mono break-all">{API_URL}/capture/web/{shown.key}</code>.</Banner>}
    <section className="panel flex flex-col gap-3 p-5"><h2 className="m-0 text-heading">Add a website form</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField label="Name" required value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Contact us page" />
        <TextField label="Websites allowed to send" optionalNote="(comma separated)" value={origins} onChange={(e) => setOrigins(e.target.value)} placeholder="https://www.example.in" />
        <SelectField label="Lead source" value={lsrc} onChange={(e) => setLsrc(e.target.value)} options={[{ value: '', label: 'Website (default)' }, ...LEAD_SOURCES.map((s) => ({ value: s, label: s }))]} />
        <TextField label="First next step" optionalNote="(optional)" value={step} onChange={(e) => setStep(e.target.value)} placeholder="e.g. Call within one working day" />
      </div>
      <Button variant="primary" icon="plus" disabled={!name.trim()} onClick={create} className="self-start">Create the source</Button>
    </section>
    {q.isError && <Banner tone="danger" title="The sources could not be loaded">{err(q.error, '')}</Banner>}
    <ul className="m-0 flex list-none flex-col gap-3 p-0">{(q.data ?? []).map((s) => <Source key={s.id} s={s} onError={setError} onRotated={(key) => { setShown({ id: s.id, key }); qc.invalidateQueries({ queryKey: ['captureSources'] }); }} />)}</ul>
    {element}
  </div>;
}

const SAMPLE = JSON.stringify({ company: 'Test Company', name: 'Test Person', email: 'test@example.com', phone: '+919800000000', consent_text: 'I agree to be contacted' }, null, 2);

function Source({ s, onError, onRotated }: { s: CaptureSource; onError: (m: string | null) => void; onRotated: (key: string) => void }) {
  const [open, setOpen] = useState(false); const [payload, setPayload] = useState(SAMPLE); const [outcome, setOutcome] = useState<string | null>(null);
  const rotate = useMutation({ mutationFn: () => api.rotateCaptureKey(s.id), onSuccess: onRotated, onError: (e) => onError(err(e, 'Could not change the key.')) });
  const test = useMutation({ mutationFn: () => api.testCaptureSource(s.id, JSON.parse(payload) as Record<string, string>), onSuccess: (o) => { setOutcome(o); onError(null); }, onError: (e) => onError(err(e, 'The test could not run: check the JSON.')) });
  return <li className="panel flex flex-col gap-3 p-4">
    <div className="flex flex-wrap items-center gap-2"><span className="text-heading">{s.name}</span><Badge>{KIND[s.kind] ?? s.kind}</Badge>{!s.active && <Badge tone="warning">Off</Badge>}</div>
    {s.sourceKey && <p className="m-0 text-small text-ink-2">The form posts to <code className="text-mono break-all">{API_URL}/capture/web/{s.sourceKey}</code></p>}
    <p className="m-0 text-xs text-ink-3">{s.allowedOrigins.length ? `Allowed: ${s.allowedOrigins.join(', ')}` : 'Any website may send'}{s.defaultLeadSource ? ` · source ${s.defaultLeadSource}` : ''}{s.defaultNextStep ? ` · first step: ${s.defaultNextStep}` : ''}</p>
    <span className="flex flex-wrap gap-2"><Button size="sm" onClick={() => setOpen(!open)}>{open ? 'Hide the test' : 'Send a test lead'}</Button><Button size="sm" variant="quiet" loading={rotate.isPending} onClick={() => rotate.mutate()}>Change the key</Button></span>
    {open && <div className="flex flex-col gap-2 rounded-lg border border-line bg-surface-0 p-3">
      <Banner tone="warning" title="A test creates a real lead">It goes through the same checks as a real submission, then appears in the Lead Pool.</Banner>
      <TextAreaField label="What the form sends (JSON)" rows={7} value={payload} onChange={(e) => setPayload(e.target.value)} className="font-mono" />
      <span className="flex flex-wrap items-center gap-3"><Button size="sm" variant="primary" loading={test.isPending} onClick={() => { setOutcome(null); test.mutate(); }}>Send the test</Button>{outcome && <span className="text-small">Result: <b>{outcome.replace(/_/g, ' ')}</b></span>}</span>
    </div>}
  </li>;
}
