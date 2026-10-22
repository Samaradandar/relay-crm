import clsx from 'clsx';
import { useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError, type Activity, type CriterionState, type Lead, type TimelineEvent } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { Avatar, Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Banner, Tabs, useToast } from '@/components/ui/Feedback';
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { Icon, type IconName } from '@/components/ui/Icon';
import { Dialog } from '@/components/ui/Overlay';
import { formatDateTimeIST, formatDueIST, formatINR, formatRelativeIST, todayIST } from '@/lib/format';
import { howAssigned } from '@/lib/leads';
import { ACTIVITY_LABEL, PIPELINES, nextStage, stageOf, stagePath, type CriterionDef, type FieldDef } from '@/lib/pipeline';
import { HealthBadge, RoundBadge, StageBar, inStageText, valueText } from './bits';
import { AssignTaskDialog, ReasonDialog } from '@/features/team/TeamBits';
import { useLogActivity } from './LogActivityDrawer';
import { LiveCriteriaList, liveProgress } from './LiveCriteria';
import { ContactTypeBadge, ContactTypeSwitch } from './ContactType';
import { MeetingPanel } from './MeetingPanel';
import { StageHistory } from './StageHistory';
import { ProposalPanel, RevisionTerms } from './ProposalPanel';
import { useLostReasons } from '@/lib/useLostReasons';

const DONE = ['ticked', 'verified', 'overridden'];
const isDone = (s?: CriterionState) => !!s && DONE.includes(s.state);

