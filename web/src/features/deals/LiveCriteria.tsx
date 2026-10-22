import clsx from 'clsx';
import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api, type TickProof } from '@/api';
import { ApiError, type Activity, type CriterionState, type Lead, type LiveCriterion } from '@/api/types';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Banner, useToast } from '@/components/ui/Feedback';
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { Icon } from '@/components/ui/Icon';
import { formatDateTimeIST, formatRelativeIST } from '@/lib/format';
import { MEETING_STATUS_LABEL, isMeeting } from '@/lib/meetings';
import { ENTRY_DOCS, fromApiValue, liveProof, toApiValue, type DocKind, type LiveProof } from '@/lib/liveCriteria';
import { ACTIVITY_LABEL, nextStage, type FieldDef } from '@/lib/pipeline';
import { ReasonDialog } from '@/features/team/TeamBits';

const DONE = ['ticked', 'verified', 'overridden'];
const stateOf = (d: Lead, key: string): CriterionState => d.criteria?.[key] ?? { state: 'open' };
const hasDoc = (d: Lead, kind: DocKind, status: string[]) => (d.documents ?? []).some((x) => x.kind === kind && status.includes((x.status ?? '').toLowerCase()));

/** The stage entry document the next forward move needs, if any (e.g. a proposal before Proposal). */
function entryDoc(d: Lead) {
  const next = d.pipeline && d.stage ? nextStage(d.pipeline, d.stage) : undefined;
  return next ? ENTRY_DOCS[next.key] : undefined;
}

/** How far the current stage is, counting the backend's criteria plus a needed entry document. */
export function liveProgress(d: Lead) {
  const list = d.liveCriteria ?? [];
  const e = entryDoc(d);
  const total = list.length + (e ? 1 : 0);
  const done = list.filter((c) => DONE.includes(stateOf(d, c.key).state)).length + (e && hasDoc(d, e.docKind, e.status) ? 1 : 0);
  return { total, done };
}

/** Live mode exit criteria: the backend's list for this stage, each with the right proof form. */
export function LiveCriteriaList({ d, activities, editable, canVerify, onChanged, onLog }: { d: Lead; activities: Activity[]; editable: boolean; canVerify: boolean; onChanged: () => void; onLog: (k: Activity['kind'], meeting?: 'booked' | 'held') => void }) {
  const e = entryDoc(d);
  return <ul className="m-0 flex list-none flex-col overflow-hidden rounded-lg border border-line p-0">
    {(d.liveCriteria ?? []).map((c) => <LiveRow key={c.key} d={d} c={c} activities={activities} editable={editable} canVerify={canVerify} onChanged={onChanged} onLog={onLog} />)}
    {e && <EntryDocRow d={d} kind={e.docKind} status={e.status} label={e.label} editable={editable} onChanged={onChanged} />}
  </ul>;
}

/** Which meetings back a criterion: the booked-or-held one for scheduling, the held one for the rest. */
const MEETING_PROOF: Record<string, Array<'booked' | 'held'>> = { meeting_scheduled: ['booked', 'held'], meeting_completed: ['held'], walkthrough_logged: ['held'] };

