import { readerFetch } from './readerTransport';
import { resolveReaderUrl } from './connection/url';
import type { SyncStorage } from './connection/storage';
import { getActiveRuntimeSnapshot } from './connection/snapshot';
import { notifyServerTokenInvalid } from './connection/tokenEvents';
import { bindLocalizedErrorMessage, localizedMessage, resolveMessage } from '../i18n/message';

import type { AuthSessionResponse as Session, LoginRequest, RegisterRequest, ChangePasswordRequest, ChangeEmailRequest } from './generated/api';
export type { AuthUserResponse as ReaderUser, AuthSessionResponse as Session } from './generated/api';
type AuthEvent = 'SIGNED_IN' | 'SIGNED_OUT' | 'TOKEN_REFRESHED' | 'USER_UPDATED';
type AuthResult = { data: { session: Session | null }; error: Error | null };

export class ReaderAuthError extends Error {
  constructor(message: string, readonly status: number) {
    const descriptor = message === 'invalid_server_token' ? localizedMessage('errors:serverTokenInvalid')
      : message === 'Email or password is incorrect.' ? localizedMessage('errors:authCredentialsInvalid')
      : message === 'Current password is incorrect.' ? localizedMessage('errors:authCurrentPasswordInvalid')
      : message === 'Email is already registered.' ? localizedMessage('errors:authEmailRegistered')
      : status === 429 ? localizedMessage('errors:authRateLimited')
      : status === 401 ? localizedMessage('errors:authSessionExpired')
      : status >= 500 ? localizedMessage('errors:authUnavailable') : null;
    super(descriptor ? resolveMessage(descriptor) : message);
    if (descriptor) bindLocalizedErrorMessage(this, descriptor);
  }
}

