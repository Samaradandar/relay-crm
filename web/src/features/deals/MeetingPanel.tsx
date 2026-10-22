import clsx from 'clsx';
import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError, type Activity, type Lead } from '@/api/types';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Banner, useToast } from '@/components/ui/Feedback';
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { Icon } from '@/components/ui/Icon';
import { Dialog } from '@/components/ui/Overlay';
import { formatDateTimeIST, todayIST } from '@/lib/format';
import { CANCEL_REASONS, MEETING_STATUS_LABEL, POSTPONE_REASONS, fromLocalIST, isMeeting, isOverdue, reasonLabel, toLocalIST, type CancelReason, type MeetingStatus, type PostponeReason } from '@/lib/meetings';

const TONE: Record<MeetingStatus, 'brand' | 'success' | 'warning' | 'danger' | 'neutral'> = { booked: 'brand', held: 'success', postponed: 'warning', cancelled: 'danger', no_show: 'danger' };

/** Meetings on a deal: the one that is booked and waiting (with what can happen to it), and the
 * whole story of earlier ones. A lead cannot pass the meeting stage until a meeting is HELD; a
 * meeting that moves or falls through is recorded with the reason, never erased. */
export function MeetingPanel({ d, activities, canAct, onLog }: { d: Lead; activities: Activity[]; canAct: boolean; onLog: (meeting: 'booked' | 'held') => void }) {
  // in Proposal only the meetings of this stage: the walkthrough is a new meeting, not the one from Meeting scheduled
  const since = d.stage === 'proposal' && d.stageEnteredAt ? Date.parse(d.stageEnteredAt) : 0;
  const meetings = activities.filter(isMeeting).filter((m) => !since || !m.createdAt || Date.parse(m.createdAt) >= since).sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  const booked = meetings.filter((m) => m.meetingStatus === 'booked').sort((a, b) => Date.parse(a.at) - Date.parse(b.at))[0];
  const held = meetings.find((m) => m.meetingStatus === 'held');
  const [dialog, setDialog] = useState<null | { kind: 'held' | 'postpone' | 'cancel'; m: Activity }>(null);
  const movedTo = (m: Activity) => meetings.find((x) => x.rescheduledFrom === m.id);
  const active = canAct && (d.status === 'open' || d.status === 'won');
  const overdue = booked && isOverdue(booked);
  if (!meetings.length && !(active && (d.stage === 'qualification' || d.stage === 'meeting_scheduled' || d.stage === 'proposal'))) return null;

  return <section className="panel flex flex-col gap-4 p-5" aria-labelledby="meet-h">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 id="meet-h" className="m-0 text-heading">Meetings</h2>
      {active && <span className="flex flex-wrap gap-2"><Button size="sm" icon="calendar" onClick={() => onLog('booked')}>Book a meeting</Button><Button size="sm" icon="file" onClick={() => onLog('held')}>Log a meeting that was held</Button></span>}
    </div>

    {booked ? <div className={clsx('flex flex-col gap-3 rounded-lg border p-4', overdue ? 'border-warning bg-warning-soft' : 'border-line bg-surface-0')}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <span className="text-overline text-ink-3">{overdue ? 'This meeting was due. Was it held?' : 'Booked meeting'}</span>
          <span className="text-heading">{formatDateTimeIST(booked.at)}</span>
          <span className="text-small text-ink-2">With {booked.attendees || 'no attendees listed'}{booked.discussed ? ` · ${booked.discussed}` : ''}</span>
        </div>
        {active && <span className="flex flex-wrap gap-2">
          <Button size="sm" variant="primary" icon="check" onClick={() => setDialog({ kind: 'held', m: booked })}>Meeting held</Button>
          <Button size="sm" onClick={() => setDialog({ kind: 'postpone', m: booked })}>Postpone</Button>
          <Button size="sm" variant="quiet" className="!text-danger" onClick={() => setDialog({ kind: 'cancel', m: booked })}>Cancel</Button>
        </span>}
      </div>
      <p className="m-0 text-small text-ink-2">{d.stage === 'qualification' ? 'The lead can move on now that a meeting is booked, but it cannot pass the Meeting Scheduled stage until the meeting is held.' : 'The lead stays here until the meeting is held. If the date moves or it falls through, record why.'}</p>
    </div>
      : held ? <p className="m-0 flex items-center gap-2 text-small text-ink-2"><Icon name="check" className="text-success" />Meeting held on {formatDateTimeIST(held.at)}.</p>
        : <Banner tone="info" title="No meeting yet">{active ? 'Book the date the customer gave you, or log a meeting that already took place.' : 'Nothing booked or held.'}</Banner>}

    {meetings.length > 0 && <ol className="m-0 flex list-none flex-col gap-2 p-0">
      {meetings.map((m) => { const st = (m.meetingStatus ?? 'held') as MeetingStatus; const next = movedTo(m);
        return <li key={m.id} className="flex flex-col gap-0.5 border-t border-line pt-2 first:border-t-0 first:pt-0">
          <span className="flex flex-wrap items-center gap-2 text-small"><Badge tone={TONE[st]}>{MEETING_STATUS_LABEL[st]}</Badge><span className="text-body-strong">{formatDateTimeIST(m.at)}</span>{m.rescheduledFrom && <span className="text-xs text-ink-3">rescheduled</span>}</span>
          {m.attendees && <span className="text-xs text-ink-3">With {m.attendees}</span>}
          {st === 'held' && m.discussed && <span className="text-small text-ink-2">{m.discussed}</span>}
          {(st === 'postponed' || st === 'cancelled' || st === 'no_show') && <span className="text-small text-ink-2">{reasonLabel(m.statusReasonCode)}{m.statusReason ? `: ${m.statusReason}` : ''}{m.statusByName ? ` — ${m.statusByName}` : ''}{next ? `. Moved to ${formatDateTimeIST(next.at)}.` : ''}</span>}
        </li>; })}
    </ol>}

    {dialog?.kind === 'held' && <HeldDialog d={d} m={dialog.m} onClose={() => setDialog(null)} />}
    {dialog?.kind === 'postpone' && <PostponeDialog d={d} m={dialog.m} onClose={() => setDialog(null)} />}
    {dialog?.kind === 'cancel' && <CancelDialog d={d} m={dialog.m} onClose={() => setDialog(null)} />}
  </section>;
}

