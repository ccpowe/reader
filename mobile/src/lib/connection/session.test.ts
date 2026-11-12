import assert from 'node:assert/strict';
import { test } from 'node:test';

import { identityOf, initialSession, persistedOf, sessionReducer, type Server, type SessionState, type Tokens } from './session.ts';

const server: Server = { base: 'https://r.example', apiBaseUrl: 'https://r.example', serverId: 'srv-1', serverToken: 'st' };
const user = { id: 'u1', email: 'me@example.com' };
const tokens = (n: number): Tokens => ({ access: `a${n}`, refresh: `r${n}`, expiresAt: 1000 + n });
const signedIn: SessionState = { status: 'signed-in', server, user, tokens: tokens(1) };

test('restores to the right place', () => {
  assert.deepEqual(sessionReducer(initialSession, { type: 'restored', persisted: { server: null, account: null } }), { status: 'no-server' });
  assert.deepEqual(sessionReducer(initialSession, { type: 'restored', persisted: { server, account: null } }), {
    status: 'signed-out',
    server,
    reason: null,
  });
  assert.deepEqual(sessionReducer(initialSession, { type: 'restored', persisted: { server, account: { user, tokens: tokens(1) } } }), signedIn);
});

test('restoring ignores everything but the restore', () => {
  assert.equal(sessionReducer(initialSession, { type: 'signed-out', reason: 'user' }), initialSession);
  assert.equal(sessionReducer(initialSession, { type: 'connected', server }), initialSession);
});

test('connect, then sign in, then sign out', () => {
  let state: SessionState = { status: 'no-server' };
  state = sessionReducer(state, { type: 'connected', server });
  assert.deepEqual(state, { status: 'signed-out', server, reason: null });
  state = sessionReducer(state, { type: 'signed-in', server, user, tokens: tokens(1) });
  assert.deepEqual(state, signedIn);
  state = sessionReducer(state, { type: 'signed-out', reason: 'expired' });
  assert.deepEqual(state, { status: 'signed-out', server, reason: 'expired' });
});

test('only the refresh of the current tokens is applied', () => {
  const next = sessionReducer(signedIn, { type: 'refreshed', spent: 'r1', tokens: tokens(2) });
  assert.deepEqual(next, { ...signedIn, tokens: tokens(2) });
  // a late result of an older refresh
  assert.equal(sessionReducer(next, { type: 'refreshed', spent: 'r1', tokens: tokens(3) }), next);
  // a refresh finishing after sign-out
  const out = sessionReducer(next, { type: 'signed-out', reason: 'user' });
  assert.equal(sessionReducer(out, { type: 'refreshed', spent: 'r2', tokens: tokens(4) }), out);
});

test('a password change replaces the tokens of the same account only', () => {
  const owner = (signedIn as Extract<SessionState, { status: 'signed-in' }>).user;
  const next = sessionReducer(signedIn, { type: 'reissued', server, owner, tokens: tokens(5) });
  assert.deepEqual(next, { ...signedIn, tokens: tokens(5) });
  assert.equal(identityOf(next), identityOf(signedIn), 'same identity: the cache stays');
  assert.equal(sessionReducer(signedIn, { type: 'reissued', server: { ...server }, owner, tokens: tokens(5) }), signedIn);
  // an earlier sign-in of the same account (another user object) is not this one
  assert.equal(sessionReducer(signedIn, { type: 'reissued', server, owner: { ...user }, tokens: tokens(5) }), signedIn);
  const out = sessionReducer(signedIn, { type: 'signed-out', reason: 'user' });
  assert.equal(sessionReducer(out, { type: 'reissued', server, owner, tokens: tokens(5) }), out);
});

test('a sign-in sent over an earlier connection does not apply', () => {
  const other = { ...server, serverId: 'srv-2', apiBaseUrl: 'https://other.example' };
  const switched: SessionState = { status: 'signed-out', server: other, reason: null };
  assert.equal(sessionReducer(switched, { type: 'signed-in', server, user, tokens: tokens(1) }), switched);
  // forgot the server and connected to the same one again while the old sign-in was in flight
  const reconnected: SessionState = { status: 'signed-out', server: { ...server }, reason: null };
  assert.equal(sessionReducer(reconnected, { type: 'signed-in', server, user, tokens: tokens(1) }), reconnected);
});

test('a rejected server token goes back to the connect page with the address kept', () => {
  assert.deepEqual(sessionReducer(signedIn, { type: 'server-token-rejected' }), {
    status: 'no-server',
    lastBase: server.base,
    tokenRejected: true,
  });
});

test('signing in twice or connecting while signed in is ignored', () => {
  assert.equal(sessionReducer(signedIn, { type: 'signed-in', server, user, tokens: tokens(9) }), signedIn);
  assert.equal(sessionReducer(signedIn, { type: 'connected', server }), signedIn);
});

test('persistence and identity follow the state', () => {
  assert.equal(persistedOf(initialSession), null);
  assert.deepEqual(persistedOf({ status: 'no-server' }), { server: null, account: null });
  assert.deepEqual(persistedOf(signedIn), { server, account: { user, tokens: tokens(1) } });
  assert.equal(identityOf(signedIn), 'srv-1/u1');
  assert.equal(identityOf({ status: 'signed-out', server, reason: null }), null);
});
