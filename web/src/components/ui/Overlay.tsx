import clsx from 'clsx';
import { useEffect, useRef, type ReactNode } from 'react';
import { Icon } from './Icon';

/** Dialog shell: overlay, focus moves in, Esc and the close button cancel. */
export function Dialog({ title, description, children, footer, onClose, wide }: { title: string; description?: ReactNode; children: ReactNode; footer: ReactNode; onClose: () => void; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLElement>('input, textarea, select, button[data-autofocus]')?.focus();
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', k);
    return () => { document.removeEventListener('keydown', k); prev?.focus?.(); };
  }, [onClose]);
  return <div className="fixed inset-0 z-50 grid place-items-center bg-[var(--overlay)] p-4" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
    <div ref={ref} role="dialog" aria-modal="true" aria-labelledby="dlg-title" className={clsx('flex max-h-[calc(100vh-32px)] w-full flex-col gap-4 overflow-y-auto rounded-lg bg-surface-1 p-6 shadow-pop', wide ? 'max-w-[600px]' : 'max-w-[480px]')}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1"><h2 id="dlg-title" className="m-0 text-title">{title}</h2>{description && <p className="m-0 text-ink-2">{description}</p>}</div>
        <button onClick={onClose} aria-label="Close" className="grid h-8 w-8 shrink-0 place-items-center rounded-md text-ink-2 hover:bg-surface-3"><Icon name="x" /></button>
      </div>
      {children}
      <div className="flex flex-wrap justify-end gap-2">{footer}</div>
    </div>
  </div>;
}

/** Active leads against a rep's cap. Turns orange near the cap and says "At cap" in words. */
export function CapMeter({ active, cap, compact }: { active: number; cap: number; compact?: boolean }) {
  const pct = cap ? Math.min(1, active / cap) : 1; const full = active >= cap; const near = !full && pct >= 0.85;
  return <span className={clsx('flex min-w-0 items-center gap-2', compact ? 'w-[150px]' : 'w-full')}>
    <span className="h-1.5 min-w-[60px] flex-1 overflow-hidden rounded bg-surface-2" role="meter" aria-valuenow={active} aria-valuemin={0} aria-valuemax={cap} aria-label={`${active} of ${cap} active leads`}>
      <span className={clsx('block h-full rounded', full || near ? 'bg-warning' : 'bg-brand')} style={{ width: `${pct * 100}%` }} />
    </span>
    <span className={clsx('whitespace-nowrap text-caption', full ? 'font-semibold text-warning' : 'text-ink-2')}>{active}/{cap}{full ? ' · At cap' : ''}</span>
  </span>;
}