/** Refresh everything that shows a meeting: the deal, the lists, Today and the stage pages. */
function useDone(d: Lead, message: string, onClose: () => void) {
  const qc = useQueryClient(); const toast = useToast();
  return () => { ['deal', 'deals', 'today', 'stagePage', 'needsYou', 'proposalRounds'].forEach((k) => qc.invalidateQueries({ queryKey: [k] })); void d; toast({ message }); onClose(); };
}
const err = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

function HeldDialog({ d, m, onClose }: { d: Lead; m: Activity; onClose: () => void }) {
  const [when, setWhen] = useState(() => toLocalIST(Date.parse(m.at) <= Date.now() ? m.at : new Date().toISOString()));
  const [attendees, setAttendees] = useState(m.attendees ?? ''); const [discussed, setDiscussed] = useState('');
  const [more, setMore] = useState(false); const [x, setX] = useState({ requirements: '', objections: '', decisions: '', actions: '' });
  const [nextStep, setNextStep] = useState(''); const [followUp, setFollowUp] = useState(''); const [lane, setLane] = useState<'mine' | 'theirs'>('mine');
  const [error, setError] = useState<string | null>(null);
  const done = useDone(d, 'Meeting marked as held', onClose);
  const m1 = useMutation({ mutationFn: () => api.meetingHeld(m.id, { occurredAt: fromLocalIST(when), attendees, discussed, ...x, nextStep, followUp, lane }), onSuccess: done, onError: (e) => setError(err(e, 'Could not save.')) });
  const future = Date.parse(fromLocalIST(when)) > Date.now() + 5 * 60_000;
  const ready = !!when && !future && attendees.trim() && discussed.trim() && nextStep.trim() && followUp && followUp >= todayIST();
  return <Dialog title="Meeting held" description={`Record what happened with ${d.company}. This is what lets the lead move on.`} onClose={onClose}
    footer={<><Button variant="quiet" onClick={onClose}>Cancel</Button><Button variant="primary" icon="check" disabled={!ready} loading={m1.isPending} onClick={() => { setError(null); m1.mutate(); }}>Save meeting as held</Button></>}>
    <div className="flex flex-col gap-3">
      <div className="grid gap-3 sm:grid-cols-2">
        <TextField label="When it happened" required type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} error={future ? 'A meeting that has not happened yet cannot be marked held.' : undefined} />
        <TextField label="Who attended" required value={attendees} onChange={(e) => setAttendees(e.target.value)} placeholder="Names, comma separated" />
      </div>
      <TextAreaField label="What was discussed" required rows={3} value={discussed} onChange={(e) => setDiscussed(e.target.value)} />
      {!more ? <button type="button" onClick={() => setMore(true)} className="w-fit text-[13px] font-semibold text-brand-ink hover:underline">+ Requirements, objections, decisions, actions</button> : <div className="grid gap-3">
        {(['requirements', 'objections', 'decisions', 'actions'] as const).map((k) => <TextAreaField key={k} label={k === 'requirements' ? 'Customer requirements' : k === 'objections' ? 'Customer objections' : k === 'decisions' ? 'Decisions made' : 'Actions taken'} optionalNote="(if any)" rows={2} value={x[k]} onChange={(e) => setX((v) => ({ ...v, [k]: e.target.value }))} />)}
      </div>}
      <div className="flex flex-col gap-3 rounded-lg border border-line bg-surface-0 p-3">
        <span className="text-subheading">What happens next</span>
        <TextField label="Next step" required value={nextStep} onChange={(e) => setNextStep(e.target.value)} placeholder="e.g. Send the pricing sheet" />
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField label="Follow-up date" required type="date" min={todayIST()} value={followUp} onChange={(e) => setFollowUp(e.target.value)} />
          <SelectField label="Whose move" value={lane} onChange={(e) => setLane(e.target.value as 'mine' | 'theirs')} options={[{ value: 'mine', label: 'My move' }, { value: 'theirs', label: 'Waiting on them' }]} />
        </div>
      </div>
      {error && <Banner tone="danger" title={error} />}
    </div>
  </Dialog>;
}

