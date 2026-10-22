import { useEffect, useState, type FormEvent } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import { useAuth } from '@/auth/AuthProvider';
import { homeFor } from '@/auth/navigation';
import { ROLE_LABEL } from '@/auth/permissions';
import { api, mockUsers } from '@/api';
import { ApiError, type Me, type Role } from '@/api/types';
import { BRAND } from '@/config/brand';
import { ENV } from '@/config/env';
import { Button } from '@/components/ui/Button';
import { TextField } from '@/components/ui/Field';
import { Banner } from '@/components/ui/Feedback';
import { Avatar } from '@/components/ui/Badge';
import { Icon } from '@/components/ui/Icon';
import './login.css';

const ROLE_HOME: { role: Role; what: string }[] = [
  { role: 'user', what: 'Your deals, Today list and requests' },
  { role: 'team_leader', what: 'Your team, its approvals and reps' },
  { role: 'admin', what: 'Every team, users and settings, with reasons' },
  { role: 'super_admin', what: 'Everything, including Admins' }
];

export function LoginPage() {
  const { status, me, signInWithPassword, signInWithGoogle, signInAs } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [welcome, setWelcome] = useState<Me | null>(null);
  // Live mode, no Cognito: the backend's dev-only /auth/dev/users route (local development only).
  const [devUsers, setDevUsers] = useState<{ email: string; name: string; role: Role }[] | null>(null);
  useEffect(() => {
    if (ENV.apiMode !== 'live') return;
    api.devUsers().then(setDevUsers).catch(() => setDevUsers([]));
  }, []);

  // Already signed in when the page opened (not mid sign-in): go straight to this role's home.
  if (status === 'signed_in' && me && !welcome && !busy) return <Navigate to={homeFor(me.role)} replace />;

  const finish = (m: Me) => { setWelcome(m); window.setTimeout(() => nav(homeFor(m.role), { replace: true }), 1100); };
  const run = async (key: string, fn: () => Promise<Me | void>) => {
    setError(null); setBusy(key);
    try { const m = await fn(); if (m) { finish(m); return; } }
    catch (e) { setError(e instanceof ApiError ? e.message : 'Sign-in failed. Check your connection and try again.'); }
    setBusy(null);
  };
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!email.trim() || !password) { setError('Enter your work email and password.'); return; }
    run('password', () => signInWithPassword(email, password));
  };

  return (
    <div className="login">
      <aside className="login__brand" aria-label={BRAND.name}>
        <div className="login__brand-bg" aria-hidden="true"><span /><span /><span /></div>
        <div className="login__logo rise" style={{ ['--d' as string]: '0s' }}>
          <img src={BRAND.logo.mark} alt="" className="login__logo-mark" />
          <img src={BRAND.logo.wordmarkWhite} alt={BRAND.name} className="login__logo-word" />
        </div>
        <div className="login__pitch rise" style={{ ['--d' as string]: '.08s' }}>
          <h1>{BRAND.promise}</h1>
          <p>Sign in once. Relay knows whether you are a sales rep, Team Leader, Admin or Super admin, and opens your own workspace.</p>
        </div>
        <ul className="login__roles rise" style={{ ['--d' as string]: '.16s' }}>
          {ROLE_HOME.map((r) => <li key={r.role}><span className="login__role">{ROLE_LABEL[r.role]}</span><span>{r.what}</span></li>)}
        </ul>
        <p className="login__tag rise" style={{ ['--d' as string]: '.24s' }}>{BRAND.tagline}</p>
      </aside>

      <main className="login__panel">
        {welcome ? (
          <div className="login__welcome" role="status">
            <Avatar name={welcome.name} size={56} />
            <h2 className="text-title">Welcome back, {welcome.name.split(' ')[0]}</h2>
            <p className="text-ink-2">Opening your {ROLE_LABEL[welcome.role]} workspace{welcome.team ? ` · ${welcome.team.name}` : ''}…</p>
            <div className="login__bar"><span /></div>
          </div>
        ) : (
          <div className="login__form-wrap">
            <div className="rise" style={{ ['--d' as string]: '.1s' }}>
              <h2 className="text-display">Sign in</h2>
              <p className="mt-1 text-ink-2">Use your company Google account or your Relay email and password.</p>
            </div>

            <div className="rise flex flex-col gap-4" style={{ ['--d' as string]: '.18s' }}>
              <button type="button" className="login__google" onClick={() => run('google', signInWithGoogle)} disabled={!!busy}>
                <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true"><path fill="#4285F4" d="M23.5 12.3c0-.8-.1-1.6-.2-2.3H12v4.5h6.5a5.6 5.6 0 0 1-2.4 3.6v3h3.9c2.2-2.1 3.5-5.1 3.5-8.8z" /><path fill="#34A853" d="M12 24c3.2 0 6-1.1 7.9-2.9l-3.9-3c-1.1.7-2.4 1.2-4 1.2-3.1 0-5.7-2.1-6.6-4.9h-4v3.1A12 12 0 0 0 12 24z" /><path fill="#FBBC05" d="M5.4 14.4a7.2 7.2 0 0 1 0-4.7V6.6h-4a12 12 0 0 0 0 10.8z" /><path fill="#EA4335" d="M12 4.8c1.7 0 3.3.6 4.5 1.8l3.4-3.4A12 12 0 0 0 1.4 6.6l4 3.1C6.3 6.9 8.9 4.8 12 4.8z" /></svg>
                {busy === 'google' ? 'Opening Google…' : 'Continue with Google'}
              </button>
              <div className="login__or"><span />or<span /></div>
              <form onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
                <TextField id="email" label="Work email" type="email" autoComplete="username" placeholder="you@adandar.com" value={email} onChange={(e) => setEmail(e.target.value)} />
                <div className="relative">
                  <TextField id="password" label="Password" type={show ? 'text' : 'password'} autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
                  <button type="button" className="login__peek" onClick={() => setShow((s) => !s)} aria-label={show ? 'Hide password' : 'Show password'} aria-pressed={show}><Icon name="eye" /></button>
                </div>
                <div className="flex justify-end"><a href="#forgot" className="text-[13px] font-semibold text-brand-ink no-underline hover:underline">Forgot password?</a></div>
                {error && <Banner tone="danger" title={error} />}
                <Button type="submit" variant="primary" className="h-11 w-full" loading={busy === 'password'}>Sign in</Button>
              </form>
            </div>

            {ENV.apiMode === 'mock' ? (
              <section className="login__samples rise" style={{ ['--d' as string]: '.26s' }} aria-label="Sample accounts">
                <p className="text-overline text-ink-3">Sample accounts · preview only</p>
                <div className="grid gap-2 sm:grid-cols-2">
                  {pickSamples().map((u) => (
                    <button key={u.id} type="button" className="login__sample" disabled={!!busy} onClick={() => run(u.id, () => signInAs(u.id))}>
                      <Avatar name={u.name} size={30} />
                      <span className="flex min-w-0 flex-col text-left"><span className="truncate font-semibold text-ink">{u.name}</span><span className="text-xs text-ink-3">{ROLE_LABEL[u.role]}</span></span>
                      {busy === u.id && <span className="ml-auto h-3.5 w-3.5 animate-spin rounded-full border-2 border-brand border-t-transparent" />}
                    </button>
                  ))}
                </div>
                <p className="text-xs text-ink-3">Or type any sample email with the password <code className="text-mono">relay123</code>.</p>
              </section>
            ) : devUsers && devUsers.length > 0 ? (
              <section className="login__samples rise" style={{ ['--d' as string]: '.26s' }} aria-label="Local development login">
                <p className="text-overline text-ink-3">Local development login</p>
                <div className="grid gap-2 sm:grid-cols-2">
                  {devUsers.map((u) => (
                    <button key={u.email} type="button" className="login__sample" disabled={!!busy} onClick={() => run(u.email, () => signInAs(u.email))}>
                      <Avatar name={u.name} size={30} />
                      <span className="flex min-w-0 flex-col text-left"><span className="truncate font-semibold text-ink">{u.name}</span><span className="text-xs text-ink-3">{ROLE_LABEL[u.role]}</span></span>
                      {busy === u.email && <span className="ml-auto h-3.5 w-3.5 animate-spin rounded-full border-2 border-brand border-t-transparent" />}
                    </button>
                  ))}
                </div>
              </section>
            ) : (
              <p className="rise text-small text-ink-3" style={{ ['--d' as string]: '.26s' }}>No account yet? Accounts are created by an Admin. Ask your Team Leader to request one.</p>
            )}
          </div>
        )}
      </main>
    </div>
  );
}

function pickSamples() {
  // One per role; Priya and her Team Leader Rohit share Team North so the Pool story lines up.
  const prefer: Record<Role, string> = { user: 'u-priya', team_leader: 'u-rohit', admin: 'u-arjun', super_admin: 'u-vikram' };
  const users = mockUsers();
  return (Object.keys(prefer) as Role[]).map((r) => users.find((u) => u.id === prefer[r]) ?? users.find((u) => u.role === r)!).filter(Boolean);
}
