import clsx from 'clsx';
import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import type { Notification } from '@/api/types';
import { PageHeader } from '@/components/layout/PageHeader';
import { Tabs } from '@/components/ui/Feedback';
import { Button } from '@/components/ui/Button';
import { Icon, type IconName } from '@/components/ui/Icon';
import { formatRelativeIST } from '@/lib/format';
import { ENV } from '@/config/env';

type Tab = 'all' | 'unread' | Notification['type'];
const STYLE: Record<Notification['type'], [IconName, string]> = { approval: ['inbox', 'bg-brand-soft text-brand-ink'], people: ['users', 'bg-surface-2 text-ink-2'], system: ['alert', 'bg-danger-soft text-danger'], lead: ['pool', 'bg-accent-soft text-accent-ink'] };

export function NotificationsPage() {
  const qc = useQueryClient(); const nav = useNavigate();
  const [tab, setTab] = useState<Tab>('all');
  const { data = [] } = useQuery({ queryKey: ['notifications'], queryFn: api.notifications });
  const mark = useMutation({ mutationFn: api.markRead, onSuccess: () => qc.invalidateQueries({ queryKey: ['notifications'] }) });
  const unread = data.filter((n) => !n.readAt).length;
  const list = data.filter((n) => tab === 'all' || (tab === 'unread' ? !n.readAt : n.type === tab));
  return <div className="max-w-[960px]">
    <PageHeader title="Notifications" subtitle={`${unread} unread`} actions={<><Button variant="quiet" onClick={() => mark.mutate('all')} disabled={!unread}>Mark all as read</Button><Button icon="settings" onClick={() => nav('/app/settings/notifications')}>Settings</Button></>} />
    <Tabs<Tab> items={[{ id: 'all', label: 'All' }, { id: 'unread', label: 'Unread', count: unread, accent: unread > 0 }, { id: 'approval', label: 'Approvals' }, ...(ENV.releasePhase >= 2 ? [{ id: 'lead' as Tab, label: 'Leads' }] : []), { id: 'people', label: 'People' }, { id: 'system', label: 'System' }]} active={tab} onChange={setTab} />
    <section className="panel mt-5 overflow-hidden">
      {list.length === 0 && <p className="p-10 text-center text-ink-2">You’re all caught up.</p>}
      {list.map((n) => <button key={n.id} onClick={() => { if (!n.readAt) mark.mutate([n.id]); if (n.link) nav(n.link); }}
        className="grid w-full grid-cols-[10px_32px_minmax(0,1fr)_auto] items-start gap-3 border-t border-line px-4 py-3.5 text-left first:border-t-0 hover:bg-surface-3">
        <span className={clsx('mt-3 h-2 w-2 rounded-full', n.readAt ? 'bg-transparent' : 'bg-brand')} aria-label={n.readAt ? undefined : 'Unread'} />
        <span className={clsx('grid h-8 w-8 place-items-center rounded-full', STYLE[n.type][1])}><Icon name={STYLE[n.type][0]} /></span>
        <span className="flex min-w-0 flex-col gap-0.5"><span className={clsx('text-ink', n.readAt ? 'font-normal' : 'font-semibold')}>{n.title}</span><span className="text-small text-ink-2">{n.body}</span></span>
        <span className="whitespace-nowrap pt-0.5 text-caption text-ink-3">{formatRelativeIST(n.createdAt)}</span>
      </button>)}
    </section>
  </div>;
}
