import clsx from 'clsx';
import type { ReactNode } from 'react';
import type { ApprovalStatus } from '@/api/types';
import { Icon, type IconName } from './Icon';
import { initials } from '@/lib/format';

const T = { neutral: 'bg-surface-2 text-ink-2', brand: 'bg-brand-soft text-brand-ink', accent: 'bg-accent-soft text-accent-ink', success: 'bg-success-soft text-success', warning: 'bg-warning-soft text-warning', danger: 'bg-danger-soft text-danger' };
export type Tone = keyof typeof T;

/** Always carries a word: colour is never the only cue. */
export function Badge({ tone = 'neutral', icon, dot, children }: { tone?: Tone; icon?: IconName; dot?: boolean; children: ReactNode }) {
  return <span className={clsx('inline-flex h-[22px] items-center gap-1.5 whitespace-nowrap rounded-sm px-2 text-caption', T[tone])}>
    {icon ? <Icon name={icon} size={14} /> : dot ? <span className="h-1.5 w-1.5 rounded-full bg-current" /> : null}{children}
  </span>;
}

const APPROVAL: Record<ApprovalStatus, [Tone, string, IconName]> = { pending: ['accent', 'Awaiting approval', 'clock'], approved: ['success', 'Approved', 'check'], rejected: ['danger', 'Rejected', 'x'], cancelled: ['neutral', 'Cancelled', 'x'] };
export const ApprovalBadge = ({ status }: { status: ApprovalStatus }) => { const [t, l, i] = APPROVAL[status]; return <Badge tone={t} icon={i}>{l}</Badge>; };

export function Avatar({ name, size = 24 }: { name: string; size?: number }) {
  return <span title={name} aria-label={name} style={{ width: size, height: size, fontSize: Math.round(size * 0.42) }} className="inline-grid shrink-0 place-items-center rounded-pill bg-brand-soft font-semibold leading-none text-brand-ink">{initials(name)}</span>;
}

export function TeamChip({ name, color }: { name: string; color: string }) {
  return <span className="inline-flex h-[22px] items-center gap-1.5 whitespace-nowrap rounded-pill bg-surface-2 pl-1 pr-2.5 text-caption text-ink">
    <span className="h-3.5 w-3.5 rounded-full shadow-[inset_0_0_0_1px_rgba(0,0,0,.12)]" style={{ background: color }} />{name}
  </span>;
}

export function Count({ n, accent }: { n: number; accent?: boolean }) {
  return <span className={clsx('min-w-5 rounded-pill px-1.5 text-center text-[11px] font-semibold leading-[18px]', accent ? 'bg-accent text-accent-on' : 'bg-surface-2 text-ink-2')}>{n}</span>;
}
