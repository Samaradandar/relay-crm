import clsx from 'clsx';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError, LEAD_SOURCES, type CsvCounts, type CsvRunResult, type CsvUpload } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/Button';
import { Banner } from '@/components/ui/Feedback';
import { SelectField, TextAreaField } from '@/components/ui/Field';

const FIELDS: { key: string; label: string; need?: string }[] = [
  { key: 'company', label: 'Company', need: 'required' }, { key: 'contact_name', label: 'Contact name', need: 'required' },
  { key: 'phone', label: 'Phone', need: 'phone or email' }, { key: 'email', label: 'Email', need: 'phone or email' },
  { key: 'lead_source', label: 'Lead source', need: 'column or a default below' }, { key: 'role', label: 'Contact role' },
  { key: 'website', label: 'Website' }, { key: 'industry', label: 'Industry' }, { key: 'city', label: 'City' },
  { key: 'next_step', label: 'Next step' }, { key: 'due_date', label: 'Next step due date' }, { key: 'first_contact_date', label: 'First contact date' }, { key: 'notes', label: 'Notes' },
];
const norm = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, '');
const ALIASES: Record<string, string[]> = { company: ['company', 'companyname', 'account', 'organisation', 'organization', 'business'], contact_name: ['contact', 'contactname', 'name', 'person', 'fullname'], phone: ['phone', 'mobile', 'phonenumber', 'contactnumber', 'tel'], email: ['email', 'emailid', 'emailaddress', 'mail'], lead_source: ['source', 'leadsource'], role: ['role', 'title', 'designation', 'contactrole'], website: ['website', 'site', 'url', 'domain'], industry: ['industry', 'sector'], city: ['city', 'location', 'town'], next_step: ['nextstep'], due_date: ['duedate', 'nextstepdue'], first_contact_date: ['firstcontactdate', 'firstcontact'], notes: ['notes', 'remarks', 'comments'] };
const guess = (headers: string[]) => Object.fromEntries(FIELDS.flatMap((f) => { const h = headers.find((x) => ALIASES[f.key].includes(norm(x))); return h ? [[f.key, h]] : []; }));
const err = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

/** Bulk import of leads from a CSV file: pick the file, say which column is which, see how the rows
 * would land (new, duplicate, needs a look, unusable), then import. Duplicates and unusable rows
 * are never created, and a report says what happened to every row. */
