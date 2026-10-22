import clsx from 'clsx';
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { Button } from '@/components/ui/Button';
import { Banner, useToast } from '@/components/ui/Feedback';
import { SelectField, TextAreaField, TextField } from '@/components/ui/Field';
import { Icon } from '@/components/ui/Icon';
import { todayIST } from '@/lib/format';
import { ACTIVITY_LABEL, type ActivityKind } from '@/lib/pipeline';

interface Ctx { open: (opts?: { dealId?: string; kind?: ActivityKind; meeting?: 'booked' | 'held' }) => void }
const LogCtx = createContext<Ctx>({ open: () => {} });
export const useLogActivity = () => useContext(LogCtx);

const KINDS: ActivityKind[] = ['call', 'email', 'meeting', 'discovery', 'walkthrough', 'kickoff', 'note'];
const nowLocal = () => { const d = new Date(Date.now() + 5.5 * 3_600_000); return d.toISOString().slice(0, 16); };

/** Feature 7: every interaction uses the same structure, so anyone can take over a deal. Opens from anywhere. */
export function LogActivityProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ dealId?: string; kind?: ActivityKind; meeting?: 'booked' | 'held' } | null>(null);
  return <LogCtx.Provider value={{ open: (o) => setState(o ?? {}) }}>
    {children}
    {state && <Drawer initial={state} onClose={() => setState(null)} />}
  </LogCtx.Provider>;
}

