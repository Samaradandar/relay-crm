import { Navigate, Outlet, useLocation } from 'react-router-dom';
import type { Role } from '@/api/types';
import { useAuth } from './AuthProvider';

export function RequireAuth() {
  const { status } = useAuth();
  const loc = useLocation();
  if (status === 'loading') return <div className="grid h-full place-items-center text-ink-3">Loading…</div>;
  if (status === 'signed_out') return <Navigate to="/login" replace state={{ from: loc.pathname }} />;
  return <Outlet />;
}

/** Hides a whole route from roles that may never open it (PRD: hide, don't disable). */
export function RequireRole({ roles }: { roles: Role[] }) {
  const { me } = useAuth();
  if (!me || !roles.includes(me.role)) return <Navigate to="/app" replace />;
  return <Outlet />;
}