export function ImportCsvPage() {
  const { me } = useAuth(); const qc = useQueryClient();
  const teams = useQuery({ queryKey: ['teams'], queryFn: api.teams, enabled: me?.role !== 'team_leader' });
  const [file, setFile] = useState<File | null>(null); const [team, setTeam] = useState('');
  const [up, setUp] = useState<CsvUpload | null>(null);
  const [map, setMap] = useState<Record<string, string>>({}); const [source, setSource] = useState('');
  const [counts, setCounts] = useState<CsvCounts | null>(null); const [result, setResult] = useState<CsvRunResult | null>(null);
  const [reason, setReason] = useState(''); const [error, setError] = useState<string | null>(null);

  const upload = useMutation({ mutationFn: () => api.uploadCsv(file!, team || undefined), onSuccess: (u) => { setUp(u); setMap(guess(u.headers)); setCounts(null); setError(null); }, onError: (e) => setError(err(e, 'Could not read the file.')) });
  const check = useMutation({ mutationFn: () => api.setCsvMapping(up!.id, map, source), onSuccess: (c) => { setCounts(c); setError(null); }, onError: (e) => setError(err(e, 'Could not check the file.')) });
  const run = useMutation({ mutationFn: () => api.runCsv(up!.id, reason), onSuccess: (r) => { setResult(r); setError(null); ['deals', 'pool', 'poolSummary', 'teamLoad', 'myLeads'].forEach((k) => qc.invalidateQueries({ queryKey: [k] })); }, onError: (e) => setError(err(e, 'The import did not finish.')) });
  const download = useMutation({ mutationFn: async () => { const text = await api.csvReport(up!.id); const url = URL.createObjectURL(new Blob([text], { type: 'text/csv' })); const a = document.createElement('a'); a.href = url; a.download = 'import-report.csv'; a.click(); URL.revokeObjectURL(url); } });

  const mapped = (k: string) => !!map[k];
  const enough = mapped('company') && mapped('contact_name') && (mapped('phone') || mapped('email')) && (mapped('lead_source') || !!source);
  const reasonNeeded = me?.role === 'admin';
  const reset = () => { setFile(null); setUp(null); setMap({}); setCounts(null); setResult(null); setReason(''); setError(null); upload.reset(); };

  return <div className="flex flex-col gap-5">
    <PageHeader title="Import leads from a CSV file" subtitle="Up to 5,000 rows and 5 MB. Duplicates and unusable rows are skipped, and a report shows what happened to every row." />
    {error && <Banner tone="danger" title={error} />}

    <Card n={1} title="Choose the file" done={!!up}>
      {up ? <p className="m-0 text-small text-ink-2">{file?.name}: {up.rowCount} rows, {up.headers.length} columns. <button className="text-brand-ink underline" onClick={reset}>Choose another file</button></p>
        : <div className="flex flex-col gap-3">
          <input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="text-small file:mr-3 file:h-9 file:rounded-md file:border file:border-line-control file:bg-surface-1 file:px-3 file:text-[13px] file:font-semibold" aria-label="CSV file" />
          {me?.role !== 'team_leader' && <SelectField label="Team the leads go to" optionalNote="(optional: otherwise the assignment rules decide)" value={team} onChange={(e) => setTeam(e.target.value)} options={[{ value: '', label: 'Let the assignment rules decide' }, ...(teams.data ?? []).map((t) => ({ value: t.id, label: t.name }))]} className="max-w-sm" />}
          <Button variant="primary" icon="file" disabled={!file} loading={upload.isPending} onClick={() => { setError(null); upload.mutate(); }} className="self-start">Read the file</Button>
        </div>}
    </Card>

    <Card n={2} title="Which column is which?" done={!!counts} locked={!up}>
      {up && !result && <div className="flex flex-col gap-3">
        <div className="grid gap-3 sm:grid-cols-2">
          {FIELDS.map((f) => <SelectField key={f.key} label={f.label} optionalNote={f.need ? `(${f.need})` : undefined} value={map[f.key] ?? ''} onChange={(e) => { setMap((m) => { const n = { ...m }; if (e.target.value) n[f.key] = e.target.value; else delete n[f.key]; return n; }); setCounts(null); }} options={[{ value: '', label: 'Not in the file' }, ...up.headers.map((h) => ({ value: h, label: h }))]} />)}
        </div>
        <SelectField label="Lead source for rows that have none" optionalNote="(needed if there is no lead source column)" value={source} onChange={(e) => { setSource(e.target.value); setCounts(null); }} options={[{ value: '', label: 'None' }, ...LEAD_SOURCES.map((s) => ({ value: s, label: s }))]} className="max-w-sm" />
        {!enough && <p className="m-0 text-small text-ink-3">Match at least the company, the contact name, a phone or an email, and a lead source (a column or the default).</p>}
        <Button icon="check" disabled={!enough} loading={check.isPending} onClick={() => { setError(null); check.mutate(); }} className="self-start">Check the file</Button>
      </div>}
    </Card>

    <Card n={3} title="Import" done={!!result} locked={!counts}>
      {counts && !result && <div className="flex flex-col gap-3">
        <dl className="m-0 grid gap-3 sm:grid-cols-5">
          {([['ready', 'Ready to create'], ['duplicate', 'Duplicates (skipped)'], ['needs_review', 'Need a look'], ['existing_customer', 'Existing customers'], ['invalid', 'Unusable (skipped)']] as const).map(([k, label]) => <div key={k} className={clsx('rounded-lg border p-3', k === 'ready' ? 'border-brand bg-brand-soft' : 'border-line bg-surface-0')}><dt className="text-xs font-semibold text-ink-3">{label}</dt><dd className="m-0 text-metric">{counts[k] ?? 0}</dd></div>)}
        </dl>
        <p className="m-0 text-small text-ink-2">{counts.ready} of {counts.total} rows will become leads, assigned like any new lead. Rows that need a look are not created automatically; the report lists them.</p>
        <TextAreaField label="Reason" required={reasonNeeded} optionalNote={reasonNeeded ? undefined : '(optional)'} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Leads from the Pune expo, 12 Oct" />
        <Button variant="primary" icon="send" disabled={counts.ready === 0 || (reasonNeeded && !reason.trim())} loading={run.isPending} onClick={() => { setError(null); run.mutate(); }} className="self-start">Import {counts.ready} leads</Button>
      </div>}
      {result && <div className="flex flex-col gap-3">
        <Banner tone="success" title={`${result.created} leads created from ${result.processed} rows checked`}>Duplicates and unusable rows were skipped. Download the report for what happened to each row.</Banner>
        <span className="flex flex-wrap gap-2"><Button icon="file" loading={download.isPending} onClick={() => download.mutate()}>Download the report</Button><Link to="/app/pool" className="inline-flex h-9 items-center rounded-md border border-line-control bg-surface-1 px-3.5 text-sm font-semibold text-ink no-underline hover:bg-surface-3">Open the Lead Pool</Link><Button variant="quiet" onClick={reset}>Import another file</Button></span>
      </div>}
    </Card>
  </div>;
}

function Card({ n, title, done, locked, children }: { n: number; title: string; done: boolean; locked?: boolean; children: React.ReactNode }) {
  return <section className={clsx('panel flex flex-col gap-3 p-5', locked && 'opacity-60')}>
    <h2 className="m-0 flex items-center gap-2 text-heading"><span className={clsx('grid h-6 w-6 place-items-center rounded-full text-[12px] font-semibold', done ? 'bg-brand text-brand-on' : 'bg-surface-2 text-ink-2')}>{n}</span>{title}</h2>
    {children}
  </section>;
}
