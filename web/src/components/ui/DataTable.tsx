import clsx from 'clsx';
import type { ReactNode } from 'react';

export interface Column<R> { key: string; label: string; numeric?: boolean; render: (row: R) => ReactNode }

/** Stage pages, lists and settings tables. Wide tables scroll inside their own frame. */
export function DataTable<R extends { id: string }>({ columns, rows, footer, empty = 'Nothing here yet.' }: { columns: Column<R>[]; rows: R[]; footer?: ReactNode; empty?: string }) {
  return <div className="overflow-x-auto rounded-lg border border-line bg-surface-1">
    <table className="w-full border-collapse text-sm">
      <thead><tr>{columns.map((c) => <th key={c.key} className={clsx('sticky top-0 whitespace-nowrap border-b border-line bg-surface-2 px-3 py-2.5 text-left text-xs font-semibold text-ink-2', c.numeric && 'text-right')}>{c.label}</th>)}</tr></thead>
      <tbody>
        {rows.length === 0 && <tr><td colSpan={columns.length} className="px-3 py-8 text-center text-ink-2">{empty}</td></tr>}
        {rows.map((r) => <tr key={r.id} className="hover:bg-surface-3">{columns.map((c) => <td key={c.key} className={clsx('h-11 whitespace-nowrap border-b border-line px-3 text-ink', c.numeric && 'text-right')}>{c.render(r)}</td>)}</tr>)}
      </tbody>
    </table>
    {footer && <div className="flex flex-wrap items-center justify-between gap-3 px-3 py-2 text-[13px] text-ink-2">{footer}</div>}
  </div>;
}