/** Feature 3 + Feature 2 for the owner: one page, two columns. Left = what to do; right = what happened. */
export function DealPage() {
  const { id = '' } = useParams(); const { me } = useAuth(); const qc = useQueryClient(); const toast = useToast(); const log = useLogActivity();
  const q = useQuery({ queryKey: ['deal', id], queryFn: () => api.deal(id) });
  const [error, setError] = useState<string | null>(null);
  const [viewStage, setViewStage] = useState<string | null>(null); // an earlier stage being looked at
  const [dialog, setDialog] = useState<null | 'lost' | 'back' | 'round2' | 'override' | 'reopen' | 'reassign' | 'task' | 'delete'>(null);
  const nav = useNavigate();
  const refresh = () => ['deal', 'deals', 'today', 'proposalRounds'].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const move = useMutation({ mutationFn: (o: { to?: string; reason?: string; round2?: boolean }) => api.moveDeal(id, o), onSuccess: (d) => { refresh(); setDialog(null); setError(null); toast({ message: d.status === 'won' ? `${d.company} is Won` : `Moved to ${stageOf(d.pipeline!, d.stage!)?.label}` }); }, onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not move the deal.') });
  const lead = useMutation({ mutationFn: (v: { kind: 'override' | 'reopen' | 'reassign'; reason: string; to?: string; userId?: string }) => v.kind === 'override' ? api.overrideMove(id, v.reason, v.to) : v.kind === 'reopen' ? api.reopenDeal(id, v.reason) : api.reassignDeal(id, v.userId!, v.reason),
    onSuccess: (d, v) => { refresh(); ['needsYou', 'teamOverview', 'repProfile'].forEach((k) => qc.invalidateQueries({ queryKey: [k] })); setDialog(null); setError(null); toast({ message: v.kind === 'override' ? `Override: moved to ${stageOf(d.pipeline!, d.stage!)?.label}` : v.kind === 'reopen' ? 'Deal reopened' : `Owner changed to ${d.ownerName}` }); },
    onError: (e) => { setDialog(null); setError(e instanceof ApiError ? e.message : 'Could not save.'); } });
  const lose = useMutation({ mutationFn: (v: { reason: string; notes: string }) => api.loseDeal(id, v.reason, v.notes), onSuccess: () => { refresh(); setDialog(null); toast({ message: 'Closed as Lost' }); } });

  if (q.isError) return <Banner tone="danger" title="This deal is not available">{(q.error as Error).message}</Banner>;
  if (!q.data) return <div className="panel h-64 animate-pulse" />;
  const { deal: d, activities, events } = q.data;
  const p = d.pipeline ?? 'new_business'; const st = stageOf(p, d.stage!)!; const next = nextStage(p, d.stage!);
  const mine = d.ownerId === me?.id; const open = d.status === 'open';
  // Team Leaders (their team) and Admins (all) supervise; never on their own deals (PRD 10)
  const lead_ = !mine && (me?.role === 'team_leader' || me?.role === 'admin' || me?.role === 'super_admin');
  const proofMissing = Object.values(d.criteria ?? {}).some((c) => c.state === 'proof_missing');
  const states = st.criteria.map((c) => ({ c, s: stateFor(d, c) }));
  // Live mode renders the backend's own checklist (keys, labels, readiness); mock mode the static one.
  const live = !!d.liveCriteria;
  const lp = liveProgress(d);
  const total = live ? lp.total : st.criteria.length;
  const ticked = live ? lp.done : states.filter((x) => isDone(x.s)).length;
  const canMove = open && mine && ticked === total;
  const revision = d.stage === 'revision' || d.stage === 'revision_2';

  return <div className="flex min-w-0 flex-col gap-5">
    <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-[13px] text-ink-3"><Link to={`/app/board${p === 'existing_customer' ? '?p=ec' : ''}`} className="text-ink-2 no-underline hover:text-ink">{PIPELINES[p].label}</Link><span>/</span><Link to={stagePath(p, d.stage!)} className="text-ink-2 no-underline hover:text-ink">{st.mergedInto ? 'Revision' : st.label}</Link><span>/</span><span className="text-ink">{d.company}</span></nav>

    <header className="panel flex flex-col gap-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 flex-col gap-1.5">
          <div className="flex flex-wrap items-center gap-2"><h1 className="m-0 text-display">{d.company}</h1>
            {d.status === 'won' ? <Badge tone="success" icon="trophy">Won</Badge> : d.status === 'lost' ? <Badge tone="danger" icon="x">Lost · {d.lostReason}</Badge> : <HealthBadge d={d} />}<RoundBadge round={d.round} /></div>
          <p className="m-0 flex flex-wrap items-center gap-x-2 text-ink-2"><span className="text-title text-ink">{valueText(d)}</span>{!!d.value && <span className="text-small text-ink-3">a year</span>}<span>·</span><span>{st.label} · {st.probability}%</span><span>·</span><span>{inStageText(d)} in {st.label}</span><span>·</span><span>{d.source}</span>{d.website && <><span>·</span><a href={`https://${d.website.replace(/^https?:\/\//, '')}`} target="_blank" rel="noreferrer" className="text-brand-ink">{d.website}</a></>}</p>
          <p className="m-0 flex flex-wrap items-center gap-2 text-small text-ink-3"><Avatar name={d.ownerName ?? '?'} size={20} />Owner {d.ownerName} · {howAssigned(d)} · added by {d.generatedByName}</p>
        </div>
        {open && mine && <div className="flex flex-wrap items-center gap-2">
          <Button icon="file" onClick={() => log.open({ dealId: d.id })}>Log activity</Button>
          {revision && <Button onClick={() => setDialog('round2')}>Another round of changes</Button>}
          <MoreMenu onBack={() => setDialog('back')} onLost={() => setDialog('lost')} canBack={PIPELINES[p].stages.findIndex((s) => s.key === d.stage) > 0} />
          {next && <Button variant="primary" icon="arrow" disabled={!canMove} loading={move.isPending} onClick={() => move.mutate({ to: next.key })}>{next.won ? `Mark Won · move to ${next.label}` : `Move to ${next.label}`}</Button>}
        </div>}
      </div>
      <StageBar d={d} picked={viewStage} onPick={live ? setViewStage : undefined} />
      {live && viewStage && <StageHistory d={d} stageKey={viewStage} onClose={() => setViewStage(null)} />}
      {open && mine && next && !canMove && <p className="m-0 text-small text-ink-3">Tick all {total} exit criteria to move to {next.label} ({ticked}/{total} done).</p>}
      {lead_ && <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-surface-0 px-3 py-2.5">
        <span className="flex basis-full items-center gap-2 text-small text-ink-2 xl:mr-auto xl:basis-auto"><Icon name="shield" className="text-brand-ink" />{d.ownerName} works this deal. You can verify ticks, assign a task, reassign it{open ? ' or override a gate' : ''}.</span>
        {open && <Button size="sm" icon="plus" onClick={() => setDialog('task')}>Assign task</Button>}
        {d.status !== 'lost' && <Button size="sm" icon="user" onClick={() => setDialog('reassign')}>Reassign</Button>}
        {open && next && <Button size="sm" icon="shield" onClick={() => setDialog('override')}>Override and move…</Button>}
        {d.status === 'lost' && <Button size="sm" variant="primary" icon="undo" onClick={() => setDialog('reopen')}>Reopen deal</Button>}
        <Button size="sm" variant="quiet" className="!text-danger" icon="x" onClick={() => setDialog('delete')}>Delete</Button>
      </div>}
      {!mine && !lead_ && open && <Banner tone="info" title={`Only ${d.ownerName} ticks criteria and moves this deal`} />}
      {proofMissing && <Banner tone="danger" title="Proof missing">A ticked criterion lost its proof. The tick stays, and the warning clears when the proof is attached again.</Banner>}
      {d.status === 'lost' && <Banner tone="danger" title={`Lost from ${stageOf(p, d.lostFromStage ?? 'lead')?.label} · ${d.lostReason}`}>{d.lostNotes || 'No notes.'}{lead_ ? '' : ' Only a Team Leader can reopen it.'}</Banner>}
      {error && <Banner tone="danger" title={error} action={<Button size="sm" variant="quiet" onClick={() => setError(null)}>Dismiss</Button>} />}
    </header>

    <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_420px]">
      <div className="flex min-w-0 flex-col gap-5">
        {d.status !== 'lost' && <NextStep d={d} mine={mine} onChanged={refresh} />}
        {live && (d.stage === 'revision' || d.stage === 'revision_2') && <RevisionTerms d={d} canAct={mine && d.status === 'open'} />}
        {live && d.stage === 'proposal' && <ProposalPanel d={d} canAct={mine} onLog={(meeting) => log.open({ dealId: d.id, kind: 'meeting', meeting })} onLost={() => setDialog('lost')} />}
        {(d.stage === 'qualification' || d.stage === 'meeting_scheduled' || d.stage === 'proposal') && <MeetingPanel d={d} activities={activities} canAct={mine || lead_} onLog={(meeting) => log.open({ dealId: d.id, kind: 'meeting', meeting })} />}
        <section className="panel flex flex-col p-5" aria-labelledby="crit-h">
          <div className="mb-3 flex items-center justify-between gap-2"><h2 id="crit-h" className="m-0 text-heading">Exit criteria · {st.label}</h2><span className={clsx('text-small font-semibold', ticked === total ? 'text-success' : 'text-ink-2')}>{ticked}/{total} ticked</span></div>
          {live ? <LiveCriteriaList d={d} activities={activities} editable={mine && (open || d.status === 'won')} canVerify={lead_} onChanged={() => { refresh(); qc.invalidateQueries({ queryKey: ['needsYou'] }); }} onLog={(k, meeting) => log.open({ dealId: d.id, kind: k, meeting })} />
          : <ul className="m-0 flex list-none flex-col overflow-hidden rounded-lg border border-line p-0">
            {states.map(({ c, s }) => <CriterionRow key={c.key} d={d} c={c} s={s} activities={activities} editable={mine && (open || d.status === 'won')} canVerify={lead_} onChanged={() => { refresh(); qc.invalidateQueries({ queryKey: ['needsYou'] }); }} onLog={(k) => log.open({ dealId: d.id, kind: k })} />)}
          </ul>}
          <p className="m-0 mt-2 text-xs text-ink-3">Proof first: each item can be ticked only once its proof exists. Your Team Leader reviews ticks afterwards.</p>
        </section>
        {d.stage !== 'qualification' && d.stage !== 'meeting_scheduled' && d.stage !== 'proposal' && <MeetingPanel d={d} activities={activities} canAct={mine || lead_} onLog={(meeting) => log.open({ dealId: d.id, kind: 'meeting', meeting })} />}
        <KeyDetails d={d} />
        <div className="grid gap-5 lg:grid-cols-2">
          <section className="panel flex flex-col gap-3 p-5"><h2 className="m-0 text-heading">Contacts</h2>
            <ul className="m-0 flex list-none flex-col gap-3 p-0">{(d.contacts ?? []).map((c) => <li key={c.id} className="flex items-start gap-3"><Avatar name={c.name} size={30} /><span className="flex min-w-0 flex-col"><span className="flex flex-wrap items-center gap-1.5 text-body-strong">{c.name}<ContactTypeBadge decisionMaker={c.decisionMaker} /></span><span className="text-xs text-ink-3">{[c.role, c.phone, c.email].filter(Boolean).join(' · ')}</span>{(mine || lead_) && d.status !== 'lost' && <span className="mt-0.5 flex flex-wrap items-center"><ContactTypeSwitch contactId={c.id} name={c.name} decisionMaker={c.decisionMaker} /></span>}</span>
              {c.phone && <a href={`tel:${c.phone.replace(/\s/g, '')}`} aria-label={`Call ${c.name}`} className="ml-auto grid h-8 w-8 place-items-center rounded-md border border-line-control text-ink-2 hover:bg-surface-3"><Icon name="phone" size={14} /></a>}</li>)}</ul></section>
          <section className="panel flex flex-col gap-3 p-5"><h2 className="m-0 text-heading">Proposals and documents</h2>
            {(d.documents ?? []).length === 0 ? <p className="m-0 text-small text-ink-2">Nothing yet. Agreements, proposals and invoices you attach as proof appear here, with every version kept.</p>
              : <ul className="m-0 flex list-none flex-col gap-2 p-0">{d.documents!.map((x) => <li key={x.id} className="flex items-center gap-2.5 text-small"><Icon name="file" className="text-ink-3" /><span className="min-w-0 flex-1 truncate font-semibold">{x.name}</span>{x.status && <Badge tone={x.status === 'Countersigned' ? 'success' : 'brand'}>{x.status}</Badge>}<span className="whitespace-nowrap text-xs text-ink-3">{formatRelativeIST(x.at).split(',')[0]}</span></li>)}</ul>}
          </section>
        </div>
        <EarlierStages d={d} />
      </div>
      <Timeline activities={activities} events={events} onLog={mine && open ? () => log.open({ dealId: d.id }) : undefined} />
    </div>

    {dialog === 'lost' && <LostDialog d={d} busy={lose.isPending} onClose={() => setDialog(null)} onConfirm={(reason, notes) => lose.mutate({ reason, notes })} />}
    {dialog === 'back' && <BackToDialog d={d} busy={move.isPending} onClose={() => setDialog(null)} onConfirm={(to, reason) => move.mutate({ to, reason })} />}
    {dialog === 'override' && <OverrideDialog d={d} busy={lead.isPending} onClose={() => setDialog(null)} onConfirm={(to, reason) => lead.mutate({ kind: 'override', to, reason })} />}
    {dialog === 'reopen' && <ReasonDialog title={`Reopen ${d.company}?`} description={`It goes back into ${stageOf(p, d.lostFromStage ?? 'lead')?.label}, the stage it was lost from.`} action="Reopen deal" busy={lead.isPending} onClose={() => setDialog(null)} onConfirm={(reason) => lead.mutate({ kind: 'reopen', reason })} />}
    {dialog === 'reassign' && <ReassignDialog d={d} busy={lead.isPending} onClose={() => setDialog(null)} onConfirm={(userId, reason) => lead.mutate({ kind: 'reassign', userId, reason })} />}
    {dialog === 'delete' && <ReasonDialog title={`Delete ${d.company}?`} description="The deal is hidden from every page but kept with its full history. It can be restored from Deleted records." action="Delete deal" danger optional={me?.role === 'super_admin'} onClose={() => setDialog(null)} onConfirm={async (reason) => { try { await api.deleteDeal(d.id, reason); toast({ message: `${d.company} deleted` }); ['deals', 'today', 'needsYou', 'teamOverview', 'deleted'].forEach((k) => qc.invalidateQueries({ queryKey: [k] })); nav('/app/board'); } catch (e) { setDialog(null); setError(e instanceof ApiError ? e.message : 'Could not delete.'); } }} />}
    {dialog === 'task' && <AssignTaskDialog repName={d.ownerName?.split(' ')[0] ?? 'the owner'} deals={[d]} initialDealId={d.id} onClose={() => { setDialog(null); refresh(); }} />}
    {dialog === 'round2' && <Round2Dialog d={d} busy={move.isPending} onClose={() => setDialog(null)} onConfirm={(reason) => move.mutate({ to: 'revision_2', reason })} />}
  </div>;
}

/* the criterion state, with auto checks computed the same way as the API */
function stateFor(d: Lead, c: CriterionDef): CriterionState {
  if (c.proof.kind === 'auto') {
    const ok = c.proof.check === 'basic_info' ? !!(d.company && d.contactName && (d.phone || d.email) && d.source) : !!(d.company && (d.website || d.industry));
    return ok ? { state: 'ticked', proof: c.proof.check === 'basic_info' ? 'All basic fields filled' : d.website ?? d.industry, by: 'System' } : { state: 'open' };
  }
  return d.criteria?.[c.key] ?? { state: 'open' };
}

function CriterionRow({ d, c, s, activities, editable, canVerify, onChanged, onLog }: { d: Lead; c: CriterionDef; s: CriterionState; activities: Activity[]; editable: boolean; canVerify?: boolean; onChanged: () => void; onLog: (k: Activity['kind']) => void }) {
  const [openForm, setOpenForm] = useState(false); const [untick, setUntick] = useState(false); const [reject, setReject] = useState(false); const toast = useToast();
  const verify = useMutation({ mutationFn: (v: { d: 'verified' | 'rejected'; reason?: string }) => api.verifyTick(d.id, c.key, v.d, v.reason), onSuccess: (_x, v) => { setReject(false); toast({ message: v.d === 'verified' ? `Verified: ${c.label}` : `Rejected. ${d.ownerName} has been told.` }); onChanged(); } });
  const done = isDone(s);
  return <li className="flex flex-col gap-2 border-t border-line bg-surface-1 px-3 py-3 first:border-t-0">
    <div className="grid grid-cols-[22px_minmax(0,1fr)_auto] items-start gap-3">
      <span aria-hidden className={clsx('mt-0.5 grid h-5 w-5 place-items-center rounded-sm border-[1.5px]', done ? 'border-brand bg-brand text-brand-on' : s.state === 'rejected' ? 'border-danger' : 'border-line-control')}>{done && <Icon name="check" size={14} />}</span>
      <span className="flex min-w-0 flex-col"><span className="text-[14px] font-medium">{c.label}</span>
        {done ? <span className="flex flex-wrap items-center gap-1.5 text-small text-ink-2"><Icon name="clip" size={14} />{s.proof}{s.by && <span className="text-ink-3">· {s.by}{s.at ? `, ${formatRelativeIST(s.at)}` : ''}</span>}</span>
          : <span className="text-small text-ink-3">{s.state === 'rejected' ? <span className="text-danger">Rejected: {s.note}</span> : c.proof.explain}</span>}</span>
      <span className="flex items-center gap-2">
        {s.state === 'verified' && <Badge tone="success" icon="check">Verified</Badge>}
        {s.state === 'ticked' && s.by !== 'System' && <Badge>Awaiting check</Badge>}
        {s.state === 'proof_missing' && <Badge tone="danger" icon="alert">Proof missing</Badge>}
        {s.by === 'System' && <Badge>Automatic</Badge>}
        {editable && !done && c.proof.kind !== 'auto' && !openForm && <Button size="sm" variant="secondary" icon="clip" onClick={() => setOpenForm(true)}>Add proof</Button>}
        {editable && done && s.by !== 'System' && s.state !== 'verified' && <Button size="sm" variant="quiet" onClick={() => setUntick(true)}>Untick</Button>}
        {canVerify && s.state === 'ticked' && s.by !== 'System' && <><Button size="sm" variant="quiet" className="!text-danger" onClick={() => setReject(true)}>Reject</Button><Button size="sm" icon="check" loading={verify.isPending} onClick={() => verify.mutate({ d: 'verified' })}>Verify</Button></>}
      </span>
    </div>
    {openForm && <ProofForm d={d} c={c} activities={activities} onCancel={() => setOpenForm(false)} onDone={() => { setOpenForm(false); onChanged(); }} onLog={onLog} />}
    {untick && <UntickDialog d={d} c={c} onClose={() => setUntick(false)} onDone={() => { setUntick(false); onChanged(); }} />}
    {reject && <ReasonDialog title={`Reject “${c.label}”?`} description={`The tick is removed and ${d.ownerName} is notified.`} action="Reject tick" danger busy={verify.isPending} onClose={() => setReject(false)} onConfirm={(reason) => verify.mutate({ d: 'rejected', reason })} />}
  </li>;
}

function ProofForm({ d, c, activities, onCancel, onDone, onLog }: { d: Lead; c: CriterionDef; activities: Activity[]; onCancel: () => void; onDone: () => void; onLog: (k: Activity['kind']) => void }) {
  const toast = useToast();
  const [vals, setVals] = useState<Record<string, string>>(() => c.proof.kind === 'fields' ? Object.fromEntries(c.proof.fields.map((f) => [f.key, d.fields?.[f.key] ?? ''])) : {});
  const [pick, setPick] = useState(''); const [file, setFile] = useState(''); const [status, setStatus] = useState(c.proof.kind === 'file' ? c.proof.status?.[0] ?? '' : '');
  const [error, setError] = useState<string | null>(null);
  const tick = useMutation({ mutationFn: () => api.tickCriterion(d.id, c.key, c.proof.kind === 'activity' ? { activityId: pick } : c.proof.kind === 'fields' ? { fields: vals } : { file: { name: file, status: status || undefined } }),
    onSuccess: () => { toast({ message: `Ticked: ${c.label}` }); onDone(); }, onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not tick it.') });
  const pf = c.proof;
  const options = pf.kind === 'activity' ? activities.filter((a) => pf.activity.includes(a.kind) && (!pf.twoWay || a.twoWay)) : [];
  const ready = pf.kind === 'activity' ? !!pick : pf.kind === 'fields' ? pf.fields.every((f) => vals[f.key]?.trim()) : !!file;
  return <div className="ml-[34px] flex flex-col gap-3 rounded-md border border-line-control bg-surface-0 p-3">
    {pf.kind === 'activity' && (options.length === 0
      ? <div className="flex flex-wrap items-center justify-between gap-2 text-small text-ink-2"><span>No matching activity yet. {pf.explain}</span><Button size="sm" icon="file" onClick={() => onLog(pf.activity[0])}>Log {ACTIVITY_LABEL[pf.activity[0]].toLowerCase()} now</Button></div>
      : <fieldset className="m-0 flex flex-col gap-1.5 border-0 p-0"><legend className="mb-1 text-[13px] font-semibold">Pick the activity that proves it</legend>{options.slice(0, 5).map((a) => <label key={a.id} className="flex cursor-pointer items-start gap-2 text-small"><input type="radio" name={'p' + c.key} checked={pick === a.id} onChange={() => setPick(a.id)} className="mt-1 accent-[var(--brand)]" /><span><b>{ACTIVITY_LABEL[a.kind]}</b> · {formatRelativeIST(a.at)}<span className="block text-ink-2">{a.discussed}</span></span></label>)}</fieldset>)}
    {pf.kind === 'fields' && <div className="grid gap-3 sm:grid-cols-2">{pf.fields.map((f) => <FieldInput key={f.key} f={f} d={d} value={vals[f.key] ?? ''} onChange={(v) => setVals((x) => ({ ...x, [f.key]: v }))} />)}</div>}
    {pf.kind === 'file' && <div className="grid gap-3 sm:grid-cols-2">
      <label className="flex flex-col gap-1.5 text-[13px] font-semibold">File<span aria-hidden className="text-danger">{''}</span><input type="file" onChange={(e) => setFile(e.target.files?.[0]?.name ?? '')} className="text-small font-normal file:mr-3 file:h-8 file:rounded-md file:border file:border-line-control file:bg-surface-1 file:px-3 file:text-[13px] file:font-semibold" /></label>
      {pf.status && pf.status.length > 0 && <SelectField label="Status" value={status} onChange={(e) => setStatus(e.target.value)} options={pf.status.map((x) => ({ value: x, label: x }))} />}
    </div>}
    {error && <Banner tone="danger" title={error} />}
    <div className="flex justify-end gap-2"><Button size="sm" variant="quiet" onClick={onCancel}>Cancel</Button><Button size="sm" variant="primary" icon="check" disabled={!ready} loading={tick.isPending} onClick={() => tick.mutate()}>Tick with this proof</Button></div>
  </div>;
}

function FieldInput({ f, d, value, onChange }: { f: FieldDef; d: Lead; value: string; onChange: (v: string) => void }) {
  if (f.type === 'longtext') return <TextAreaField label={f.label} required rows={2} value={value} onChange={(e) => onChange(e.target.value)} placeholder={f.placeholder} className="sm:col-span-2" />;
  if (f.type === 'select') return <SelectField label={f.label} required value={value} onChange={(e) => onChange(e.target.value)} options={[{ value: '', label: 'Choose…' }, ...(f.options ?? []).map((o) => ({ value: o, label: o }))]} className={f.options?.some((o) => o.length > 30) || f.label.length > 30 ? 'sm:col-span-2' : ''} />;
  if (f.type === 'contact') return <SelectField label={f.label} required value={value} onChange={(e) => onChange(e.target.value)} options={[{ value: '', label: 'Choose a contact…' }, ...(d.contacts ?? []).map((c) => ({ value: c.name, label: `${c.name}${c.role ? ` (${c.role})` : ''}` }))]} />;
  return <TextField label={f.label} required type={f.type === 'date' ? 'date' : f.type === 'number' || f.type === 'money' ? 'number' : 'text'} value={value} onChange={(e) => onChange(e.target.value)} placeholder={f.placeholder} min={f.type === 'money' || f.type === 'number' ? 0 : undefined} />;
}

function NextStep({ d, mine, onChanged }: { d: Lead; mine: boolean; onChanged: () => void }) {
  const toast = useToast(); const [mode, setMode] = useState<null | 'done' | 'edit'>(null);
  const [text, setText] = useState(''); const [due, setDue] = useState(''); const [lane, setLane] = useState<'mine' | 'theirs'>('mine'); const [err, setErr] = useState<string | null>(null);
  const save = useMutation({ mutationFn: () => api.setNextStep(d.id, { text, due, lane }), onSuccess: () => { toast({ message: mode === 'done' ? 'Done. Next step saved' : 'Next step updated' }); setMode(null); onChanged(); }, onError: (e) => setErr(e instanceof ApiError ? e.message : 'Could not save.') });
  const quick = useMutation({ mutationFn: (k: 'snooze' | 'nudge' | 'replied') => api[k](d.id), onSuccess: (_x, k) => { toast({ message: k === 'snooze' ? 'Snoozed 2 days' : k === 'nudge' ? 'Nudge logged' : 'Back to your move' }); onChanged(); } });
  const dueInfo = formatDueIST(d.nextStepDue); const theirs = d.lane === 'theirs';
  const start = (m: 'done' | 'edit') => { setMode(m); setErr(null); setText(m === 'edit' ? d.nextStep : ''); setDue(m === 'edit' ? d.nextStepDue : ''); setLane(m === 'edit' ? d.lane ?? 'mine' : 'mine'); };
  return <section className={clsx('panel flex flex-col gap-3 p-5', dueInfo.overdue && !theirs && '!border-danger')} aria-labelledby="ns-h">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="flex min-w-0 flex-col gap-1"><h2 id="ns-h" className="m-0 text-overline text-ink-3">{theirs ? 'Waiting on them' : 'Your next step'}</h2>
        <p className="m-0 text-heading">{d.nextStep}</p>
        <p className={clsx('m-0 text-small', dueInfo.overdue ? 'font-semibold text-danger' : 'text-ink-2')}>{theirs && d.waitingSince ? `Waiting since ${formatRelativeIST(d.waitingSince).split(',')[0]} · ` : ''}Due {dueInfo.text}{d.snoozeCount ? ` · snoozed ${d.snoozeCount}×` : ''}</p></div>
      {mine && !mode && <div className="flex flex-wrap gap-2">
        {theirs ? <><Button size="sm" icon="send" onClick={() => quick.mutate('nudge')}>Nudge</Button><Button size="sm" onClick={() => quick.mutate('replied')}>They replied</Button></> : <Button size="sm" onClick={() => quick.mutate('snooze')}>+2d</Button>}
        <Button size="sm" variant="quiet" onClick={() => start('edit')}>Edit</Button>
        <Button size="sm" variant="primary" icon="check" onClick={() => start('done')}>Done</Button></div>}
    </div>
    {mode && <form className="flex flex-col gap-3 rounded-md border border-line-control bg-surface-0 p-3" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <p className="m-0 text-subheading">{mode === 'done' ? 'Nice. What’s the next step?' : 'Change the next step'}</p>
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_160px_160px]"><TextField label="Next step" required value={text} onChange={(e) => setText(e.target.value)} autoFocus /><TextField label="Due" required type="date" min={todayIST()} value={due} onChange={(e) => setDue(e.target.value)} />
        <SelectField label="Whose move" value={lane} onChange={(e) => setLane(e.target.value as 'mine' | 'theirs')} options={[{ value: 'mine', label: 'My move' }, { value: 'theirs', label: 'Waiting on them' }]} /></div>
      {err && <Banner tone="danger" title={err} />}
      <div className="flex justify-end gap-2"><Button size="sm" variant="quiet" onClick={() => setMode(null)}>Cancel</Button><Button size="sm" type="submit" variant="primary" disabled={!text.trim() || !due} loading={save.isPending}>{mode === 'done' ? 'Save and close task' : 'Save'}</Button></div>
    </form>}
  </section>;
}

const FIELD_LABELS: Record<string, string> = Object.fromEntries(Object.values(PIPELINES).flatMap((p) => p.stages.flatMap((s) => s.criteria.flatMap((c) => (c.proof.kind === 'fields' ? c.proof.fields.map((f) => [f.key, f.label]) : [])))).reverse()) as Record<string, string>; // first definition wins
Object.assign(FIELD_LABELS, { meetingAt: 'Meeting', attendees: 'Attendees', proposalVersion: 'Proposal version', proposalSent: 'Proposal sent', walkthroughAt: 'Walkthrough', agreementStatus: 'Agreement', changes: 'Requested changes', round2Reason: 'Reason for round 2' });
function KeyDetails({ d }: { d: Lead }) {
  const entries = Object.entries(d.fields ?? {}).filter(([k, v]) => v && FIELD_LABELS[k] && !/^fit\d$/.test(k));
  if (!entries.length) return null;
  const fmt = (k: string, v: string) => (k === 'budget' || k === 'finalPrice') ? `${formatINR(Number(v) * 100)} / month` : k === 'minutes' ? `${Number(v).toLocaleString('en-IN')} min / month` : /T\d\d:/.test(v) ? formatDateTimeIST(v) : /^\d{4}-\d{2}-\d{2}$/.test(v) ? new Date(v + 'T00:00:00Z').toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' }) : v;
  return <section className="panel flex flex-col gap-3 p-5"><h2 className="m-0 text-heading">Key details</h2>
    <dl className="m-0 grid gap-x-6 gap-y-3 sm:grid-cols-2">{entries.map(([k, v]) => <div key={k} className={clsx('flex flex-col gap-0.5', v.length > 60 && 'sm:col-span-2')}><dt className="text-xs font-semibold text-ink-3">{FIELD_LABELS[k]}</dt><dd className="m-0 text-ink">{fmt(k, v)}</dd></div>)}</dl>
  </section>;
}

function EarlierStages({ d }: { d: Lead }) {
  const [open, setOpen] = useState(false);
  const prev = Object.entries(d.criteria ?? {}).filter(([k]) => k.includes('@'));
  if (!prev.length) return null;
  const byStage = prev.reduce<Record<string, [string, CriterionState][]>>((acc, [k, v]) => { const [ck, sk] = k.split('@'); (acc[sk] ||= []).push([ck, v]); return acc; }, {});
  return <section className="panel p-5"><button onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full items-center justify-between text-left"><span className="text-heading">Earlier stages</span><span className="text-small text-ink-2">{prev.length} criteria · {open ? 'Hide' : 'Show'}</span></button>
    {open && <div className="mt-3 flex flex-col gap-3">{Object.entries(byStage).map(([sk, list]) => { const st = stageOf(d.pipeline!, sk); return <div key={sk}><p className="m-0 mb-1 text-overline text-ink-3">{st?.label}</p>
      <ul className="m-0 flex list-none flex-col gap-1 p-0">{list.map(([ck, v]) => <li key={ck} className="flex items-center gap-2 text-small"><Icon name="check" size={14} className="text-success" />{st?.criteria.find((c) => c.key === ck)?.label ?? ck}<span className="text-ink-3">· {v.state === 'verified' ? 'Verified' : v.state}</span></li>)}</ul></div>; })}</div>}
  </section>;
}

const EV_ICON: Record<TimelineEvent['kind'], IconName> = { stage: 'arrow', activity: 'phone', assign: 'user', won: 'trophy', lost: 'x', criterion: 'check', edit: 'clock', alert: 'alert' };
const ACT_ICON: Record<Activity['kind'], IconName> = { call: 'phone', email: 'mail', meeting: 'calendar', discovery: 'users', walkthrough: 'file', kickoff: 'send', note: 'file' };
function Timeline({ activities, events, onLog }: { activities: Activity[]; events: TimelineEvent[]; onLog?: () => void }) {
  const [tab, setTab] = useState<'all' | 'activity' | 'changes'>('all');
  const items = events.filter((e) => tab === 'all' || (tab === 'activity' ? e.kind === 'activity' : e.kind !== 'activity'));
  return <aside className="panel flex flex-col gap-3 p-5 xl:sticky xl:top-[72px]" aria-labelledby="tl-h">
    <div className="flex items-center justify-between gap-2"><h2 id="tl-h" className="m-0 text-heading">Timeline</h2>{onLog && <Button size="sm" icon="plus" onClick={onLog}>Log</Button>}</div>
    <Tabs items={[{ id: 'all', label: 'All' }, { id: 'activity', label: 'Activities', count: activities.length }, { id: 'changes', label: 'Changes' }]} active={tab} onChange={setTab} />
    <ol className="m-0 flex max-h-[calc(100vh-260px)] list-none flex-col overflow-y-auto p-0">
      {items.length === 0 && <li className="py-6 text-center text-small text-ink-3">Nothing here yet.</li>}
      {items.map((e, i) => { const a = e.activityId ? activities.find((x) => x.id === e.activityId) : undefined;
        const tone = e.kind === 'won' ? 'bg-success-soft text-success' : e.kind === 'lost' || e.kind === 'alert' ? 'bg-danger-soft text-danger' : e.kind === 'stage' ? 'bg-brand-soft text-brand-ink' : 'bg-surface-2 text-ink-2';
        return <li key={e.id} className="relative grid grid-cols-[28px_minmax(0,1fr)] gap-3 pb-4">
          {i < items.length - 1 && <span aria-hidden className="absolute bottom-0 left-[13px] top-7 w-0.5 bg-line" />}
          <span className={clsx('grid h-7 w-7 place-items-center rounded-full', tone)}><Icon name={a ? ACT_ICON[a.kind] : EV_ICON[e.kind]} size={14} /></span>
          <div className="flex min-w-0 flex-col pt-0.5">
            {a ? <ActivityItem a={a} /> : <><span className="text-[14px] font-medium">{e.title}</span>{e.detail && <span className="text-small text-ink-2">{e.detail}</span>}</>}
            <span className="text-xs text-ink-3">{formatDateTimeIST(e.at)} · {e.by}</span>
            {e.reason && <span className="mt-1.5 rounded-sm bg-surface-2 px-2.5 py-1.5 text-small text-ink-2">Reason: {e.reason}</span>}
          </div></li>; })}
    </ol>
  </aside>;
}

function ActivityItem({ a }: { a: Activity }) {
  const [open, setOpen] = useState(false);
  const extra = [['Requirements', a.requirements], ['Objections', a.objections], ['Decisions', a.decisions], ['Actions', a.actions]].filter(([, v]) => v) as [string, string][];
  return <>
    <span className="flex flex-wrap items-center gap-1.5 text-[14px] font-medium">{ACTIVITY_LABEL[a.kind]}{a.kind === 'meeting' && a.meetingAt && !a.twoWay ? ` booked for ${formatRelativeIST(a.meetingAt)}` : ''}{(a.kind === 'call' || a.kind === 'email') && <Badge tone={a.twoWay ? 'success' : 'neutral'}>{a.twoWay ? 'Two-way' : 'No reply'}</Badge>}</span>
    <span className="text-small text-ink-2">{a.discussed}</span>
    {(extra.length > 0 || a.attendees) && <button onClick={() => setOpen(!open)} className="w-fit text-xs font-semibold text-brand-ink hover:underline">{open ? 'Hide details' : 'Show full note'}</button>}
    {open && <dl className="m-0 mt-1 grid gap-1 text-small">{a.attendees && <div><dt className="inline font-semibold">Attendees: </dt><dd className="inline">{a.attendees}</dd></div>}{extra.map(([k, v]) => <div key={k}><dt className="inline font-semibold">{k}: </dt><dd className="inline text-ink-2">{v}</dd></div>)}<div><dt className="inline font-semibold">Next: </dt><dd className="inline text-ink-2">{a.nextStep} · {a.followUp}</dd></div></dl>}
  </>;
}

function MoreMenu({ onBack, onLost, canBack }: { onBack: () => void; onLost: () => void; canBack: boolean }) {
  const [open, setOpen] = useState(false);
  return <div className="relative"><Button aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(!open)}>More</Button>
    {open && <div role="menu" className="absolute right-0 top-11 z-20 w-52 rounded-lg border border-line bg-surface-1 p-1 shadow-pop" onMouseLeave={() => setOpen(false)}>
      {canBack && <button role="menuitem" onClick={() => { setOpen(false); onBack(); }} className="flex h-9 w-full items-center gap-2 rounded-md px-2.5 text-left text-sm hover:bg-surface-3"><Icon name="undo" />Move back a stage…</button>}
      <button role="menuitem" onClick={() => { setOpen(false); onLost(); }} className="flex h-9 w-full items-center gap-2 rounded-md px-2.5 text-left text-sm text-danger hover:bg-danger-soft"><Icon name="x" />Close as Lost…</button>
    </div>}</div>;
}

function LostDialog({ d, busy, onClose, onConfirm }: { d: Lead; busy: boolean; onClose: () => void; onConfirm: (r: string, n: string) => void }) {
  const [r, setR] = useState(''); const [n, setN] = useState(''); const reasons = useLostReasons();
  return <Dialog title={`Close ${d.company} as Lost?`} description="The full history is kept. Only a Team Leader can reopen it." onClose={onClose} footer={<><Button variant="quiet" onClick={onClose}>Cancel</Button><Button variant="danger" disabled={!r} loading={busy} onClick={() => onConfirm(r, n)}>Close as Lost</Button></>}>
    <SelectField label="Lost reason" required value={r} onChange={(e) => setR(e.target.value)} options={[{ value: '', label: 'Choose…' }, ...reasons.map((x) => ({ value: x, label: x }))]} />
    <TextAreaField label="Notes" optionalNote="(what happened)" rows={3} value={n} onChange={(e) => setN(e.target.value)} placeholder="e.g. Chose a cheaper IVR; may revisit in Q2" />
  </Dialog>;
}
function BackToDialog({ d, busy, onClose, onConfirm }: { d: Lead; busy: boolean; onClose: () => void; onConfirm: (to: string, reason: string) => void }) {
  const stages = PIPELINES[d.pipeline!].stages; const before = stages.slice(0, stages.findIndex((s) => s.key === d.stage)).filter((s) => !s.mergedInto);
  const [to, setTo] = useState(before[before.length - 1]?.key ?? ''); const [reason, setReason] = useState('');
  return <Dialog title="Move back a stage" description="Allowed with a reason; it is saved in history." onClose={onClose} footer={<><Button variant="quiet" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!reason.trim() || !to} loading={busy} onClick={() => onConfirm(to, reason)}>Move back</Button></>}>
    <SelectField label="Move to" value={to} onChange={(e) => setTo(e.target.value)} options={before.map((s) => ({ value: s.key, label: s.label }))} />
    <TextAreaField label="Reason" required rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. New decision maker; budget to be re-confirmed" />
  </Dialog>;
}
function Round2Dialog({ d, busy, onClose, onConfirm }: { d: Lead; busy: boolean; onClose: () => void; onConfirm: (reason: string) => void }) {
  const [reason, setReason] = useState('');
  return <Dialog title={`Start revision round ${(d.round ?? 1) + 1}?`} description="Only when the customer genuinely needs another round of changes. Otherwise move Revision → Closure." onClose={onClose} footer={<><Button variant="quiet" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!reason.trim()} loading={busy} onClick={() => onConfirm(reason)}>Start round {(d.round ?? 1) + 1}</Button></>}>
    <TextAreaField label="Why is another round needed?" required rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Legal asked for quarterly billing" />
  </Dialog>;
}
/** Override a gate: force the next move or skip ahead. Missing criteria are marked "Overridden by [name]". */
function OverrideDialog({ d, busy, onClose, onConfirm }: { d: Lead; busy: boolean; onClose: () => void; onConfirm: (to: string, reason: string) => void }) {
  const stages = PIPELINES[d.pipeline!].stages; const i = stages.findIndex((s) => s.key === d.stage);
  const ahead = stages.slice(i + 1).filter((s) => !s.mergedInto);
  const [to, setTo] = useState(nextStage(d.pipeline!, d.stage!)?.key ?? ahead[0]?.key ?? ''); const [reason, setReason] = useState('');
  const miss = stageOf(d.pipeline!, d.stage!)!.criteria.filter((c) => !isDone(d.criteria?.[c.key]) && c.proof.kind !== 'auto');
  return <Dialog title={`Override: move ${d.company}`} description="Use this when the work is done but the record is not, or to skip a stage. The owner is notified." onClose={onClose}
    footer={<><Button variant="quiet" onClick={onClose}>Cancel</Button><Button variant="primary" icon="shield" disabled={!reason.trim() || !to} loading={busy} onClick={() => onConfirm(to, reason)}>Override and move</Button></>}>
    <SelectField label="Move to" value={to} onChange={(e) => setTo(e.target.value)} options={ahead.map((s) => ({ value: s.key, label: `${s.label}${s.key === nextStage(d.pipeline!, d.stage!)?.key ? ' (next)' : ' (skip ahead)'}` }))} />
    {miss.length > 0 && <Banner tone="warning" title={`${miss.length} criteria will be marked “Overridden by you”`}>{miss.map((c) => c.label).join(' · ')}</Banner>}
    <TextAreaField label="Reason" required rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Meeting happened at the expo; Ankit is logging it tonight" />
  </Dialog>;
}

