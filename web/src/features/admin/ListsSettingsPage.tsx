import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api, type SettingsListKind } from '@/api';
import { PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/Button';
import { Banner, Tabs } from '@/components/ui/Feedback';
import { useReasonPrompt } from '@/components/ui/ReasonPrompt';

type Row = { id?: string; label: string };
const TABS: { id: SettingsListKind; label: string; title: string; help: string }[] = [
  { id: 'fit-checklist', label: 'Fit checklist', title: 'Fit checklist', help: 'The questions a rep answers about a lead before it can leave the Lead stage. Every item must be ticked.' },
  { id: 'lost-reasons', label: 'Lost reasons', title: 'Lost reasons', help: 'The reasons offered when a deal is marked Lost.' },
];

/** The lists the sales team picks from. A removed entry is hidden from now on, never erased: deals
 * that already use it keep it. Changes ask for a reason and go in the audit log. */
export function ListsSettingsPage() {
  const [tab, setTab] = useState<SettingsListKind>('fit-checklist');
  const cur = TABS.find((t) => t.id === tab)!;
  return <div className="flex flex-col gap-5">
    <PageHeader title="Settings lists" subtitle="The wording your team chooses from. Each change is saved with a reason." />
    <Tabs items={TABS.map((t) => ({ id: t.id, label: t.label }))} active={tab} onChange={setTab} />
    <Editor key={tab} kind={tab} title={cur.title} help={cur.help} />
  </div>;
}

function Editor({ kind, title, help }: { kind: SettingsListKind; title: string; help: string }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['settingsList', kind], queryFn: () => api.settingsList(kind) });
  const [items, setItems] = useState<Row[]>([]);
  useEffect(() => { if (q.data) setItems(q.data.map((i) => ({ id: i.id, label: i.label }))); }, [q.data]);
  const { ask, element } = useReasonPrompt(() => ['settingsList', 'lostReasons', 'deal'].forEach((k) => qc.invalidateQueries({ queryKey: [k] })));
  const saved = JSON.stringify((q.data ?? []).map((i) => ({ id: i.id, label: i.label })));
  const dirty = JSON.stringify(items) !== saved;
  const labels = items.map((i) => i.label.trim().toLowerCase());
  const blank = items.some((i) => !i.label.trim()); const dup = new Set(labels).size !== labels.length;
  const move = (i: number, d: -1 | 1) => setItems((x) => { const n = [...x]; const j = i + d; if (j < 0 || j >= n.length) return x; [n[i], n[j]] = [n[j], n[i]]; return n; });
  return <section className="panel flex flex-col gap-4 p-5">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="flex flex-col gap-0.5"><h2 className="m-0 text-heading">{title}</h2><p className="m-0 text-small text-ink-2">{help}</p></div>
      <span className="flex gap-2"><Button variant="quiet" disabled={!dirty} onClick={() => setItems((q.data ?? []).map((i) => ({ id: i.id, label: i.label })))}>Discard</Button>
        <Button variant="primary" icon="check" disabled={!dirty || blank || dup || items.length === 0} onClick={() => ask({ title: `Save the ${title.toLowerCase()}?`, description: 'Removed entries are hidden from now on; deals that already use them keep them.', action: 'Save', done: `${title} saved`, run: (reason) => api.saveSettingsList(kind, items.map((i) => ({ id: i.id, label: i.label.trim() })), reason) })}>Save</Button></span>
    </div>
    {q.isError && <Banner tone="danger" title="The list could not be loaded">Lists can be edited when the app is connected to the server.</Banner>}
    {dup && <Banner tone="warning" title="Two entries have the same text" />}
    <ol className="m-0 flex list-none flex-col gap-2 p-0">
      {items.map((it, i) => <li key={it.id ?? `new-${i}`} className="flex items-center gap-2">
        <span className="w-6 text-right text-small text-ink-3">{i + 1}</span>
        <input aria-label={`${title} entry ${i + 1}`} value={it.label} onChange={(e) => setItems((x) => x.map((r, k) => (k === i ? { ...r, label: e.target.value } : r)))} className="h-9 min-w-0 flex-1 rounded-md border border-line-control bg-surface-1 px-2.5 text-sm" />
        <Button size="sm" variant="quiet" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}>↑</Button>
        <Button size="sm" variant="quiet" aria-label="Move down" disabled={i === items.length - 1} onClick={() => move(i, 1)}>↓</Button>
        <Button size="sm" variant="quiet" className="!text-danger" aria-label={`Remove ${it.label}`} onClick={() => setItems((x) => x.filter((_, k) => k !== i))}>✕</Button>
      </li>)}
    </ol>
    <Button size="sm" icon="plus" className="self-start" onClick={() => setItems((x) => [...x, { label: '' }])}>Add an entry</Button>
    {element}
  </section>;
}