/** Reader-owned session lifecycle, isolated to one server and API base URL. */
export function createReaderAuthClient(apiBaseUrl: string, serverToken: string, storage: SyncStorage) {
  let session: Session | null = null;
  let loaded = false;
  let epoch = 0;
  let refresh: Promise<Session | null> | null = null;
  let mutation: Promise<AuthResult> | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let running = false;
  const requests = new Set<AbortController>();
  const listeners = new Set<(event: AuthEvent, next: Session | null) => void>();

  // A storage consistency tag, not an authentication hash. This detects an
  // interrupted two-entry write without duplicating the refresh credential.
  function refreshBinding(token: string): string {
    let hash = 2166136261;
    for (const char of token) hash = Math.imul(hash ^ char.charCodeAt(0), 16777619);
    return (hash >>> 0).toString(16);
  }

  function load() {
    if (loaded) return;
    loaded = true;
    try {
      const raw = storage.getItem('session');
      const token = storage.getItem('refresh');
      if (!raw || !token) return;
      const value = JSON.parse(raw);
      if (value.refresh_binding && value.refresh_binding !== refreshBinding(token)) return;
      if (typeof value.access_token === 'string' && typeof value.expires_at === 'number' &&
          typeof value.user?.id === 'string' && typeof value.user?.email === 'string') {
        session = { ...value, refresh_token: token };
      }
    } catch { session = null; }
  }

  function schedule() {
    if (timer) clearTimeout(timer);
    timer = null;
    if (!running || !session) return;
    timer = setTimeout(() => {
      timer = null;
      void refreshSession().catch(() => {
        if (running && session) timer = setTimeout(() => { void refreshSession().catch(() => undefined); }, 30_000);
      });
    }, Math.max(1_000, (session.expires_at * 1_000) - Date.now() - 60_000));
    // Node test runners must not be kept alive by a background session timer.
    (timer as unknown as { unref?: () => void }).unref?.();
  }

  function publish(next: Session | null, event: AuthEvent) {
    if (next) {
      const { refresh_token, ...rest } = next;
      storage.setItem('refresh', refresh_token);
      storage.setItem('session', JSON.stringify({ ...rest, refresh_binding: refreshBinding(refresh_token) }));
    } else {
      session = null;
      loaded = true;
      try {
        storage.removeItem('refresh');
        storage.removeItem('session');
      } finally {
        schedule();
        for (const listener of listeners) listener(event, null);
      }
      return;
    }
    session = next;
    loaded = true;
    schedule();
    for (const listener of listeners) listener(event, next);
  }

  async function request(path: string, body: unknown, method = 'POST', accessToken?: string) {
    const controller = new AbortController();
    requests.add(controller);
    const timeout = setTimeout(() => controller.abort(), 15_000);
    try {
      const response = await readerFetch(resolveReaderUrl(apiBaseUrl, `/v1/auth/${path}`), {
        method, redirect: 'error', signal: controller.signal,
        headers: {
          Accept: 'application/json', 'Content-Type': 'application/json',
          'X-Reader-Server-Token': serverToken,
          ...(accessToken ? { Authorization: `Bearer ${accessToken}` } : {}),
        },
        body: JSON.stringify(body),
      });
      if (response.status === 204) return null;
      const value = await response.json();
      if (!response.ok) {
        if (value?.detail === 'invalid_server_token') {
          const active = getActiveRuntimeSnapshot();
          if (active?.authClient.auth === auth) notifyServerTokenInvalid(active);
        }
        throw new ReaderAuthError(typeof value.detail === 'string' ? value.detail : resolveMessage(localizedMessage('errors:authUnavailable')), response.status);
      }
      if (!value || typeof value.access_token !== 'string' || typeof value.refresh_token !== 'string' ||
          typeof value.expires_at !== 'number' || typeof value.user?.id !== 'string' || typeof value.user?.email !== 'string') {
        throw new Error('Invalid Reader session response.');
      }
      return value as Session;
    } finally { requests.delete(controller); clearTimeout(timeout); }
  }

  async function refreshSession(): Promise<Session | null> {
    // Password/email changes rotate the entire session family. A refresh
    // starting during that request must wait and read the resulting family.
    if (mutation) {
      const waitingEpoch = epoch;
      await mutation;
      if (waitingEpoch !== epoch) return session;
      return refreshSession();
    }
    load();
    if (!session) return null;
    if (refresh) return refresh;
    const expectedEpoch = epoch;
    const token = session.refresh_token;
    const pending = (async () => {
      try {
        const next = await request('refresh', { refresh_token: token });
        if (epoch !== expectedEpoch) return session;
        publish(next, 'TOKEN_REFRESHED');
        return next;
      } catch (error) {
        if (epoch === expectedEpoch && error instanceof ReaderAuthError && error.status === 401 &&
            error.message !== resolveMessage(localizedMessage('errors:serverTokenInvalid'))) publish(null, 'SIGNED_OUT');
        throw error;
      }
    })();
    refresh = pending;
    try { return await pending; } finally { if (refresh === pending) refresh = null; }
  }

  async function mutate(path: string, body: unknown, method = 'POST'): Promise<AuthResult> {
    const startedEpoch = epoch;
    if (mutation) await mutation;
    if (startedEpoch !== epoch) return { data: { session: null }, error: new Error('This account operation expired.') };
    // Account changes require a current JWT and must not race token rotation.
    if (method === 'PATCH') {
      try {
        if (refresh) await refresh;
        if (epoch !== startedEpoch) return { data: { session: null }, error: new Error('This account operation expired.') };
        await auth.getAccessToken();
      } catch (error) {
        return { data: { session: null }, error: error instanceof Error ? error : new Error('Authentication failed.') };
      }
      if (epoch !== startedEpoch) return { data: { session: null }, error: new Error('This account operation expired.') };
    }
    const expectedEpoch = ++epoch;
    refresh = null;
    const pending = (async (): Promise<AuthResult> => {
      try {
        const next = await request(path, body, method, session?.access_token);
        if (expectedEpoch !== epoch) return { data: { session: null }, error: new Error('This account operation expired.') };
        publish(next, path === 'login' || path === 'register' ? 'SIGNED_IN' : 'USER_UPDATED');
        return { data: { session: next }, error: null };
      } catch (error) { return { data: { session: null }, error: error instanceof Error ? error : new Error('Authentication failed.') }; }
    })();
    mutation = pending;
    try { return await pending; } finally { if (mutation === pending) mutation = null; }
  }

  const auth = {
    async getSession(): Promise<AuthResult> {
      load();
      // Candidate restoration is read-only. Refresh only after atomic runtime
      // activation, so a candidate cannot rotate the active runtime's token.
      return { data: { session }, error: null };
    },
    async getAccessToken(): Promise<string | null> {
      load();
      if (session && session.expires_at * 1_000 <= Date.now() + 30_000) await refreshSession();
      return session?.access_token ?? null;
    },
    refreshSession,
    signInWithPassword: (body: LoginRequest) => mutate('login', body),
    signUp: (body: RegisterRequest) => mutate('register', body),
    updatePassword: (body: ChangePasswordRequest) => mutate('password', body, 'PATCH'),
    updateEmail: (body: ChangeEmailRequest) => mutate('email', body, 'PATCH'),
    async signOut() {
      load();
      const token = session?.refresh_token;
      ++epoch;
      refresh = null;
      mutation = null;
      try { publish(null, 'SIGNED_OUT'); }
      finally { if (token) await request('logout', { refresh_token: token }); }
    },
    onAuthStateChange(listener: (event: AuthEvent, next: Session | null) => void) {
      listeners.add(listener);
      return { data: { subscription: { unsubscribe: () => { listeners.delete(listener); } } } };
    },
    startAutoRefresh() { running = true; schedule(); },
    stopAutoRefresh() {
      running = false;
      ++epoch;
      refresh = null;
      mutation = null;
      for (const request of requests) request.abort();
      if (timer) clearTimeout(timer);
      timer = null;
    },
  };
  return { auth };
}

export type ReaderAuthClient = ReturnType<typeof createReaderAuthClient>;
