import type { ReactNode } from 'react';
export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
    <div className="flex flex-col gap-1"><h1 className="m-0 text-display">{title}</h1>{subtitle && <p className="m-0 text-ink-2">{subtitle}</p>}</div>
    {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
  </div>;
}
