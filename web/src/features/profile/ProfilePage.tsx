import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { api } from '@/api';
import { useAuth } from '@/auth/AuthProvider';
import { ROLE_LABEL } from '@/auth/permissions';
import { Avatar, Badge, TeamChip } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Feedback';
import { Icon, type IconName } from '@/components/ui/Icon';
import { formatDateTimeIST, formatRelativeIST } from '@/lib/format';

const KIND: Record<string, [IconName, string]> = { assign: ['user', 'bg-surface-2 text-ink-2'], note: ['info', 'bg-brand-soft text-brand-ink'], alert: ['alert', 'bg-danger-soft text-danger'], stage: ['arrow', 'bg-brand-soft text-brand-ink'] };

export function ProfilePage() {
  const { me } = useAuth(); const toast = useToast();
  const history = useQuery({ queryKey: ['history', 'me'], queryFn: api.myHistory });
  if (!me) return null;
  return <div className="flex max-w-[1200px] flex-col gap-5">
    <section className="panel flex flex-wrap items-center gap-5 p-6">
      <Avatar name={me.name} size={64} />
      <div className="flex flex-[1_1_240px] flex-col gap-1.5"><h1 className="m-0 text-display">{me.name}</h1>
        <div className="flex flex-wrap items-center gap-2"><Badge>{ROLE_LABEL[me.role]}</Badge>{me.team && <TeamChip name={me.team.name} color={me.team.color} />}<Badge tone="success" dot>Active</Badge></div></div>
      <span className="text-small text-ink-2">Joined {formatDateTimeIST(me.createdAt).split(',')[0]}</span>
    </section>
    <div className="flex flex-wrap items-start gap-5">
      <div className="flex min-w-0 flex-[1_1_360px] flex-col gap-5">
        <section className="panel flex flex-col gap-4 p-5"><h2 className="m-0 text-heading">Details</h2>
          <dl className="m-0 grid grid-cols-[140px_minmax(0,1fr)] gap-x-4 gap-y-2.5">
            <dt className="text-ink-2">Work email</dt><dd className="m-0">{me.email}</dd>
            {me.leadCap != null && <><dt className="text-ink-2">Lead cap</dt><dd className="m-0">{me.leadCap} active leads <span className="text-ink-3">· set by an Admin</span></dd></>}
            <dt className="text-ink-2">Time zone</dt><dd className="m-0">IST (UTC+05:30)</dd>
          </dl>
          <p className="m-0 text-xs text-ink-3">Name, role and team are changed by an Admin. Ask your Team Leader if something is wrong.</p>
        </section>
        <section className="panel flex flex-col gap-3.5 p-5"><h2 className="m-0 text-heading">Sign-in</h2>
          <div className="flex flex-wrap items-center justify-between gap-3"><span className="flex flex-col"><span className="font-medium">Google</span><span className="text-xs text-ink-3">{me.email}{me.lastSignInAt && ` · last used ${formatRelativeIST(me.lastSignInAt)}`}</span></span><Badge tone="success" icon="check">Connected</Badge></div>
          <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3.5"><span className="font-medium">Password</span><Button size="sm" onClick={() => toast({ message: `Reset link sent to ${me.email}` })}>Reset password</Button></div>
        </section>
        <section className="panel flex flex-wrap items-center justify-between gap-3 p-5"><span><h2 className="m-0 text-heading">Notifications</h2><span className="text-small text-ink-2">Choose which emails you get. In-app alerts are always on.</span></span><Link to="/app/settings/notifications"><Button size="sm">Notification settings</Button></Link></section>
      </div>
      <section className="panel flex min-w-0 flex-[1.3_1_420px] flex-col gap-4 p-5">
        <div className="flex items-baseline justify-between"><h2 className="m-0 text-heading">My history</h2><span className="text-xs text-ink-3">Cannot be edited by anyone</span></div>
        <ol className="m-0 list-none p-0">{(history.data ?? []).map((h, i, arr) => <li key={h.id} className="relative grid grid-cols-[28px_1fr] gap-3 pb-4">
          {i < arr.length - 1 && <span className="absolute bottom-0 left-[13px] top-7 w-0.5 bg-line" />}
          <span className={`grid h-7 w-7 place-items-center rounded-full ${KIND[h.kind][1]}`}><Icon name={KIND[h.kind][0]} size={14} /></span>
          <div><p className="m-0 pt-1 font-medium">{h.title}</p><p className="m-0 text-xs text-ink-3">{formatDateTimeIST(h.at)} · {h.actor}</p>{h.reason && <p className="m-0 mt-1.5 rounded-sm bg-surface-2 px-2.5 py-1.5 text-small text-ink-2">Reason: {h.reason}</p>}</div>
        </li>)}</ol>
      </section>
    </div>
  </div>;
}
