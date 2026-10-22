import { useEffect, useState } from 'react';
import { Navigate, Outlet, RouterProvider, createBrowserRouter, createMemoryRouter, useNavigate, useSearchParams, type RouteObject } from 'react-router-dom';
import { ENV } from '@/config/env';
import { Splash, shouldPlaySplash } from '@/features/splash/Splash';
import { LoginPage } from '@/features/auth/LoginPage';
import { RequireAuth, RequireRole } from '@/auth/guards';
import { useAuth } from '@/auth/AuthProvider';
import { homeFor } from '@/auth/navigation';
import { finishGoogleSignIn } from '@/auth/cognito';
import { AppShell } from '@/components/layout/AppShell';
import { HomePage } from '@/features/home/HomePage';
import { ApprovalsPage } from '@/features/approvals/ApprovalsPage';
import { UsersPage } from '@/features/admin/UsersPage';
import { TeamsPage } from '@/features/admin/TeamsPage';
import { FailedJobsPage } from '@/features/admin/FailedJobsPage';
import { ProfilePage } from '@/features/profile/ProfilePage';
import { NotificationsPage } from '@/features/notifications/NotificationsPage';
import { NotificationSettingsPage } from '@/features/notifications/NotificationSettingsPage';
import { NewLeadPage } from '@/features/leads/NewLeadPage';
import { PoolPage } from '@/features/pool/PoolPage';
import { BoardPage } from '@/features/deals/BoardPage';
import { StagePage } from '@/features/deals/StagePage';
import { DealPage } from '@/features/deals/DealPage';
import { TodayPage } from '@/features/home/TodayPage';
import { MyTeamPage, RepPage } from '@/features/team/TeamPages';
import { WonPage, LostPage } from '@/features/deals/ClosedPages';
import { OverviewPage } from '@/features/admin/OverviewPage';
import { AssignmentSettingsPage } from '@/features/admin/AssignmentSettingsPage';
import { TransfersPage } from '@/features/leads/TransfersPage';
import { CaptureReviewPage, CaptureSourcesPage } from '@/features/leads/CapturePages';
import { ImportCsvPage } from '@/features/leads/ImportCsvPage';
import { ListsSettingsPage } from '@/features/admin/ListsSettingsPage';
import { ProposalTemplatePage } from '@/features/admin/ProposalTemplatePage';
import { PipelineSettingsPage } from '@/features/admin/PipelineSettingsPage';
import { CatalogPage } from '@/features/admin/CatalogPage';
import { AuditPage, DeletedPage, PermissionsPage } from '@/features/admin/AuditPages';
import { AccountsListPage } from '@/features/accounts/AccountsListPage';
import { AccountPage } from '@/features/accounts/AccountPage';
import { CustomersPage } from '@/features/customers/CustomersPage';
import { AtRiskPage } from '@/features/customers/AtRiskPage';
import { ChurnHistoryPage } from '@/features/churn/ChurnHistoryPage';
import { ChurnReasonsSettingsPage } from '@/features/admin/ChurnReasonsSettingsPage';
import { LeaderboardPage } from '@/features/leaderboard/LeaderboardPage';
import { MyDashboardPage } from '@/features/dashboard/MyDashboardPage';
import { ReportsPage } from '@/features/reports/ReportsPage';
import { TargetsPage } from '@/features/team/TargetsPage';
import { IncentiveRatesPage } from '@/features/team/IncentiveRatesPage';
import { IncentivesPage } from '@/features/incentives/IncentivesPage';

/** Opening animation sits above whatever page loads first (usually Sign in). */
function Root() {
  const [splash, setSplash] = useState(shouldPlaySplash);
  return <>{splash && <Splash onDone={() => setSplash(false)} />}<Outlet /></>;
}

function Landing() {
  const { status, me } = useAuth();
  if (status === 'loading') return null;
  return <Navigate to={status === 'signed_in' && me ? homeFor(me.role) : '/login'} replace />;
}

function GoogleCallback() {
  const [q] = useSearchParams(); const nav = useNavigate();
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { const code = q.get('code'); if (!code) { setError('Missing sign-in code.'); return; } finishGoogleSignIn(code).then(() => window.location.replace('/')).catch((e) => setError(e.message)); }, [q, nav]);
  return <div className="grid h-full place-items-center text-ink-2">{error ?? 'Finishing Google sign-in…'}</div>;
}

const ADMINS = ['admin', 'super_admin'] as const;

/**
 * Routes grouped by build phase. A phase's routes are added here when its backend ships;
 * navigation.ts lists every page with its phase so the sidebar only shows released ones.
 */