/** Who attended and what was said at the meeting behind a criterion, shown right under it. */
function MeetingDetails({ activities, statuses }: { activities: Activity[]; statuses: Array<'booked' | 'held'> }) {
  const m = activities.filter(isMeeting).filter((a) => statuses.includes(a.meetingStatus as 'booked' | 'held')).sort((a, b) => Date.parse(b.at) - Date.parse(a.at))[0];
  if (!m) return null;
  const extra: Array<[string, string | undefined]> = [['Requirements', m.requirements], ['Objections', m.objections], ['Decisions', m.decisions], ['Actions', m.actions]];
  return <div className="ml-[34px] flex flex-col gap-1 rounded-lg border border-line bg-surface-0 p-3 text-small">
    <span className="flex flex-wrap items-center gap-2"><Badge tone={m.meetingStatus === 'held' ? 'success' : 'brand'}>{MEETING_STATUS_LABEL[m.meetingStatus ?? 'held']}</Badge><span className="text-body-strong">{formatDateTimeIST(m.at)}</span>{m.byName && <span className="text-xs text-ink-3">logged by {m.byName}</span>}</span>
    <span><span className="text-ink-3">Attended by: </span>{m.attendees || 'not recorded yet'}</span>
    {m.discussed && <span><span className="text-ink-3">Discussed: </span>{m.discussed}</span>}
    {extra.filter(([, v]) => !!v).map(([k, v]) => <span key={k}><span className="text-ink-3">{k}: </span>{v}</span>)}
    {m.nextStep && <span><span className="text-ink-3">Next step: </span>{m.nextStep}{m.followUp ? ` (by ${m.followUp})` : ''}</span>}
  </div>;
}

function LiveRow({ d, c, activities, editable, canVerify, onChanged, onLog }: { d: Lead; c: LiveCriterion; activities: Activity[]; editable: boolean; canVerify: boolean; onChanged: () => void; onLog: (k: Activity['kind'], meeting?: 'booked' | 'held') => void }) {
  const s = stateOf(d, c.key); const done = DONE.includes(s.state); const proof = liveProof(c.key, c.proofType);
  const [openForm, setOpenForm] = useState(false); const [dialog, setDialog] = useState<null | 'untick' | 'reject'>(null); const [error, setError] = useState<string | null>(null);
  const toast = useToast();
  const fail = (e: unknown) => setError(e instanceof ApiError ? e.message : 'Could not save.');
  const quick = useMutation({ mutationFn: () => api.tickCriterion(d.id, c.key, {}), onSuccess: () => { toast({ message: `Ticked: ${c.label}` }); onChanged(); }, onError: fail });
  const verify = useMutation({ mutationFn: (v: { d: 'verified' | 'rejected'; reason?: string }) => api.verifyTick(d.id, c.key, v.d, v.reason),
    onSuccess: (_x, v) => { setDialog(null); toast({ message: v.d === 'verified' ? `Verified: ${c.label}` : `Rejected. ${d.ownerName} has been told.` }); onChanged(); }, onError: (e) => { setDialog(null); fail(e); } });
  const untick = useMutation({ mutationFn: (reason: string) => api.untickCriterion(d.id, c.key, reason), onSuccess: () => { setDialog(null); onChanged(); }, onError: (e) => { setDialog(null); fail(e); } });
  return <li className="flex flex-col gap-2 border-t border-line bg-surface-1 px-3 py-3 first:border-t-0">
    <div className="grid grid-cols-[22px_minmax(0,1fr)_auto] items-start gap-3">
      <span aria-hidden className={clsx('mt-0.5 grid h-5 w-5 place-items-center rounded-sm border-[1.5px]', done ? 'border-brand bg-brand text-brand-on' : s.state === 'proof_missing' ? 'border-danger' : 'border-line-control')}>{done && <Icon name="check" size={14} />}</span>
      <span className="flex min-w-0 flex-col"><span className="text-[14px] font-medium">{c.label}</span>
        {done || s.state === 'proof_missing' ? <span className="text-small text-ink-2">{s.by && <>Ticked by {s.by}{s.at ? `, ${formatRelativeIST(s.at)}` : ''}</>}</span>
          : c.missing.length > 0 && <span className="text-small text-ink-3">Still needed: {c.missing.join('; ')}</span>}</span>
      <span className="flex flex-wrap items-center justify-end gap-2">
        {s.state === 'verified' && <Badge tone="success" icon="check">Verified</Badge>}
        {s.state === 'overridden' && <Badge>Overridden</Badge>}
        {s.state === 'ticked' && <Badge>Awaiting check</Badge>}
        {s.state === 'proof_missing' && <Badge tone="danger" icon="alert">Proof missing</Badge>}
        {editable && !done && proof.kind === 'confirm' && <Button size="sm" variant="secondary" icon="check" disabled={!c.ready} loading={quick.isPending} onClick={() => { setError(null); quick.mutate(); }}>Tick</Button>}
        {editable && !done && proof.kind !== 'confirm' && !openForm && <Button size="sm" variant="secondary" icon="clip" onClick={() => { setError(null); setOpenForm(true); }}>Add proof</Button>}
        {editable && (done || s.state === 'proof_missing') && s.state !== 'overridden' && <Button size="sm" variant="quiet" onClick={() => setDialog('untick')}>Untick</Button>}
        {canVerify && s.state === 'ticked' && <><Button size="sm" variant="quiet" className="!text-danger" onClick={() => setDialog('reject')}>Reject</Button><Button size="sm" icon="check" loading={verify.isPending} onClick={() => { setError(null); verify.mutate({ d: 'verified' }); }}>Verify</Button></>}
      </span>
    </div>
    {MEETING_PROOF[c.key] && <MeetingDetails activities={activities} statuses={MEETING_PROOF[c.key]} />}
    {error && <div className="ml-[34px]"><Banner tone="danger" title={error} /></div>}
    {openForm && <LiveProofForm d={d} criterionKey={c.key} proof={proof} activities={activities} onCancel={() => setOpenForm(false)} onDone={() => { setOpenForm(false); toast({ message: `Ticked: ${c.label}` }); onChanged(); }} onLog={onLog} />}
    {dialog === 'untick' && <ReasonDialog title={`Untick “${c.label}”?`} action="Untick" busy={untick.isPending} onClose={() => setDialog(null)} onConfirm={(r) => untick.mutate(r)} />}
    {dialog === 'reject' && <ReasonDialog title={`Reject “${c.label}”?`} description={`The tick is removed and ${d.ownerName} is notified.`} action="Reject tick" danger busy={verify.isPending} onClose={() => setDialog(null)} onConfirm={(reason) => verify.mutate({ d: 'rejected', reason })} />}
  </li>;
}

