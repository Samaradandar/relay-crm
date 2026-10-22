import { ENV } from '@/config/env';
import { setToken } from '@/api/client';

/** Google sign-in through the Cognito hosted UI (authorization code + PKCE). Live mode only. */
const b64url = (buf: ArrayBuffer) => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

export async function startGoogleSignIn() {
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)).buffer);
  const challenge = b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)));
  sessionStorage.setItem('relay.pkce', verifier);
  const q = new URLSearchParams({
    identity_provider: 'Google', client_id: ENV.cognito.clientId, response_type: 'code',
    redirect_uri: ENV.cognito.redirectUri, scope: 'openid email profile', code_challenge_method: 'S256', code_challenge: challenge
  });
  window.location.assign(`${ENV.cognito.domain}/oauth2/authorize?${q}`);
}

/** Called on /auth/callback: swaps the code for tokens; the API then matches the email to a CRM user. */
export async function finishGoogleSignIn(code: string) {
  const body = new URLSearchParams({
    grant_type: 'authorization_code', client_id: ENV.cognito.clientId, code,
    redirect_uri: ENV.cognito.redirectUri, code_verifier: sessionStorage.getItem('relay.pkce') ?? ''
  });
  const res = await fetch(`${ENV.cognito.domain}/oauth2/token`, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body });
  if (!res.ok) throw new Error('Google sign-in did not complete. Try again.');
  const t = await res.json();
  setToken(t.id_token);
  sessionStorage.removeItem('relay.pkce');
}
