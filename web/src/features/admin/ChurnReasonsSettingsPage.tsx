/* Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta. */
import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import type { ChurnReason } from '@/api/types';
import { PageHeader } from '@/components/layout/PageHeader';
import { Button } from '@/components/ui/Button';
import { TextField } from '@/components/ui/Field';
import { useReasonPrompt } from '@/components/ui/ReasonPrompt';

type Draft = { id?: string; label: string; active: boolean; sortOrder: number };

/** PRD Feature 9a: Admin (with reason) or Super admin edits the churn-reasons list. */
export function ChurnReasonsSettingsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ['churnReasons'], queryFn: api.churnReasons });
  const [draft, setDraft] = useState<Draft[]>([]);
  useEffect(() => { if (q.data) setDraft(q.data.map((r: ChurnReason) => ({ id: r.id, label: r.label, active: r.active, sortOrder: r.sortOrder }))); }, [q.data]);
  const { ask, element } = useReasonPrompt(() => qc.invalidateQueries({ queryKey: ['churnReasons'] }));

  const update = (i: number, patch: Partial<Draft>) => setDraft((d) => d.map((r, j) => j === i ? { ...r, ...patch } : r));
  const addRow = () => setDraft((d) => [...d, { label: '', active: true, sortOrder: d.length }]);
  const save = () => ask({ title: 'Save churn reasons', action: 'Save', done: 'Churn reasons saved', run: (reason) => api.saveChurnReasons(draft.filter((r) => r.label.trim()), reason) });

  return <div className="flex flex-col gap-5">
    <PageHeader title="Churn reasons" subtitle="The list shown when anyone records a churn." actions={<Button variant="primary" onClick={save}>Save</Button>} />
    <div className="panel flex flex-col gap-3 p-5">
      {draft.map((r, i) => <div key={r.id ?? i} className="flex items-center gap-3">
        <TextField label="" value={r.label} onChange={(e) => update(i, { label: e.target.value })} placeholder="Reason" className="flex-1" />
        <label className="flex items-center gap-1.5 text-sm text-ink-2"><input type="checkbox" checked={r.active} onChange={(e) => update(i, { active: e.target.checked })} />Active</label>
      </div>)}
      <Button size="sm" icon="plus" variant="quiet" onClick={addRow}>Add reason</Button>
    </div>
    {element}
  </div>;
}