function LiveProofForm({ d, criterionKey, proof, activities, onCancel, onDone, onLog }: { d: Lead; criterionKey: string; proof: LiveProof; activities: Activity[]; onCancel: () => void; onDone: () => void; onLog: (k: Activity['kind'], meeting?: 'booked' | 'held') => void }) {
  const fields: FieldDef[] = 'fields' in proof && proof.fields ? proof.fields : [];
  const [vals, setVals] = useState<Record<string, string>>(() => Object.fromEntries(fields.map((f) => [f.key, fromApiValue(f, d.liveValues?.[f.key])])));
  const [pick, setPick] = useState('');
  const [fit, setFit] = useState<Record<string, boolean>>(() => Object.fromEntries((d.fit ?? []).map((i) => [i.id, i.ticked])));
  const [contact, setContact] = useState(() => d.contacts?.find((c) => c.decisionMaker)?.id ?? '');
  const [product, setProduct] = useState('');
  const dmContacts = d.contacts ?? []; const firstDm = dmContacts.find((c) => c.decisionMaker) ?? dmContacts[0];
  const [dmSel, setDmSel] = useState(firstDm?.id ?? 'new');
  const [dm, setDm] = useState(() => ({ name: firstDm?.name ?? '', phone: firstDm?.phone ?? '', email: firstDm?.email ?? '', role: firstDm?.role ?? '' }));
  const pickDm = (id: string) => { setDmSel(id); const c = dmContacts.find((x) => x.id === id); setDm(c ? { name: c.name, phone: c.phone ?? '', email: c.email ?? '', role: c.role ?? '' } : { name: '', phone: '', email: '', role: '' }); };
  const [file, setFile] = useState<File | null>(null);
  const [status, setStatus] = useState(proof.kind === 'document' ? proof.status[0] : '');
  const [error, setError] = useState<string | null>(null);
  const products = useQuery({ queryKey: ['catalog'], queryFn: () => api.catalog(), enabled: proof.kind === 'lines' });

  const now = Date.now();
  const options = proof.kind === 'activity' ? activities.filter((a) => proof.activity.includes(a.kind) && (!proof.twoWay || a.twoWay)
    && (proof.when !== 'future' || Date.parse(a.at) > now) && (proof.when !== 'past' || Date.parse(a.at) <= now)
    && (!proof.status || proof.status.includes((a.meetingStatus ?? 'held') as 'booked' | 'held'))) : [];
  const fieldsReady = fields.every((f) => vals[f.key]?.trim());
  const ready = fieldsReady && (proof.kind === 'activity' ? !!pick
    : proof.kind === 'checklist' ? (d.fit ?? []).length > 0 && (d.fit ?? []).every((i) => fit[i.id])
    : proof.kind === 'contact_tag' ? !!contact
    : proof.kind === 'decision_maker' ? !!(dm.name.trim() && dm.phone.trim() && /^\S+@\S+\.\S+$/.test(dm.email.trim()))
    : proof.kind === 'document' ? (!proof.file || !!file)
    : true);

  const tick = useMutation({
    mutationFn: () => {
      const body: TickProof = {};
      if (fields.length) body.patch = Object.fromEntries(fields.map((f) => [f.key, toApiValue(f, vals[f.key])]));
      if (proof.kind === 'activity') body.activityId = pick;
      if (proof.kind === 'checklist') body.fit = fit;
      if (proof.kind === 'contact_tag' && !d.contacts?.find((c) => c.id === contact)?.decisionMaker) body.decisionMakerId = contact;
      if (proof.kind === 'lines' && product) body.productId = product;
      if (proof.kind === 'decision_maker') body.decisionMaker = { accountId: d.accountId, contactId: dmSel === 'new' ? undefined : dmSel, ...dm };
      if (proof.kind === 'document') body.document = { kind: proof.docKind, status, file: file ?? undefined };
      return api.tickCriterion(d.id, criterionKey, body);
    },
    onSuccess: onDone, onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not tick it.'),
  });

  return <div className="ml-[34px] flex flex-col gap-3 rounded-md border border-line-control bg-surface-0 p-3">
    {proof.kind === 'activity' && (options.length === 0
      ? (proof.status
        ? <div className="flex flex-col gap-2 text-small text-ink-2"><span>{proof.status.length === 1 && activities.some((a) => a.meetingStatus === 'booked') ? 'A meeting is booked. Once it has happened, open the Meetings panel and choose “Meeting held”.' : `No matching meeting yet. ${proof.explain}`}</span>
          {!(proof.status.length === 1 && activities.some((a) => a.meetingStatus === 'booked')) && <span className="flex flex-wrap gap-2">{proof.status.includes('booked') && <Button size="sm" icon="calendar" onClick={() => onLog('meeting', 'booked')}>Book a meeting</Button>}<Button size="sm" icon="file" onClick={() => onLog('meeting', 'held')}>Log a meeting that was held</Button></span>}</div>
        : <div className="flex flex-wrap items-center justify-between gap-2 text-small text-ink-2"><span>No matching activity yet. {proof.explain}</span><Button size="sm" icon="file" onClick={() => onLog(proof.activity[0])}>Log {ACTIVITY_LABEL[proof.activity[0]].toLowerCase()} now</Button></div>)
      : <fieldset className="m-0 flex flex-col gap-1.5 border-0 p-0"><legend className="mb-1 text-[13px] font-semibold">Pick the activity that proves it</legend>{options.slice(0, 5).map((a) => <label key={a.id} className="flex cursor-pointer items-start gap-2 text-small"><input type="radio" name={'p' + criterionKey} checked={pick === a.id} onChange={() => setPick(a.id)} className="mt-1 accent-[var(--brand)]" /><span><b>{ACTIVITY_LABEL[a.kind]}</b> · {formatRelativeIST(a.at)}<span className="block text-ink-2">{a.discussed}</span></span></label>)}</fieldset>)}
    {proof.kind === 'checklist' && ((d.fit ?? []).length === 0
      ? <p className="m-0 text-small text-ink-2">The fit checklist has no items yet. Ask the Super admin to add them.</p>
      : <fieldset className="m-0 flex flex-col gap-1.5 border-0 p-0"><legend className="mb-1 text-[13px] font-semibold">Fit checklist</legend>{d.fit!.map((i) => <label key={i.id} className="flex cursor-pointer items-start gap-2 text-small"><input type="checkbox" checked={!!fit[i.id]} onChange={(e) => setFit((x) => ({ ...x, [i.id]: e.target.checked }))} className="mt-1 accent-[var(--brand)]" /><span>{i.label}</span></label>)}</fieldset>)}
    {proof.kind === 'contact_tag' && <SelectField label="Decision maker (the contact who signs)" required value={contact} onChange={(e) => setContact(e.target.value)} options={[{ value: '', label: 'Choose a contact…' }, ...(d.contacts ?? []).map((c) => ({ value: c.id, label: `${c.name}${c.role ? ` (${c.role})` : ''}` }))]} />}
    {proof.kind === 'decision_maker' && <div className="flex flex-col gap-3">
      <SelectField label="Who is the decision maker?" required value={dmSel} onChange={(e) => pickDm(e.target.value)} options={[...dmContacts.map((c) => ({ value: c.id, label: `${c.name}${c.role ? ` (${c.role})` : ''}` })), { value: 'new', label: 'Someone else (add a new contact)' }]} />
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField label="Name" required value={dm.name} onChange={(e) => setDm((x) => ({ ...x, name: e.target.value }))} />
        <TextField label="Role" optionalNote="(optional)" value={dm.role} onChange={(e) => setDm((x) => ({ ...x, role: e.target.value }))} placeholder="Owner, Ops Head…" />
        <TextField label="Phone" required type="tel" value={dm.phone} onChange={(e) => setDm((x) => ({ ...x, phone: e.target.value }))} placeholder="+91 98110 22345" />
        <TextField label="Email" required type="email" value={dm.email} onChange={(e) => setDm((x) => ({ ...x, email: e.target.value }))} error={dm.email && !/^\S+@\S+\.\S+$/.test(dm.email.trim()) ? 'Check the email address.' : undefined} />
      </div>
      <p className="m-0 text-xs text-ink-3">The proposal is emailed to this person, so their name, phone and email are all needed.</p>
    </div>}
    {proof.kind === 'lines' && <SelectField label="Add a product from the catalog" hint="Leave empty if the deal already has a product line." value={product} onChange={(e) => setProduct(e.target.value)} options={[{ value: '', label: 'No new line' }, ...(products.data ?? []).filter((p) => p.active).map((p) => ({ value: p.id, label: p.name }))]} />}
    {proof.kind === 'document' && <div className="grid gap-3 sm:grid-cols-2">
      <label className="flex flex-col gap-1.5 text-[13px] font-semibold">File{proof.file ? '' : ' (optional)'}<input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="text-small font-normal file:mr-3 file:h-8 file:rounded-md file:border file:border-line-control file:bg-surface-1 file:px-3 file:text-[13px] file:font-semibold" /></label>
      {proof.status.length > 1 && <SelectField label="Status" value={status} onChange={(e) => setStatus(e.target.value)} options={proof.status.map((x) => ({ value: x, label: x.charAt(0).toUpperCase() + x.slice(1) }))} />}
    </div>}
    {fields.length > 0 && <div className="grid gap-3 sm:grid-cols-2">{fields.map((f) => <LiveField key={f.key} f={f} d={d} value={vals[f.key] ?? ''} onChange={(v) => setVals((x) => ({ ...x, [f.key]: v }))} />)}</div>}
    {error && <Banner tone="danger" title={error} />}
    <div className="flex justify-end gap-2"><Button size="sm" variant="quiet" onClick={onCancel}>Cancel</Button><Button size="sm" variant="primary" icon="check" disabled={!ready} loading={tick.isPending} onClick={() => { setError(null); tick.mutate(); }}>Tick with this proof</Button></div>
  </div>;
}

