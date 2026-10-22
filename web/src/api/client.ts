import { ApiError } from './types';

const TOKEN_KEY = 'relay.token';
export const API_URL: string = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

let token: string | null = null;
try { token = sessionStorage.getItem(TOKEN_KEY); } catch { /* storage blocked: stay in memory */ }

export function setToken(t: string | null) {
  token = t;
  try { if (t) sessionStorage.setItem(TOKEN_KEY, t); else sessionStorage.removeItem(TOKEN_KEY); } catch { /* ignore */ }
}
export const getToken = () => token;

/** The server turned the action into an approval request (HTTP 202). */
export class ApprovalRequested {
  constructor(public approvalId: string) {}
}

let onUnauthorized: () => void = () => {};
export const setUnauthorizedHandler = (fn: () => void) => {
  onUnauthorized = fn;
};

const BODY_METHODS = new Set(['POST', 'PUT', 'PATCH']);

/**
 * Thin fetch wrapper for the FastAPI backend. Errors come back as {code, message}.
 * The backend reads `reason` as a field in the JSON body (there is no X-Reason header
 * convention), and every POST/PUT/PATCH route requires a body, even an empty one — so this
 * merges `reason` into whatever body was given, defaulting to `{}` when none was.
 */
export async function http<T>(
  path: string,
  init: RequestInit & { body?: unknown; reason?: string } = {},
): Promise<T | ApprovalRequested> {
  const headers = new Headers(init.headers);
  const method = (init.method ?? 'GET').toUpperCase();
  let body: string | undefined;
  if (BODY_METHODS.has(method)) {
    const obj = (init.body !== undefined ? JSON.parse(init.body as string) : {}) as Record<string, unknown>;
    if (init.reason !== undefined) obj.reason = init.reason;
    body = JSON.stringify(obj);
    headers.set('Content-Type', 'application/json');
  }
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const res = await fetch(API_URL + path, { ...init, headers, body });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (res.status === 202 && data.code === 'approval_requested') {
    return new ApprovalRequested(data.approval_id);
  }
  if (!res.ok) {
    if (res.status === 401) onUnauthorized();
    throw new ApiError(res.status, data.code ?? 'error', withDetails(data.message ?? res.statusText, data.details));
  }
  return data as T;
}

/**
 * The backend puts the specifics in `details`: `missing` (what a criterion or stage move still
 * needs) or `errors` (which request field failed validation). Without them the message alone is
 * just "Invalid request" / "Tick every exit criterion…", so fold them into the message.
 */
export function withDetails(message: string, details: unknown): string {
  const d = (details ?? {}) as { missing?: { label?: string; reason?: string }[]; errors?: { loc?: unknown[]; msg?: string }[] };
  const parts = d.missing?.length
    ? d.missing.map((m) => m.label ?? m.reason ?? '').filter(Boolean)
    : (d.errors ?? []).map((e) => `${(e.loc ?? []).filter((x) => x !== 'body').join('.')}: ${e.msg ?? ''}`);
  return parts.length ? `${message}: ${parts.join('; ')}` : message;
}

/** For reads: the result is never an approval request. */
export async function get<T>(path: string): Promise<T> {
  return (await http<T>(path)) as T;
}
