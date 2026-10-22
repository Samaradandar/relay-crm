import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import type { Product } from '@/api/types';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { useReasonPrompt } from '@/components/ui/ReasonPrompt';
import { formatINR } from '@/lib/format';

/** Feature 8 product catalog: products, plans, list price and floor price. Proposals and deal values read from here. */
export function CatalogPage() {
  const qc = useQueryClient(); const q = useQuery({ queryKey: ['catalog'], queryFn: api.catalog });
  const [draft, setDraft] = useState<Product[] | null>(null);
  useEffect(() => { if (q.data && !draft) setDraft(JSON.parse(JSON.stringify(q.data))); }, [q.data, draft]);
  const { ask, element } = useReasonPrompt(() => { qc.invalidateQueries({ queryKey: ['catalog'] }); setDraft(null); });
  if (!draft || !q.data) return <div className="panel h-64 animate-pulse" />;
  const dirty = (i: number) => JSON.stringify(draft[i]) !== JSON.stringify(q.data[i]);
  const setPlan = (i: number, j: number, k: 'price' | 'floor' | 'minutes', v: number) => setDraft((d) => { const n = JSON.parse(JSON.stringify(d)); n[i].plans[j][k] = v; return n; });

  return <div className="flex flex-col gap-5">
    <PageHeader title="Product catalog" subtitle="Prices are per month, ex-GST. Discounts up to 10% need the Team Leader; above 10% or below the floor price need an Admin." />
    {draft.map((p, i) => <section key={p.id} className="panel flex flex-col gap-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3"><div className="flex flex-col gap-1"><h2 className="m-0 flex items-center gap-2 text-heading">{p.name}<Badge tone={p.active ? 'success' : 'neutral'} dot>{p.active ? 'Active' : 'Hidden'}</Badge></h2><p className="m-0 text-small text-ink-2">{p.model} · {p.description}</p><p className="m-0 text-xs text-ink-3">Terms: {p.terms.join(', ')} · one-time setup {formatINR(p.oneTimeFee * 100)}</p></div>
        {dirty(i) && <span className="flex gap-2"><Button size="sm" variant="quiet" onClick={() => setDraft(JSON.parse(JSON.stringify(q.data)))}>Discard</Button><Button size="sm" variant="primary" onClick={() => ask({ title: `Save prices for ${p.name}?`, description: 'New proposals use these prices. Deals already priced keep theirs.', done: 'Catalog saved', run: (x) => api.saveProduct(draft[i], x) })}>Save changes</Button></span>}</div>
      <div className="overflow-x-auto rounded-lg border border-line"><table className="w-full border-collapse text-sm">
        <thead><tr>{['Plan', 'Minutes in pack', 'List price / month', 'Floor price', 'Max TL discount'].map((h) => <th key={h} className="whitespace-nowrap border-b border-line bg-surface-2 px-3 py-2.5 text-left text-xs font-semibold text-ink-2">{h}</th>)}</tr></thead>
        <tbody>{p.plans.map((pl, j) => <tr key={pl.name}>
          <td className="border-b border-line px-3 py-2 font-semibold">{pl.name}</td>
          <td className="border-b border-line px-3">{pl.minutes ? <input type="number" aria-label={`${pl.name} minutes`} value={pl.minutes} onChange={(e) => setPlan(i, j, 'minutes', Number(e.target.value))} className="h-8 w-28 rounded-md border border-line-control bg-surface-1 px-2" /> : <span className="text-ink-3">Customer infrastructure</span>}</td>
          <td className="border-b border-line px-3"><span className="flex items-center gap-1">₹<input type="number" aria-label={`${pl.name} price`} value={pl.price} onChange={(e) => setPlan(i, j, 'price', Number(e.target.value))} className="h-8 w-28 rounded-md border border-line-control bg-surface-1 px-2" /></span></td>
          <td className="border-b border-line px-3"><span className="flex items-center gap-1">₹<input type="number" aria-label={`${pl.name} floor`} value={pl.floor} onChange={(e) => setPlan(i, j, 'floor', Number(e.target.value))} className={'h-8 w-28 rounded-md border bg-surface-1 px-2 ' + (pl.floor > pl.price ? 'border-danger' : 'border-line-control')} /></span></td>
          <td className="border-b border-line px-3 text-ink-2">{formatINR(Math.round(pl.price * 0.9) * 100)} (10%)</td>
        </tr>)}</tbody>
      </table></div>
    </section>)}
    {element}
  </div>;
}
