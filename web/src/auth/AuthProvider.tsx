import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api } from '@/api';
import { ApiError, type Me } from '@/api/types';
import { ENV } from '@/config/env';
import { startGoogleSignIn } from './cognito';

interface AuthState {
  me: Me | null;
  status: 'loading' | 'signed_out' | 'signed_in';
  signInWithPassword(email: string, password: string): Promise<Me>;
  signInWithGoogle(): Promise<void>;
  /** Mock mode: a user id. Live mode: an email from a local dev account (see api.devUsers()). */
  signInAs(idOrEmail: string): Promise<Me>;
  signOut(): Promise<void>;
}

const Ctx = createContext<AuthState | null>(null);

/**
 * The person's role is never chosen on the sign-in page: it comes from their account (/me).
 * After sign-in, routes send each role to its own home (see auth/navigation.ts).
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [status, setStatus] = useState<AuthState['status']>('loading');

  useEffect(() => {
    api.me().then((m) => { setMe(m); setStatus('signed_in'); }).catch(() => setStatus('signed_out'));
  }, []);

  const done = useCallback((m: Me) => { setMe(m); setStatus('signed_in'); return m; }, []);
  const value = useMemo<AuthState>(() => ({
    me, status,
    signInWithPassword: (e, p) => api.signInWithPassword(e, p).then(done),
    signInWithGoogle: async () => {
      if (ENV.apiMode === 'mock') throw new ApiError(400, 'mock', 'Google sign-in needs the live backend. Use a sample account below.');
      await startGoogleSignIn();
    },
    signInAs: (id) => api.signInAs(id).then(done),
    signOut: async () => { await api.signOut(); setMe(null); setStatus('signed_out'); }
  }), [me, status, done]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth() {
  const v = useContext(Ctx);
  if (!v) throw new Error('useAuth must be used inside <AuthProvider>');
  return v;
}