const routes: RouteObject[] = [{
  element: <Root />,
  children: [
    { path: '/', element: <Landing /> },
    { path: '/login', element: <LoginPage /> },
    { path: '/auth/callback', element: <GoogleCallback /> },
    { path: '/app', element: <RequireAuth />, children: [{ element: <AppShell />, children: [
      // Phase 1 · The base
      { index: true, element: <HomePage /> },
      { path: 'approvals', element: <ApprovalsPage /> },
      { path: 'notifications', element: <NotificationsPage /> },
      { path: 'settings/notifications', element: <NotificationSettingsPage /> },
      { path: 'profile', element: <ProfilePage /> },
      { path: 'admin', element: <RequireRole roles={[...ADMINS]} />, children: [
        { path: 'users', element: <UsersPage /> },
        { path: 'teams', element: <TeamsPage /> },
        { path: 'jobs', element: <FailedJobsPage /> },
        { path: 'audit', element: <AuditPage /> }
      ] },
      { path: 'admin/permissions', element: <RequireRole roles={['super_admin']} />, children: [{ index: true, element: <PermissionsPage /> }] },
      // Phase 2 · Lead Pool (accounts, deals/:id and settings/assignment still to come)
      ...(ENV.releasePhase >= 2 ? [
        { path: 'leads/new', element: <NewLeadPage /> },
        { path: 'settings/assignment', element: <RequireRole roles={[...ADMINS]} />, children: [{ index: true, element: <AssignmentSettingsPage /> }] },
        { path: 'transfers', element: <RequireRole roles={['team_leader', ...ADMINS]} />, children: [{ index: true, element: <TransfersPage /> }] },
        { path: 'leads/review', element: <RequireRole roles={['team_leader', ...ADMINS]} />, children: [{ index: true, element: <CaptureReviewPage /> }] },
        { path: 'leads/import', element: <RequireRole roles={['team_leader', ...ADMINS]} />, children: [{ index: true, element: <ImportCsvPage /> }] },
        { path: 'pool', element: <RequireRole roles={['team_leader', ...ADMINS]} />, children: [{ index: true, element: <PoolPage /> }] }
      ] : []),
      // Phase 3 · New business pipeline (existing-customer stages use the same pages from Phase 5)
      ...(ENV.releasePhase >= 3 ? [
        { path: 'board', element: <BoardPage /> },
        { path: 'stages/:p/:key', element: <StagePage /> },
        { path: 'deals/:id', element: <DealPage /> },
        { path: 'my-today', element: <TodayPage /> },
        { path: 'won', element: <WonPage /> },
        { path: 'lost', element: <LostPage /> },
        { path: 'deleted', element: <RequireRole roles={['team_leader', ...ADMINS]} />, children: [{ index: true, element: <DeletedPage /> }] },
        { path: 'overview', element: <RequireRole roles={[...ADMINS]} />, children: [{ index: true, element: <OverviewPage /> }] },
        { path: 'settings', element: <RequireRole roles={[...ADMINS]} />, children: [{ path: 'pipeline', element: <PipelineSettingsPage /> }, { path: 'catalog', element: <CatalogPage /> }, { path: 'proposal-template', element: <ProposalTemplatePage /> }, { path: 'lists', element: <ListsSettingsPage /> }, { path: 'capture', element: <CaptureSourcesPage /> }] },
        { path: 'team', element: <RequireRole roles={['team_leader', ...ADMINS]} />, children: [{ index: true, element: <MyTeamPage /> }, { path: ':id', element: <RepPage /> }] }
      ] : []),
      // Phase 4 · Customers (built alongside Phase 5, whose renewal/cancellation/churn actions act on the same record)
      ...(ENV.releasePhase >= 4 ? [
        { path: 'accounts', element: <AccountsListPage /> },
        { path: 'accounts/:id', element: <AccountPage /> },
        { path: 'customers', element: <CustomersPage /> }
      ] : []),
      // Phase 5 · Existing-customer pipeline, renewals, cancellations, churn
      ...(ENV.releasePhase >= 5 ? [
        { path: 'customers/at-risk', element: <RequireRole roles={['team_leader', ...ADMINS]} />, children: [{ index: true, element: <AtRiskPage /> }] },
        { path: 'churn', element: <ChurnHistoryPage /> },
        { path: 'settings/churn-reasons', element: <RequireRole roles={[...ADMINS]} />, children: [{ index: true, element: <ChurnReasonsSettingsPage /> }] }
      ] : []),
      // Phase 6 · Performance: leaderboard, targets, incentives, personal dashboard, reports, forecast
      ...(ENV.releasePhase >= 6 ? [
        { path: 'leaderboard', element: <LeaderboardPage /> },
        { path: 'reports', element: <ReportsPage /> },
        { path: 'me/dashboard', element: <RequireRole roles={['user']} />, children: [{ index: true, element: <MyDashboardPage /> }] },
        { path: 'team/targets', element: <RequireRole roles={['team_leader', ...ADMINS]} />, children: [{ index: true, element: <TargetsPage /> }] },
        { path: 'incentives', element: <IncentivesPage /> },
        { path: 'team/incentive-rates', element: <RequireRole roles={[...ADMINS]} />, children: [{ index: true, element: <IncentiveRatesPage /> }] }
      ] : [])
    ] }] },
    { path: '*', element: <Navigate to="/" replace /> }
  ]
}];

const router = ENV.router === 'memory' ? createMemoryRouter(routes) : createBrowserRouter(routes);
export const App = () => <RouterProvider router={router} />;