function Drawer({ initial, onClose }: { initial: { dealId?: string; kind?: ActivityKind; meeting?: 'booked' | 'held' }; onClose: () => void }) {
  const { me } = useAuth(); const qc = useQueryClient(); const toast = useToast(); const ref = useRef<HTMLDivElement>(null);
  const deals = useQuery({ queryKey: ['deals'], queryFn: api.deals });
  const mine = (deals.data ?? []).filter((d) => d.ownerId === me?.id && d.status !== 'lost').sort((a, b) => a.company.localeCompare(b.company));
  const [dealId, setDealId] = useState(initial.dealId ?? '');
  const [kind, setKind] = useState<ActivityKind>(initial.kind ?? (initial.meeting ? 'meeting' : 'call'));
  const [twoWay, setTwoWay] = useState(true);
  const [when, setWhen] = useState(nowLocal());
  const [meetingStatus, setMeetingStatus] = useState<'scheduled' | 'held'>(initial.meeting === 'booked' ? 'scheduled' : 'held');
  const [meetingAt, setMeetingAt] = useState(''); const [attendees, setAttendees] = useState('');
  const [n, setN] = useState({ discussed: '', requirements: '', objections: '', decisions: '', actions: '', nextStep: '', followUp: '' });
  const [lane, setLane] = useState<'mine' | 'theirs'>('mine');
  const [more, setMore] = useState(false); const [error, setError] = useState<string | null>(null);
  const deal = mine.find((d) => d.id === dealId);
  const set = (k: keyof typeof n) => (e: { target: { value: string } }) => setN((x) => ({ ...x, [k]: e.target.value }));

  useEffect(() => { ref.current?.querySelector<HTMLElement>('select, textarea')?.focus(); const k = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); }; document.addEventListener('keydown', k); return () => document.removeEventListener('keydown', k); }, [onClose]);
  const scheduling = kind === 'meeting' && meetingStatus === 'scheduled';
  const askTwoWay = kind === 'call' || kind === 'email';

  const save = useMutation({
    mutationFn: () => api.logActivity(dealId, {
      dealId, kind, at: new Date(when + ':00+05:30').toISOString(), twoWay: kind === 'note' ? false : scheduling ? false : askTwoWay ? twoWay : true,
      discussed: n.discussed, requirements: n.requirements || undefined, objections: n.objections || undefined, decisions: n.decisions || undefined, actions: n.actions || undefined,
      nextStep: n.nextStep, followUp: n.followUp, lane, meetingAt: kind === 'meeting' ? (scheduling ? (meetingAt ? new Date(meetingAt + ':00+05:30').toISOString() : undefined) : new Date(when + ':00+05:30').toISOString()) : undefined, attendees: kind === 'meeting' ? attendees : undefined
    }),
    onSuccess: () => { ['deals', 'today', 'deal', 'proposalRounds'].forEach((k) => qc.invalidateQueries({ queryKey: [k] })); toast({ message: `${ACTIVITY_LABEL[kind]} logged on ${deal?.company}` }); onClose(); },
    onError: (e) => setError(e instanceof ApiError ? e.message : 'Could not save the activity.')
  });
  const heldInFuture = kind === 'meeting' && !scheduling && Date.parse(when + ':00+05:30') > Date.now() + 5 * 60_000;
  const bookedInPast = scheduling && !!meetingAt && Date.parse(meetingAt + ':00+05:30') <= Date.now();
  const ready = dealId && n.discussed.trim() && n.nextStep.trim() && n.followUp && n.followUp >= todayIST() && !heldInFuture && !bookedInPast && (kind !== 'meeting' || (attendees.trim() && (!scheduling || meetingAt)));

  return <div className="fixed inset-0 z-50 flex justify-end bg-[var(--overlay)]" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
    <aside ref={ref} role="dialog" aria-modal="true" aria-labelledby="log-title" className="flex h-full w-full max-w-[520px] flex-col bg-surface-1 shadow-pop">
      <header className="flex items-center justify-between gap-3 border-b border-line px-5 py-4">
        <div><h2 id="log-title" className="m-0 text-title">Log activity</h2><p className="m-0 text-small text-ink-2">Same structure for every interaction, so anyone can pick the deal up.</p></div>
        <button onClick={onClose} aria-label="Close" className="grid h-8 w-8 place-items-center rounded-md text-ink-2 hover:bg-surface-3"><Icon name="x" /></button>
      </header>
      <form className="flex flex-1 flex-col gap-4 overflow-y-auto px-5 py-4" onSubmit={(e) => { e.preventDefault(); setError(null); if (ready) save.mutate(); }} noValidate>
        <SelectField label="Deal" required value={dealId} onChange={(e) => setDealId(e.target.value)} options={[{ value: '', label: 'Choose one of your deals…' }, ...mine.map((d) => ({ value: d.id, label: `${d.company} · ${d.contactName}` }))]} />
        <fieldset className="m-0 border-0 p-0"><legend className="mb-1.5 text-[13px] font-semibold">Type</legend>
          <div className="flex flex-wrap gap-1.5">{KINDS.map((k) => <button type="button" key={k} onClick={() => setKind(k)} aria-pressed={kind === k} className={clsx('h-8 rounded-pill border px-3 text-[13px] font-semibold', kind === k ? 'border-brand bg-brand-soft text-brand-ink' : 'border-line-control text-ink-2 hover:bg-surface-3')}>{ACTIVITY_LABEL[k]}</button>)}</div>
        </fieldset>
        {kind === 'meeting' && <fieldset className="m-0 grid gap-2 border-0 p-0 sm:grid-cols-2"><legend className="mb-1.5 text-[13px] font-semibold">Which meeting is this?</legend>
          {([['held', 'Meeting held', 'It already happened. Log who came and what was discussed.'], ['scheduled', 'Book a meeting', 'The customer gave a date. Lock it: the lead can only move past the meeting stage once it is held.']] as const).map(([m, title, text]) =>
            <button type="button" key={m} onClick={() => setMeetingStatus(m)} aria-pressed={meetingStatus === m} className={clsx('flex flex-col gap-1 rounded-lg border p-3 text-left', meetingStatus === m ? 'border-brand bg-brand-soft' : 'border-line-control hover:bg-surface-3')}>
              <span className={clsx('text-[14px] font-semibold', meetingStatus === m ? 'text-brand-ink' : 'text-ink')}>{title}</span><span className="text-xs text-ink-2">{text}</span></button>)}
        </fieldset>}
        <div className="grid gap-3 sm:grid-cols-2">
          <TextField label={scheduling ? 'Booked on' : kind === 'meeting' ? 'When it happened' : 'When'} type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} error={heldInFuture ? 'A meeting that is still to come must be booked, not held.' : undefined} />
          {askTwoWay && <SelectField label="Did they respond?" value={twoWay ? 'yes' : 'no'} onChange={(e) => setTwoWay(e.target.value === 'yes')} options={[{ value: 'yes', label: 'Yes, two-way' }, { value: 'no', label: 'No answer / no reply' }]} hint="Two-way calls and emails count as proof for “Contact verified”." />}
          {scheduling && <TextField label="Meeting date and time" required type="datetime-local" value={meetingAt} onChange={(e) => setMeetingAt(e.target.value)} error={bookedInPast ? 'Pick a time in the future.' : undefined} />}
          {kind === 'meeting' && <TextField label="Attendees" required value={attendees} onChange={(e) => setAttendees(e.target.value)} placeholder="Names, comma separated" className={scheduling ? 'sm:col-span-2' : ''} />}
        </div>
        <TextAreaField label={scheduling ? 'Agenda / what you agreed' : 'What was discussed'} required rows={3} value={n.discussed} onChange={set('discussed')} />
        {!more ? <button type="button" onClick={() => setMore(true)} className="w-fit text-[13px] font-semibold text-brand-ink hover:underline">+ Requirements, objections, decisions, actions</button> : <div className="grid gap-3">
          <TextAreaField label="Customer requirements" optionalNote="(if raised)" rows={2} value={n.requirements} onChange={set('requirements')} />
          <TextAreaField label="Customer objections" optionalNote="(if raised)" rows={2} value={n.objections} onChange={set('objections')} />
          <TextAreaField label="Decisions made" optionalNote="(if any)" rows={2} value={n.decisions} onChange={set('decisions')} />
          <TextAreaField label="Actions taken" optionalNote="(if any)" rows={2} value={n.actions} onChange={set('actions')} />
        </div>}
        <div className="flex flex-col gap-3 rounded-lg border border-line bg-surface-0 p-3">
          <span className="text-subheading">What happens next</span>
          <TextField label="Next step" required value={n.nextStep} onChange={set('nextStep')} placeholder={deal?.nextStep ? `Was: ${deal.nextStep}` : 'e.g. Send the pricing sheet'} />
          <div className="grid gap-3 sm:grid-cols-2">
            <TextField label="Follow-up date" required type="date" min={todayIST()} value={n.followUp} onChange={set('followUp')} />
            <SelectField label="Whose move" value={lane} onChange={(e) => setLane(e.target.value as 'mine' | 'theirs')} options={[{ value: 'mine', label: 'My move' }, { value: 'theirs', label: 'Waiting on them' }]} />
          </div>
          <p className="m-0 text-xs text-ink-3">Person responsible: {me?.name} (owner). This replaces the deal’s current next step and shows on Today.</p>
        </div>
        {error && <Banner tone="danger" title={error} />}
      </form>
      <footer className="flex justify-end gap-2 border-t border-line px-5 py-3"><Button variant="quiet" onClick={onClose}>Cancel</Button><Button variant="primary" icon="check" disabled={!ready} loading={save.isPending} onClick={() => { setError(null); save.mutate(); }}>Save activity</Button></footer>
    </aside>
  </div>;
}