function PostponeDialog({ d, m, onClose }: { d: Lead; m: Activity; onClose: () => void }) {
  const [newAt, setNewAt] = useState(''); const [code, setCode] = useState<PostponeReason | ''>(''); const [why, setWhy] = useState(''); const [attendees, setAttendees] = useState(m.attendees ?? '');
  const [error, setError] = useState<string | null>(null);
  const done = useDone(d, 'Meeting moved to the new date', onClose);
  const m1 = useMutation({ mutationFn: () => api.postponeMeeting(m.id, { newAt: fromLocalIST(newAt), reasonCode: code as PostponeReason, why, attendees }), onSuccess: done, onError: (e) => setError(err(e, 'Could not postpone.')) });
  const past = !!newAt && Date.parse(fromLocalIST(newAt)) <= Date.now();
  const ready = !!newAt && !past && !!code && why.trim().length >= 3 && attendees.trim();
  return <Dialog title="Postpone the meeting" description={`Booked for ${formatDateTimeIST(m.at)}. The new date replaces it and the reason stays on record.`} onClose={onClose}
    footer={<><Button variant="quiet" onClick={onClose}>Cancel</Button><Button variant="primary" disabled={!ready} loading={m1.isPending} onClick={() => { setError(null); m1.mutate(); }}>Postpone</Button></>}>
    <div className="flex flex-col gap-3">
      <TextField label="New date and time" required type="datetime-local" value={newAt} onChange={(e) => setNewAt(e.target.value)} error={past ? 'Pick a time in the future.' : undefined} />
      <SelectField label="Why is it being postponed?" required value={code} onChange={(e) => setCode(e.target.value as PostponeReason)} options={[{ value: '', label: 'Choose…' }, ...POSTPONE_REASONS]} />
      <TextAreaField label="Explain" required rows={2} value={why} onChange={(e) => setWhy(e.target.value)} placeholder="e.g. Owner is travelling, asked for the following week" />
      <TextField label="Who will attend" required value={attendees} onChange={(e) => setAttendees(e.target.value)} />
      {error && <Banner tone="danger" title={error} />}
    </div>
  </Dialog>;
}

function CancelDialog({ d, m, onClose }: { d: Lead; m: Activity; onClose: () => void }) {
  const [code, setCode] = useState<CancelReason | ''>(''); const [why, setWhy] = useState(''); const [error, setError] = useState<string | null>(null);
  const done = useDone(d, 'Meeting cancelled. Book a new one to move the lead on', onClose);
  const m1 = useMutation({ mutationFn: () => api.cancelMeeting(m.id, { reasonCode: code as CancelReason, why }), onSuccess: done, onError: (e) => setError(err(e, 'Could not cancel.')) });
  const ready = !!code && why.trim().length >= 3;
  return <Dialog title="Cancel the meeting" description={`Booked for ${formatDateTimeIST(m.at)}. The lead cannot pass the meeting stage until a new meeting is booked and held.`} onClose={onClose}
    footer={<><Button variant="quiet" onClick={onClose}>Keep it</Button><Button variant="danger" disabled={!ready} loading={m1.isPending} onClick={() => { setError(null); m1.mutate(); }}>Cancel the meeting</Button></>}>
    <div className="flex flex-col gap-3">
      <SelectField label="What happened?" required value={code} onChange={(e) => setCode(e.target.value as CancelReason)} options={[{ value: '', label: 'Choose…' }, ...CANCEL_REASONS]} />
      <TextAreaField label="Explain" required rows={2} value={why} onChange={(e) => setWhy(e.target.value)} placeholder="e.g. Customer said they chose another vendor" />
      {error && <Banner tone="danger" title={error} />}
    </div>
  </Dialog>;
}
