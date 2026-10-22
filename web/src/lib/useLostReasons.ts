import { useQuery } from '@tanstack/react-query';
import { api } from '@/api';
import { LOST_REASONS } from '@/lib/pipeline';

/** The reasons offered when a deal is marked Lost: the list an Admin maintains (the built-in list in the demo). */
export function useLostReasons(): string[] {
  const q = useQuery({ queryKey: ['lostReasons'], queryFn: () => api.lostReasons(), staleTime: 60_000 });
  return q.data && q.data.length ? q.data : [...LOST_REASONS];
}
