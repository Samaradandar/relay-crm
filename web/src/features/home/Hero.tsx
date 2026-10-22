import type { ReactNode } from 'react';

export function greeting(now = new Date()) {
  const hour = Number(new Intl.DateTimeFormat('en-IN', { hour: 'numeric', hour12: false, timeZone: 'Asia/Kolkata' }).format(now));
  return hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
}
export const todayLong = (now = new Date()) => new Intl.DateTimeFormat('en-IN', { weekday: 'long', day: 'numeric', month: 'short', year: 'numeric', timeZone: 'Asia/Kolkata' }).format(now);

/** Welcome band at the top of every home page: white into soft blue, with the page's main actions. */
export function Hero({ title, meta, actions }: { title: string; meta?: ReactNode; actions?: ReactNode }) {
  return <section className="relative isolate overflow-hidden rounded-lg border border-line bg-[linear-gradient(115deg,var(--surface-1)_0%,var(--surface-1)_45%,var(--brand-soft)_100%)] px-6 py-5">
    <span aria-hidden className="absolute -right-16 -top-24 -z-10 h-64 w-64 rounded-full border border-[color-mix(in_srgb,var(--brand)_18%,transparent)]" />
    <span aria-hidden className="absolute -right-4 -top-10 -z-10 h-40 w-40 rounded-full border border-dashed border-[color-mix(in_srgb,var(--accent)_45%,transparent)]" />
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div className="flex flex-col gap-1.5">
        <span className="text-overline text-ink-3">{todayLong()}</span>
        <h1 className="m-0 text-display">{title}</h1>
        {meta && <div className="flex flex-wrap items-center gap-2 text-ink-2">{meta}</div>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  </section>;
}
