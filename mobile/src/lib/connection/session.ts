/**
 * The connection-and-account session as a state machine: which server, who is signed in.
 *
 *   restoring ──restored──▶ no-server | signed-out | signed-in
 *   no-server ──connected──▶ signed-out
 *   signed-out ──signed-in──▶ signed-in
 *   signed-in ──refreshed──▶ signed-in (new tokens, same user)
 *   signed-in ──signed-out──▶ signed-out (user action, or the session ended on the server)
 *   signed-out | signed-in ──server-token-rejected──▶ no-server, keeping the address to re-enter the token
 *   any ready state ──forget-server──▶ no-server
 *
 * Pure (DESIGN: react-guidelines §2): no storage, no requests, no clock. The owner in
 * `session-store.ts` performs those and feeds the results back as events.
 */

export type Server = {
  /** what the user typed, normalised */
  base: string;
  /** from discovery; requests go here */
  apiBaseUrl: string;
  serverId: string;
  serverToken: string;
};

export type User = { id: string; email: string };

export type Tokens = { access: string; refresh: string; /** epoch seconds */ expiresAt: number };

export type SignedOutReason = 'user' | 'expired' | null;

export type SessionState =
  | { status: 'restoring' }
  | { status: 'no-server'; /** prefill after the server token was rejected */ lastBase?: string; tokenRejected?: boolean }
  | { status: 'signed-out'; server: Server; reason: SignedOutReason }
  | { status: 'signed-in'; server: Server; user: User; tokens: Tokens };

/** What survives an app restart. Secrets (server token, refresh token) are stored securely. */
export type Persisted = { server: Server | null; account: { user: User; tokens: Tokens } | null };

export type SessionEvent =
  | { type: 'restored'; persisted: Persisted }
  | { type: 'connected'; server: Server }
  | { type: 'signed-in'; /** the server the sign-in was sent to */ server: Server; user: User; tokens: Tokens }
  | { type: 'refreshed'; /** the refresh token that was spent */ spent: string; tokens: Tokens }
  | { type: 'signed-out'; reason: Exclude<SignedOutReason, null> }
  | { type: 'server-token-rejected' }
  | { type: 'forget-server' };

export const initialSession: SessionState = { status: 'restoring' };

export function sessionReducer(state: SessionState, event: SessionEvent): SessionState {
  switch (state.status) {
    case 'restoring':
      if (event.type !== 'restored') return state;
      if (!event.persisted.server) return { status: 'no-server' };
      if (!event.persisted.account) return { status: 'signed-out', server: event.persisted.server, reason: null };
      return { status: 'signed-in', server: event.persisted.server, ...event.persisted.account };

    case 'no-server':
      if (event.type === 'connected') return { status: 'signed-out', server: event.server, reason: null };
      return state;

    case 'signed-out':
      // A sign-in sent over an earlier connection (the user switched or reconnected while it was in
      // flight, even to the same server) does not apply: each `connected` makes a new Server object.
      if (event.type === 'signed-in') {
        return event.server === state.server ? { status: 'signed-in', server: state.server, user: event.user, tokens: event.tokens } : state;
      }
      if (event.type === 'server-token-rejected') return { status: 'no-server', lastBase: state.server.base, tokenRejected: true };
      if (event.type === 'forget-server') return { status: 'no-server', lastBase: state.server.base };
      return state;

    case 'signed-in':
      // A refresh that finished after another one replaced the tokens is stale: ignore it.
      if (event.type === 'refreshed') return event.spent === state.tokens.refresh ? { ...state, tokens: event.tokens } : state;
      if (event.type === 'signed-out') return { status: 'signed-out', server: state.server, reason: event.reason };
      if (event.type === 'server-token-rejected') return { status: 'no-server', lastBase: state.server.base, tokenRejected: true };
      if (event.type === 'forget-server') return { status: 'no-server', lastBase: state.server.base };
      return state;
  }
}

/** What to keep on the device for this state. */
export function persistedOf(state: SessionState): Persisted | null {
  switch (state.status) {
    case 'restoring':
      return null; // nothing decided yet: do not overwrite storage
    case 'no-server':
      return { server: null, account: null };
    case 'signed-out':
      return { server: state.server, account: null };
    case 'signed-in':
      return { server: state.server, account: { user: state.user, tokens: state.tokens } };
  }
}

/**
 * The cache identity (react-guidelines §4.2): server + account. Queries of one identity are never
 * shown to another; signing out or switching server clears the old identity's cache.
 */
export function identityOf(state: SessionState): string | null {
  return state.status === 'signed-in' ? `${state.server.serverId}/${state.user.id}` : null;
}