function LiveField({ f, d, value, onChange }: { f: FieldDef; d: Lead; value: string; onChange: (v: string) => void }) {
  if (f.type === 'longtext') return <TextAreaField label={f.label} required rows={2} value={value} onChange={(e) => onChange(e.target.value)} placeholder={f.placeholder} className="sm:col-span-2" />;
  if (f.type === 'select') return <SelectField label={f.label} required value={value} onChange={(e) => onChange(e.target.value)} options={[{ value: '', label: 'Choose…' }, ...(f.options ?? []).map((o) => ({ value: o, label: o }))]} />;
  if (f.type === 'contact') return <SelectField label={f.label} required value={value} onChange={(e) => onChange(e.target.value)} options={[{ value: '', label: 'Choose a contact…' }, ...(d.contacts ?? []).map((c) => ({ value: c.name, label: `${c.name}${c.role ? ` (${c.role})` : ''}` }))]} />;
  return <TextField label={f.label} required type={f.type === 'date' ? 'date' : f.type === 'number' || f.type === 'money' ? 'number' : 'text'} value={value} onChange={(e) => onChange(e.target.value)} placeholder={f.placeholder} min={f.type === 'money' || f.type === 'number' ? 0 : undefined} />;
}

/** A document the backend checks on entry to the next stage (not a criterion, so nothing to tick). */
function EntryDocRow({ d, kind, status, label, editable, onChanged }: { d: Lead; kind: DocKind; status: string[]; label: string; editable: boolean; onChanged: () => void }) {
  const done = hasDoc(d, kind, status); const toast = useToast();
  const [open, setOpen] = useState(false); const [file, setFile] = useState<File | null>(null); const [st, setSt] = useState(status[status.length - 1]); const [error, setError] = useState<string | null>(null);
  const add = useMutation({ mutationFn: () => api.addDocument(d.id, { kind, status: st, file: file ?? undefined }), onSuccess: () => { setOpen(false); toast({ message: `${kind.charAt(0).toUpperCase() + kind.slice(1)} added` }); onChanged(); }, onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not add it.') });
  return <li className="flex flex-col gap-2 border-t border-line bg-surface-1 px-3 py-3 first:border-t-0">
    <div className="grid grid-cols-[22px_minmax(0,1fr)_auto] items-start gap-3">
      <span aria-hidden className={clsx('mt-0.5 grid h-5 w-5 place-items-center rounded-sm border-[1.5px]', done ? 'border-brand bg-brand text-brand-on' : 'border-line-control')}>{done && <Icon name="check" size={14} />}</span>
      <span className="text-[14px] font-medium">{label}</span>
      {editable && !done && !open && <Button size="sm" variant="secondary" icon="plus" onClick={() => setOpen(true)}>Add {kind}</Button>}
    </div>
    {open && <div className="ml-[34px] flex flex-col gap-3 rounded-md border border-line-control bg-surface-0 p-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1.5 text-[13px] font-semibold">File (optional)<input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="text-small font-normal" /></label>
        <SelectField label="Status" value={st} onChange={(e) => setSt(e.target.value)} options={status.map((x) => ({ value: x, label: x.charAt(0).toUpperCase() + x.slice(1) }))} />
      </div>
      {error && <Banner tone="danger" title={error} />}
      <div className="flex justify-end gap-2"><Button size="sm" variant="quiet" onClick={() => setOpen(false)}>Cancel</Button><Button size="sm" variant="primary" icon="check" loading={add.isPending} onClick={() => { setError(null); add.mutate(); }}>Add {kind}</Button></div>
    </div>}
  </li>;
}
