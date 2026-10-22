import { useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import type { Lead } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { SelectField } from '@/components/ui/Field';

/** "Whole team / Mine / one rep" for Team Leaders and Admins (Feature 4: per-rep filter). Kept in ?owner= so links can open it. */
export function useOwnerFilter() {
  const { me } = useAuth(); const [q, setQ] = useSearchParams();
  const value = q.get('owner') ?? '';
  const show = me?.role !== 'user';
  const set = (v: string) => { const nq = new URLSearchParams(q); if (v) nq.set('owner', v); else nq.delete('owner'); setQ(nq, { replace: true }); };
  const match = (d: Lead) => !value || (value === 'me' ? d.ownerId === me?.id : d.ownerId === value);
  return { value, set, show, match };
}

export function OwnerFilter({ f }: { f: ReturnType<typeof useOwnerFilter> }) {
  const { me } = useAuth();
  const users = useQuery({ queryKey: ['users'], queryFn: api.users, enabled: f.show });
  if (!f.show) return null;
  const reps = (users.data ?? []).filter((u) => u.role === 'user' && u.active && (me?.role !== 'team_leader' || u.teamId === me.teamId));
  return <SelectField label="Owner" value={f.value} onChange={(e) => f.set(e.target.value)} className="w-48"
    options={[{ value: '', label: me?.role === 'team_leader' ? 'Whole team' : 'Everyone' }, { value: 'me', label: 'Mine' }, ...reps.map((u) => ({ value: u.id, label: u.name }))]} />;
}
