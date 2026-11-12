/**
 * The one owner of the connection-and-account session (react-guidelines §1). Holds the state
 * machine from `session.ts` and does what the reducer must not: storage, sign-in / refresh /
 * sign-out requests, and telling the query cache when an identity ends.
 *
 * Pages read it with `useSession()` (useSyncExternalStore) and call its commands from handlers.
 * Pure apart from the injected storage and `fetch`: runs under `node --test`.
 */
import { createApiClient, type ApiClient, type Credential, type RequestOptions } from '../api/client.ts';
import { errorCode, isApiError } from '../api/errors.ts';
import {
  identityOf,
  initialSession,
  persistedOf,
  sessionReducer,
  type Persisted,
  type Server,
  type SessionEvent,
  type SessionState,
  type Tokens,
  type User,
} from './session.ts';

export type SessionStorage = {
  load(): Promise<Persisted | null>;
  save(persisted: Persisted): Promise<void>;
};

type TokensResponse = { access_token: string; refresh_token: string; expires_at: number; user: User };

export function tokensOf(response: TokensResponse): { user: User; tokens: Tokens } {
  return {
    user: { id: response.user.id, email: response.user.email },
    tokens: { access: response.access_token, refresh: response.refresh_token, expiresAt: response.expires_at },
  };
}

