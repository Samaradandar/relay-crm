import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '@/api';
import { ApiError } from '@/api/types';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { useToast } from '@/components/ui/Feedback';
import { ReasonDialog } from '@/features/team/TeamBits';

/** Who the contact is in the deal: the person who signs (decision maker) or one who advises
 * (influencer). A new lead's contact is an influencer unless someone says otherwise. */
export function ContactTypeBadge({ decisionMaker }: { decisionMaker?: boolean }) {
  return decisionMaker ? <Badge tone="brand" icon="shield">Decision maker</Badge> : <Badge>Influencer</Badge>;
}

/** One click to change the type at any stage of the deal. The API decides who may (the owner,
 * their Team Leader, Admins); an Admin is asked for a reason. */
export function ContactTypeSwitch({ contactId, name, decisionMaker }: { contactId: string; name: string; decisionMaker?: boolean }) {
  const qc = useQueryClient(); const toast = useToast();
  const [ask, setAsk] = useState(false); const [error, setError] = useState<string | null>(null);
  const to = !decisionMaker;
  const change = useMutation({
    mutationFn: (reason?: string) => api.setContactType(contactId, to, reason),
    onSuccess: () => {
      setAsk(false); setError(null);
      ['deal', 'deals', 'stagePage', 'needsYou'].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      toast({ message: to ? `${name} is now the decision maker` : `${name} is now an influencer. If they were the only decision maker, “Decision maker identified” shows Proof missing until another contact is tagged.` });
    },
    onError: (e) => { if (e instanceof ApiError && e.code === 'reason_required') { setAsk(true); return; } setAsk(false); setError(e instanceof ApiError ? e.message : 'Could not change it.'); },
  });
  return <>
    <Button size="sm" variant="quiet" loading={change.isPending && !ask} onClick={() => { setError(null); change.mutate(undefined); }}>{to ? 'Make decision maker' : 'Mark as influencer'}</Button>
    {error && <span className="basis-full text-xs text-danger">{error}</span>}
    {ask && <ReasonDialog title={`Make ${name} ${to ? 'the decision maker' : 'an influencer'}?`} description={!to ? 'If this person was the proof for “Decision maker identified”, that exit criterion shows Proof missing until another contact is tagged.' : undefined} action="Change" busy={change.isPending} onClose={() => setAsk(false)} onConfirm={(reason) => change.mutate(reason)} />}
  </>;
}
