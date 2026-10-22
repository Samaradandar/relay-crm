import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import type { NotificationPref } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { PageHeader } from '@/components/layout/PageHeader';
import { Badge } from '@/components/ui/Badge';
import { Banner, useToast } from '@/components/ui/Feedback';
import { Button } from '@/components/ui/Button';

export function NotificationSettingsPage() {
  const { me } = useAuth(); const toast = useToast();
  const { data } = useQuery({ queryKey: ['prefs'], queryFn: api.notificationPrefs });
  const [prefs, setPrefs] = useState<NotificationPref[]>([]);
  useEffect(() => { if (data) setPrefs(data); }, [data]);
  const save = useMutation({ mutationFn: () => api.saveNotificationPrefs(prefs), onSuccess: () => toast({ message: 'Notification settings saved' }) });
  return <div className="max-w-[960px]">
    <PageHeader title="Notification settings" subtitle={`In-app alerts are always on. Choose which also come by email to ${me?.email}.`} />
    <section className="panel overflow-hidden">
      <div className="grid grid-cols-[minmax(0,1fr)_80px_80px] gap-4 bg-surface-2 px-4 py-2.5 text-xs font-semibold text-ink-2"><span>Notification</span><span className="text-center">In-app</span><span className="text-center">Email</span></div>
      {prefs.map((p, i) => <div key={p.type} className="grid grid-cols-[minmax(0,1fr)_80px_80px] items-center gap-4 border-t border-line px-4 py-3.5">
        <span className="flex min-w-0 flex-col gap-0.5"><span className="flex flex-wrap items-center gap-2 font-semibold">{p.label}{p.required && <Badge icon="lock">Required</Badge>}</span><span className="text-small text-ink-2">{p.description}</span></span>
        <Toggle checked disabled label={`In-app: ${p.label} (always on)`} onChange={() => {}} />
        <Toggle checked={p.email} disabled={p.required} label={`Email: ${p.label}`} onChange={(v) => setPrefs(prefs.map((x, j) => (j === i ? { ...x, email: v } : x)))} />
      </div>)}
    </section>
    <div className="mt-5"><Banner tone="info" title="More types arrive with each release">Lead assigned and transfers (Phase 2), stage, proof and Today alerts (Phase 3), payment and minute alerts (Phase 4), contract and churn alerts (Phase 5).</Banner></div>
    <div className="mt-5 flex justify-end"><Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>Save settings</Button></div>
  </div>;
}

function Toggle({ checked, disabled, label, onChange }: { checked: boolean; disabled?: boolean; label: string; onChange: (v: boolean) => void }) {
  return <span className="flex justify-center"><button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled} onClick={() => onChange(!checked)}
    className={`relative h-5 w-9 rounded-pill border transition-colors disabled:cursor-not-allowed disabled:opacity-55 ${checked ? 'border-brand bg-brand' : 'border-line-control bg-surface-3'}`}>
    <span className={`absolute top-0.5 h-3.5 w-3.5 rounded-full bg-surface-1 shadow transition-[left] ${checked ? 'left-[18px]' : 'left-0.5'}`} />
  </button></span>;
}
