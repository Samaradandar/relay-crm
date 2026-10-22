const IST = 'Asia/Kolkata';

/** "6 Oct 2026, 14:02 IST" — every time in the product is shown in IST. */
export function formatDateTimeIST(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  const date = new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', year: 'numeric', timeZone: IST }).format(d);
  const time = new Intl.DateTimeFormat('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: IST }).format(d);
  return `${date}, ${time} IST`;
}

/** "Today, 09:40" / "Yesterday, 18:02" / "4 Oct, 16:20" */
export function formatRelativeIST(iso: string, now: Date = new Date()): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '—';
  const day = (x: Date) => new Intl.DateTimeFormat('en-CA', { timeZone: IST }).format(x);
  const time = new Intl.DateTimeFormat('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: IST }).format(d);
  const yesterday = new Date(now.getTime() - 86_400_000);
  if (day(d) === day(now)) return `Today, ${time}`;
  if (day(d) === day(yesterday)) return `Yesterday, ${time}`;
  return `${new Intl.DateTimeFormat('en-IN', { day: 'numeric', month: 'short', timeZone: IST }).format(d)}, ${time}`;
}

/** paise → "₹4,20,000", or compact "₹4.2L" / "₹1.25Cr" / "₹95K". Money is always stored in paise. */
export function formatINR(paise: number, opts: { compact?: boolean } = {}): string {
  const rupees = Math.round(paise / 100);
  if (!opts.compact) return '₹' + rupees.toLocaleString('en-IN');
  const a = Math.abs(rupees), s = rupees < 0 ? '-' : '';
  if (a >= 1e7) return `${s}₹${(a / 1e7).toFixed(a >= 1e9 ? 0 : 2).replace(/\.?0+$/, '')}Cr`;
  if (a >= 1e5) return `${s}₹${(a / 1e5).toFixed(1).replace(/\.0$/, '')}L`;
  if (a >= 1e3) return `${s}₹${(a / 1e3).toFixed(a >= 1e4 ? 0 : 1).replace(/\.0$/, '')}K`;
  return `${s}₹${a}`;
}

export function initials(name: string): string {
  return name.trim().split(/\s+/).slice(0, 2).map((w) => w[0] ?? '').join('').toUpperCase();
}

const dIST = (x: Date) => new Intl.DateTimeFormat('en-CA', { timeZone: IST }).format(x);
/** Today's date in IST as YYYY-MM-DD (for date inputs and due dates). */
export const todayIST = (): string => dIST(new Date());

/** Next-step due date on cards: "Today", "Tomorrow", "2 days overdue", "Mon 12 Oct". */
export function formatDueIST(date: string, now: Date = new Date()): { text: string; overdue: boolean; today: boolean } {
  if (!date || isNaN(Date.parse(date + 'T00:00:00Z'))) return { text: '—', overdue: false, today: false };
  const diff = Math.round((Date.parse(date + 'T00:00:00Z') - Date.parse(dIST(now) + 'T00:00:00Z')) / 86_400_000);
  if (diff < 0) return { text: diff === -1 ? '1 day overdue' : `${-diff} days overdue`, overdue: true, today: false };
  if (diff === 0) return { text: 'Today', overdue: false, today: true };
  if (diff === 1) return { text: 'Tomorrow', overdue: false, today: false };
  return { text: new Intl.DateTimeFormat('en-IN', { weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC' }).format(new Date(date + 'T00:00:00Z')), overdue: false, today: false };
}

/** Live "time waiting" counter: "14m", "3h 05m", "2d 4h". */
export function formatWaiting(iso: string, now: number = Date.now()): string {
  if (!iso || isNaN(Date.parse(iso))) return '—';
  const m = Math.max(0, Math.floor((now - Date.parse(iso)) / 60_000));
  if (m < 60) return `${m}m`;
  const hrs = Math.floor(m / 60);
  if (hrs < 24) return `${hrs}h ${String(m % 60).padStart(2, '0')}m`;
  return `${Math.floor(hrs / 24)}d ${hrs % 24}h`;
}
