import { useState, type ReactNode } from 'react';
import { ApiError } from '@/api/types';
import { useAuth } from '@/auth/AuthProvider';
import { Button } from './Button';
import { Banner, useToast } from './Feedback';
import { TextAreaField } from './Field';
import { Dialog } from './Overlay';

interface Ask { title: string; description?: ReactNode; action?: string; danger?: boolean; done: string; run: (reason: string) => Promise<unknown> }

/**
 * Every settings change asks for a reason first (PRD 4.2): required for Admins, optional for a Super admin.
 * Returns `ask(...)` and the dialog element to render once in the page.
 */
export function useReasonPrompt(onDone?: () => void) {
  const { me } = useAuth(); const toast = useToast();
  const [cur, setCur] = useState<Ask | null>(null); const [reason, setReason] = useState(''); const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const optional = me?.role === 'super_admin';
  const ask = (a: Ask) => { setCur(a); setReason(''); setErr(null); };
  const confirm = async () => {
    if (!cur) return; setBusy(true); setErr(null);
    try { await cur.run(reason); toast({ message: cur.done }); setCur(null); onDone?.(); } catch (e) { setErr(e instanceof ApiError ? e.message : 'Could not save.'); } finally { setBusy(false); }
  };
  const element = cur && <Dialog title={cur.title} description={cur.description} onClose={() => setCur(null)}
    footer={<><Button variant="quiet" onClick={() => setCur(null)}>Cancel</Button><Button variant={cur.danger ? 'danger' : 'primary'} disabled={!optional && !reason.trim()} loading={busy} onClick={confirm}>{cur.action ?? 'Save'}</Button></>}>
    <TextAreaField label="Reason" required={!optional} optionalNote={optional ? '(optional for Super admin)' : undefined} rows={3} value={reason} onChange={(e) => setReason(e.target.value)} hint="Saved in the audit log with the old and new value." />
    {err && <Banner tone="danger" title={err} />}
  </Dialog>;
  return { ask, element };
}
