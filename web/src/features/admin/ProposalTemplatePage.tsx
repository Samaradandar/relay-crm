import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { PageHeader } from '@/components/layout/PageHeader';
import { Banner } from '@/components/ui/Feedback';
import { Button } from '@/components/ui/Button';
import { TextAreaField, TextField } from '@/components/ui/Field';
import { useReasonPrompt } from '@/components/ui/ReasonPrompt';

const PLACEHOLDERS: [string, string][] = [
  ['{{company}}', 'the customer company'], ['{{recipient_name}}', 'who the proposal is addressed to'], ['{{scope}}', 'the scope agreed on the deal'],
  ['{{pricing_model}}', 'pricing model'], ['{{plan}}', 'plan'], ['{{expected_minutes_per_month}}', 'expected minutes a month'],
  ['{{items_html}}', 'the priced products as a table'], ['{{total_inr}}', 'the total in rupees'], ['{{contract_term}}', 'contract term'], ['{{billing_frequency}}', 'billing frequency'],
];

/** The wording every proposal draft starts from. Each save is a new version; drafts that already
 * exist keep the text they were made with. */
export function ProposalTemplatePage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['proposalTemplate'], queryFn: () => api.proposalTemplate() });
  const [name, setName] = useState(''); const [body, setBody] = useState(''); const [terms, setTerms] = useState('');
  useEffect(() => { if (q.data) { setName(q.data.name); setBody(q.data.body); setTerms(q.data.termsHtml); } }, [q.data?.id, q.data?.version]); // eslint-disable-line react-hooks/exhaustive-deps
  const { ask, element } = useReasonPrompt(() => qc.invalidateQueries({ queryKey: ['proposalTemplate'] }));
  const changed = !q.data || name !== q.data.name || body !== q.data.body || terms !== q.data.termsHtml;
  const ready = !!name.trim() && !!body.trim() && changed;
  return <div className="flex flex-col gap-5">
    <PageHeader title="Proposal template" subtitle={q.data ? `Live: version ${q.data.version}. Every proposal draft starts from this wording.` : 'No template yet: create one so reps can draft proposals.'}
      actions={<Button variant="primary" icon="check" disabled={!ready} onClick={() => ask({ title: 'Save the proposal template?', description: 'This becomes a new version. Drafts that already exist keep their text.', action: 'Save', done: 'Proposal template saved', run: (reason) => api.saveProposalTemplate({ name: name.trim(), body, termsHtml: terms }, reason) })}>Save template</Button>} />
    {q.isError && <Banner tone="danger" title="The template could not be loaded" />}
    <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_320px]">
      <section className="panel flex flex-col gap-4 p-5">
        <TextField label="Template name" required value={name} onChange={(e) => setName(e.target.value)} />
        <TextAreaField label="Proposal text (HTML)" required rows={14} value={body} onChange={(e) => setBody(e.target.value)} hint="Plain HTML. Placeholders in double curly brackets are filled from the deal." />
        <TextAreaField label="Terms (HTML)" rows={5} value={terms} onChange={(e) => setTerms(e.target.value)} />
      </section>
      <aside className="panel flex flex-col gap-2 p-4 xl:sticky xl:top-[72px]"><h2 className="m-0 text-heading">Placeholders</h2>
        <ul className="m-0 flex list-none flex-col gap-1.5 p-0 text-small">{PLACEHOLDERS.map(([k, v]) => <li key={k} className="flex flex-col"><code className="text-mono text-brand-ink">{k}</code><span className="text-xs text-ink-3">{v}</span></li>)}</ul>
        <h2 className="m-0 mt-3 text-heading">Preview</h2>
        <iframe title="Template preview" sandbox="" srcDoc={`<body style="font-family:Arial,sans-serif;font-size:13px;padding:10px">${body}<hr>${terms}</body>`} className="h-64 w-full rounded-md border border-line bg-white" />
      </aside>
    </div>
    {element}
  </div>;
}
