import type { Activity } from '@/api/types';

/** A meeting's life: booked (the customer gave a date, locked) -> held (it happened), or it moved,
 * was cancelled, or the customer did not turn up. Every outcome that is not "held" needs a reason. */
export type MeetingStatus = 'booked' | 'held' | 'postponed' | 'cancelled' | 'no_show';
export type PostponeReason = 'customer_asked' | 'we_asked' | 'customer_unavailable' | 'other';
export type CancelReason = 'customer_cancelled' | 'we_cancelled' | 'no_show' | 'other';

export const MEETING_STATUS_LABEL: Record<MeetingStatus, string> = { booked: 'Booked', held: 'Held', postponed: 'Postponed', cancelled: 'Cancelled', no_show: 'No-show' };

export const POSTPONE_REASONS: { value: PostponeReason; label: string }[] = [
  { value: 'customer_asked', label: 'The customer asked to move it' },
  { value: 'we_asked', label: 'We need to move it' },
  { value: 'customer_unavailable', label: 'The customer is unavailable' },
  { value: 'other', label: 'Another reason' },
];
export const CANCEL_REASONS: { value: CancelReason; label: string }[] = [
  { value: 'customer_cancelled', label: 'The customer cancelled' },
  { value: 'we_cancelled', label: 'We cancelled' },
  { value: 'no_show', label: 'The customer did not turn up' },
  { value: 'other', label: 'Another reason' },
];
export const reasonLabel = (code?: string) => [...POSTPONE_REASONS, ...CANCEL_REASONS].find((r) => r.value === code)?.label ?? '';

export const isMeeting = (a: Activity) => a.kind === 'meeting';
/** A booking whose date has passed and that nobody has marked held, postponed or cancelled. */
export const isOverdue = (a: Activity) => a.meetingStatus === 'booked' && Date.parse(a.at) < Date.now();

/** Local time in India as the value of a datetime-local input, and back to an ISO instant. */
export const toLocalIST = (iso: string) => new Date(Date.parse(iso) + 5.5 * 3_600_000).toISOString().slice(0, 16);
export const fromLocalIST = (v: string) => new Date(v + ':00+05:30').toISOString();
