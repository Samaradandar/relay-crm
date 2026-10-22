import clsx from 'clsx';
import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react';
import { Icon, type IconName } from './Icon';
import { Button } from './Button';
import { TextAreaField } from './Field';

const BT = { info: ['bg-brand-soft border-brand text-brand-ink', 'info'], notice: ['bg-accent-soft border-accent text-accent-ink', 'clock'], warning: ['bg-warning-soft border-warning text-warning', 'alert'], danger: ['bg-danger-soft border-danger text-danger', 'alert'], success: ['bg-success-soft border-success text-success', 'check'] } as const;

export function Banner({ tone = 'info', title, children, action }: { tone?: keyof typeof BT; title: string; children?: ReactNode; action?: ReactNode }) {
  const [cls, icon] = BT[tone];
  return <div role={tone === 'danger' ? 'alert' : 'status'} className={clsx('grid grid-cols-[20px_1fr_auto] items-start gap-3 rounded-lg border px-4 py-3', cls)}>
    <Icon name={icon as IconName} size={20} />
    <div><p className="text-body-strong">{title}</p>{children && <p className="mt-0.5 text-small text-ink-2">{children}</p>}</div>
    {action}
  </div>;
}

export function StatTile({ label, value, hint }: { label: string; value: ReactNode; hint?: ReactNode }) {
  return <div className="panel flex min-w-[180px] flex-1 flex-col gap-1 p-4"><span className="text-[13px] font-medium text-ink-2">{label}</span><span className="text-metric">{value}</span>{hint && <span className="text-xs text-ink-3">{hint}</span>}</div>;
}

export function Tabs<T extends string>({ items, active, onChange }: { items: { id: T; label: string; count?: number; accent?: boolean }[]; active: T; onChange: (id: T) => void }) {
  return <div role="tablist" className="flex gap-1 overflow-x-auto border-b border-line">
    {items.map((it) => <button key={it.id} role="tab" aria-selected={it.id === active} onClick={() => onChange(it.id)}
      className={clsx('relative inline-flex items-center gap-1.5 whitespace-nowrap px-3 py-2.5 text-sm', it.id === active ? 'font-semibold text-ink after:absolute after:inset-x-2 after:-bottom-px after:h-0.5 after:rounded after:bg-brand' : 'font-medium text-ink-2 hover:text-ink')}>
      {it.label}{it.count != null && <span className={clsx('min-w-5 rounded-pill px-1.5 text-center text-[11px] font-semibold leading-[18px]', it.accent ? 'bg-accent text-accent-on' : 'bg-surface-2 text-ink-2')}>{it.count}</span>}
    </button>)}
  </div>;
}

/** Inline reason box: required for User/TL/Admin on important actions, optional for Super admin (PRD 4.2). */
export function ReasonBox({ policy, value, onChange, id, label = 'Reason', placeholder }: { policy: 'required' | 'optional' | 'none'; value: string; onChange: (v: string) => void; id: string; label?: string; placeholder?: string }) {
  if (policy === 'none') return null;
  return <TextAreaField id={id} label={label} required={policy === 'required'} optionalNote={policy === 'optional' ? '(optional)' : undefined} rows={2} value={value}
    onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
    hint={policy === 'required' ? 'Saved in history with this change. The person it affects can see it.' : 'As Super admin you can leave this blank.'} />;
}

/* ---------- toasts: "<What> updated" + Undo for 10 seconds ---------- */
interface Toast { id: number; message: string; undo?: () => void }
const ToastCtx = createContext<(t: Omit<Toast, 'id'>) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [t, setT] = useState<Toast | null>(null);
  const timer = useRef<number>();
  const show = useCallback((x: Omit<Toast, 'id'>) => { setT({ ...x, id: Date.now() }); window.clearTimeout(timer.current); timer.current = window.setTimeout(() => setT(null), 10_000); }, []);
  return <ToastCtx.Provider value={show}>
    {children}
    <div aria-live="polite" className="pointer-events-none fixed inset-x-0 bottom-6 z-[60] flex justify-center px-4">
      {t && <div className="pointer-events-auto inline-flex items-center gap-3 rounded-lg bg-ink py-2.5 pl-4 pr-2.5 text-sm font-medium text-surface-1 shadow-pop">
        {t.message}
        {t.undo && <Button size="sm" variant="quiet" icon="undo" className="!border-current !text-surface-1 hover:!bg-transparent" onClick={() => { t.undo?.(); setT(null); }}>Undo</Button>}
      </div>}
    </div>
  </ToastCtx.Provider>;
}