function ReassignDialog({ d, busy, onClose, onConfirm }: { d: Lead; busy: boolean; onClose: () => void; onConfirm: (userId: string, reason: string) => void }) {
  const load = useQuery({ queryKey: ['teamLoad'], queryFn: api.teamLoad });
  const { me } = useAuth(); const cross = me?.role === 'admin' || me?.role === 'super_admin';
  const reps = (load.data ?? []).filter((r) => r.active && r.id !== d.ownerId && (cross || r.teamId === d.teamId)).sort((a, b) => Number(b.teamId === d.teamId) - Number(a.teamId === d.teamId));
  const [to, setTo] = useState(''); const [reason, setReason] = useState('');
  return <Dialog title={`Reassign ${d.company}`} description={cross ? 'You can move it to anyone in any team. Open next steps move to the new owner; both owners are notified.' : 'Open next steps move to the new owner. Both owners are notified. To move it to another team, raise a transfer request.'} onClose={onClose}
    footer={<><Button variant="quiet" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!to || !reason.trim()} loading={busy} onClick={() => onConfirm(to, reason)}>Reassign</Button></>}>
    <SelectField label="New owner" required value={to} onChange={(e) => setTo(e.target.value)} options={[{ value: '', label: 'Choose a rep…' }, ...reps.map((r) => ({ value: r.id, label: `${r.name} · ${r.activeLeads}/${r.cap} active` }))]} />
    <TextAreaField label="Reason" required rows={3} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Divya on leave until 20 Oct" />
  </Dialog>;
}

function UntickDialog({ d, c, onClose, onDone }: { d: Lead; c: CriterionDef; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState('');
  const m = useMutation({ mutationFn: () => api.untickCriterion(d.id, c.key, reason), onSuccess: onDone });
  return <Dialog title={`Untick “${c.label}”?`} onClose={onClose} footer={<><Button variant="quiet" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!reason.trim()} loading={m.isPending} onClick={() => m.mutate()}>Untick</Button></>}>
    <TextAreaField label="Reason" required rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Ticked on the wrong deal" />
  </Dialog>;
}