export function createSessionStore(options: {
  storage: SessionStorage;
  fetch?: typeof fetch;
  /** an identity's cached data must go (sign-out, server switch) */
  onIdentityEnded?: (identity: string) => void;
}) {
  let state: SessionState = initialSession;
  const listeners = new Set<() => void>();
  let writes: Promise<void> = Promise.resolve();
  const refreshes = new Map<string, Promise<Credential | null>>();
  let client: { server: Server; api: ApiClient } | null = null;

  function dispatch(event: SessionEvent) {
    const previous = state;
    state = sessionReducer(state, event);
    if (state === previous) return;
    // What was just loaded need not be written back.
    const persisted = event.type === 'restored' ? null : persistedOf(state);
    if (persisted) writes = writes.then(() => options.storage.save(persisted)).catch(() => undefined);
    const ended = identityOf(previous);
    if (ended && ended !== identityOf(state)) options.onIdentityEnded?.(ended);
    for (const listener of listeners) listener();
  }

  function serverOf(current: SessionState): Server | null {
    return current.status === 'signed-out' || current.status === 'signed-in' ? current.server : null;
  }

  /** A server-token rejection anywhere means the deployment token changed: back to connect. */
  function watch<T>(promise: Promise<T>, server: Server): Promise<T> {
    return promise.catch((error: unknown) => {
      const current = serverOf(state);
      if (errorCode(error) === 'invalid_server_token' && current === server) {
        dispatch({ type: 'server-token-rejected' });
      }
      throw error;
    });
  }

  /**
   * The sign-in a credential belongs to is the signed-in state's `user` object: a refresh keeps
   * it (the reducer spreads the state), a new sign-in creates a new one. So a 401 answered for an
   * older sign-in, another account or another server never receives the current token.
   */
  function currentCredential(server: Server): Credential | null {
    return state.status === 'signed-in' && state.server === server ? { token: state.tokens.access, owner: state.user } : null;
  }

  async function refreshAccessToken(server: Server, rejected: Credential): Promise<Credential | null> {
    const current = state;
    if (current.status !== 'signed-in' || current.server !== server || current.user !== rejected.owner) return null;
    // Another request of the same sign-in already refreshed: use its token.
    if (current.tokens.access !== rejected.token) return currentCredential(server);
    const spent = current.tokens.refresh;
    const inFlight = refreshes.get(spent);
    if (inFlight) return inFlight;
    const job = (async (): Promise<Credential | null> => {
      const bare = createApiClient({ apiBaseUrl: server.apiBaseUrl, serverToken: server.serverToken, fetch: options.fetch });
      try {
        const response = await watch(
          bare.request<TokensResponse>('/auth/refresh', { method: 'POST', body: { refresh_token: spent }, authenticated: false }),
          server,
        );
        dispatch({ type: 'refreshed', spent, tokens: tokensOf(response).tokens });
      } catch (error) {
        if (isApiError(error) && error.problem.kind === 'http' && error.problem.status === 401) {
          // A password change of this sign-in revokes the old tokens on purpose: its new ones are
          // on their way, so wait for them rather than signing out.
          if (reissuing && reissuing.owner === rejected.owner) {
            await reissuing.done.catch(() => undefined);
            if (state.status === 'signed-in' && state.user === rejected.owner && state.tokens.refresh !== spent)
              return currentCredential(server);
          }
          // The session ended on the server (revoked, expired, or the refresh token was used elsewhere).
          if (state.status === 'signed-in' && state.tokens.refresh === spent) dispatch({ type: 'signed-out', reason: 'expired' });
          return null;
        }
        throw error; // offline etc.: keep the session, the caller shows the failure
      } finally {
        refreshes.delete(spent);
      }
      // Only if the same sign-in is still current (not signed out or replaced meanwhile).
      return state.status === 'signed-in' && state.user === rejected.owner ? currentCredential(server) : null;
    })();
    refreshes.set(spent, job);
    return job;
  }

  function apiFor(server: Server): ApiClient {
    if (client && client.server === server) return client.api;
    const api = createApiClient({
      apiBaseUrl: server.apiBaseUrl,
      serverToken: server.serverToken,
      fetch: options.fetch,
      tokens: {
        accessToken: () => currentCredential(server),
        refreshAccessToken: (rejected) => refreshAccessToken(server, rejected),
      },
    });
    const watched: ApiClient = { request: <T>(path: string, opts?: RequestOptions) => watch(api.request<T>(path, opts), server) };
    client = { server, api: watched };
    return watched;
  }

  let attempt: object | null = null;
  /** A password change in flight, for the sign-in (user object) that asked for it. */
  let reissuing: { owner: User; done: Promise<void> } | null = null;

  async function authenticate(path: '/auth/login' | '/auth/register', body: object): Promise<'done' | 'busy' | 'ignored'> {
    const server = serverOf(state);
    if (state.status !== 'signed-out' || !server) return 'ignored';
    if (attempt) return 'busy';
    const mine = {};
    attempt = mine;
    try {
      const response = await apiFor(server).request<TokensResponse>(path, { method: 'POST', body, authenticated: false });
      if (attempt !== mine) return 'ignored'; // abandoned meanwhile
      dispatch({ type: 'signed-in', server, ...tokensOf(response) });
      return 'done';
    } finally {
      if (attempt === mine) attempt = null;
    }
  }

  return {
    getSnapshot: () => state,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },

    async restore() {
      let persisted: Persisted | null = null;
      try {
        persisted = await options.storage.load();
      } catch {
        persisted = null; // unreadable storage: start over rather than crash
      }
      dispatch({ type: 'restored', persisted: persisted ?? { server: null, account: null } });
    },

    /** After discovery and the user's confirmation. */
    connect(server: Server) {
      dispatch({ type: 'connected', server });
    },

    /** The API of the current server; requests are signed in when the session is. */
    api(): ApiClient {
      const server = serverOf(state);
      // Routes that need the API are only mounted with a server (src/app/_layout.tsx).
      if (!server) throw new Error('No server connected');
      return apiFor(server);
    },

    /**
     * Sign in, or register and sign in. One attempt at a time: while one is in flight another
     * returns 'busy' without a request. Signing out or changing server abandons the attempt, so
     * its late success does not sign anyone in.
     */
    signIn(email: string, password: string) {
      return authenticate('/auth/login', { email, password });
    },

    register(email: string, password: string, displayName: string | null) {
      return authenticate('/auth/register', { email, password, display_name: displayName });
    },

    /**
     * Change the password. The server ends every session and starts a new one for this device,
     * whose tokens replace ours (signed out or switched meanwhile: they are not applied).
     * Errors (wrong_password …) are thrown as is.
     */
    async changePassword(currentPassword: string, password: string): Promise<void> {
      if (state.status !== 'signed-in') throw new Error('not signed in');
      const { server, user: owner } = state;
      const done = apiFor(server)
        .request<TokensResponse>('/me/password', { method: 'PATCH', body: { current_password: currentPassword, password } })
        .then((response) => dispatch({ type: 'reissued', server, owner, tokens: tokensOf(response).tokens }));
      reissuing = { owner, done };
      try {
        await done;
      } finally {
        if (reissuing?.done === done) reissuing = null;
      }
    },

    /** Signs out here at once; telling the server is best effort. */
    signOut() {
      attempt = null;
      if (state.status !== 'signed-in') return;
      const { server, tokens } = state;
      dispatch({ type: 'signed-out', reason: 'user' });
      void apiFor(server)
        .request('/auth/logout', { method: 'POST', body: { refresh_token: tokens.refresh }, authenticated: false, timeoutMs: 5_000 })
        .catch(() => undefined);
    },

    /** Change server: the current one (and its account) is forgotten on this device. */
    forgetServer() {
      attempt = null;
      if (state.status === 'signed-in') this.signOut();
      dispatch({ type: 'forget-server' });
    },

    /** Wait until queued storage writes are done (tests, and before the app is backgrounded). */
    flush: () => writes,
  };
}

export type SessionStore = ReturnType<typeof createSessionStore>;
