import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import { useAuth } from '@/auth/AuthProvider';
import { ROLE_LABEL } from '@/auth/permissions';
import { NAV } from '@/auth/navigation';
import { ENV } from '@/config/env';
import { PageHeader } from '@/components/layout/PageHeader';
import { Banner, StatTile } from '@/components/ui/Feedback';
import { ApprovalBadge, TeamChip } from '@/components/ui/Badge';
import { formatRelativeIST } from '@/lib/format';
import { RepHome } from './RepHome';
import { TodayPage } from './TodayPage';
import { TeamHome } from '@/features/team/TeamHome';
import { TeamLeaderHome } from './TeamLeaderHome';

/** Home by role and release phase. Admins and Super admins land on Approvals instead (navigation.homeFor). */
export function HomePage() {
  const { me } = useAuth();
  if (ENV.releasePhase >= 3 && me?.role === 'user') return <TodayPage />;
  if (ENV.releasePhase >= 2 && me?.role === 'user') return <RepHome />;
  if (ENV.releasePhase >= 3 && me?.role === 'team_leader') return <TeamHome />;
  if (ENV.releasePhase >= 2 && me?.role === 'team_leader') return <TeamLeaderHome />;
  return <Phase1Home />;
}

/** Phase 1 home: requests and notifications only. */
function Phase1Home() {
  const { me } = useAuth();
  const approvals = useQuery({ queryKey: ['approvals'], queryFn: api.approvals });
  const notes = useQuery({ queryKey: ['notifications'], queryFn: api.notifications });
  if (!me) return null;
  const mine = approvals.data?.filter((a) => a.requestedBy === me.id) ?? [];
  const next = NAV.filter((n) => n.phase > ENV.releasePhase && n.roles.includes(me.role)).slice(0, 4);
  const hour = Number(new Intl.DateTimeFormat('en-IN', { hour: 'numeric', hour12: false, timeZone: 'Asia/Kolkata' }).format(new Date()));
  const greet = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';

  return <>
    <PageHeader title={`${greet}, ${me.name.split(' ')[0]}`} subtitle={<span className="inline-flex flex-wrap items-center gap-2">{ROLE_LABEL[me.role]}{me.team && <TeamChip name={me.team.name} color={me.team.color} />}</span>} />
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap gap-4">
        <StatTile label="Unread notifications" value={notes.data?.filter((n) => !n.readAt).length ?? '–'} hint={<Link to="/app/notifications" className="text-brand-ink">Open notifications</Link>} />
        <StatTile label="My requests waiting" value={mine.filter((a) => a.status === 'pending').length} hint="Approvals you raised that are not decided yet" />
        {me.leadCap != null && <StatTile label="My lead cap" value={me.leadCap} hint="Set by an Admin. Used when leads are assigned from Phase 2." />}
      </div>
      <section className="panel p-5">
        <h2 className="m-0 mb-3 text-heading">My recent requests</h2>
        {mine.length === 0 ? <p className="m-0 text-ink-2">You have not raised any requests yet.</p> :
          <ul className="m-0 flex list-none flex-col divide-y divide-line p-0">{mine.map((a) => <li key={a.id} className="flex items-center justify-between gap-3 py-3"><span className="flex flex-col"><span className="text-body-strong">{a.title}</span><span className="text-xs text-ink-3">{formatRelativeIST(a.requestedAt)}</span></span><ApprovalBadge status={a.status} /></li>)}</ul>}
      </section>
      {next.length > 0 && <Banner tone="info" title="More of Relay is on its way">Coming next for you: {next.map((n) => n.label).join(', ')}. They switch on as each release goes live.</Banner>}
    </div>
  </>;
}
