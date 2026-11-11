import assert from 'node:assert/strict';
import { test } from 'node:test';

import { authProblemField, authReducer, initialAuth, type AuthState } from './auth-flow.ts';
import { connectReducer, initialConnect, problemField, type ConnectState } from './connect-flow.ts';

const discovered = { base: 'https://r.example', serverId: 'home', apiBaseUrl: 'https://r.example' };

test('connect: empty fields are caught before any request', () => {
  let state = initialConnect();
  state = connectReducer(state, { type: 'submit', op: '1' });
  assert.equal(state.status, 'editing');
  assert.equal(problemField((state as Extract<ConnectState, { status: 'editing' }>).problem), 'address');
  state = connectReducer(state, { type: 'edit', field: 'address', value: 'r.example' });
  state = connectReducer(state, { type: 'submit', op: '2' });
  assert.equal(problemField((state as Extract<ConnectState, { status: 'editing' }>).problem), 'token');
});

test('connect: check → confirm → back keeps the fields', () => {
  let state = initialConnect('https://r.example', true);
  assert.deepEqual(state, { status: 'editing', address: 'https://r.example', token: '', problem: null, notice: 'token-rejected' });
  state = connectReducer(state, { type: 'edit', field: 'token', value: 't' });
  state = connectReducer(state, { type: 'submit', op: 'a' });
  assert.equal(state.status, 'checking');
  // submitting again while checking does nothing
  assert.equal(connectReducer(state, { type: 'submit', op: 'b' }), state);
  state = connectReducer(state, { type: 'checked', op: 'a', result: { ok: true, server: discovered } });
  assert.deepEqual(state, { status: 'confirming', address: 'https://r.example', token: 't', server: discovered });
  state = connectReducer(state, { type: 'back' });
  assert.deepEqual(state, { status: 'editing', address: 'https://r.example', token: 't', problem: null, notice: null });
});

test('connect: a late result of an older check is ignored', () => {
  let state = connectReducer({ ...initialConnect('x.example'), token: 't' }, { type: 'submit', op: 'a' });
  const stale = connectReducer(state, { type: 'checked', op: 'old', result: { ok: false, problem: { kind: 'timeout' } } });
  assert.equal(stale, state);
  state = connectReducer(state, { type: 'checked', op: 'a', result: { ok: false, problem: { kind: 'wrong-token' } } });
  assert.equal(state.status, 'editing');
  assert.equal(problemField((state as Extract<ConnectState, { status: 'editing' }>).problem), 'token');
});

test('auth: local checks, then submit; a wrong password is cleared, the email kept', () => {
  let state: AuthState = initialAuth();
  state = authReducer(state, { type: 'submit', op: '1' });
  assert.equal(state.status === 'editing' && state.problem, 'email-empty');
  state = authReducer(state, { type: 'edit', field: 'email', value: ' me@x.example ' });
  state = authReducer(state, { type: 'edit', field: 'password', value: 'pw' });
  state = authReducer(state, { type: 'submit', op: '2' });
  assert.equal(state.status, 'submitting');
  assert.equal(state.email, 'me@x.example');
  state = authReducer(state, { type: 'failed', op: '2', problem: { kind: 'http', status: 401, code: 'invalid_credentials', message: '' } });
  assert.equal(state.status, 'editing');
  assert.equal(state.password, '');
  assert.equal(state.email, 'me@x.example');
  assert.equal(state.status === 'editing' && authProblemField(state.problem), 'password');
});

test('auth: register needs a long enough password; mode switch clears the problem', () => {
  let state: AuthState = authReducer(initialAuth('me@x.example'), { type: 'switch-mode' });
  state = authReducer(state, { type: 'edit', field: 'password', value: 'short' });
  state = authReducer(state, { type: 'submit', op: '1' });
  assert.equal(state.status === 'editing' && state.problem, 'password-short');
  state = authReducer(state, { type: 'switch-mode' });
  assert.equal(state.mode, 'sign-in');
  assert.equal(state.status === 'editing' && state.problem, null);
});

test('auth: offline failure keeps the password; late failures are ignored', () => {
  let state: AuthState = { ...initialAuth('me@x.example'), password: 'secret' };
  state = authReducer(state, { type: 'submit', op: 'a' });
  assert.equal(authReducer(state, { type: 'failed', op: 'zzz', problem: { kind: 'network' } }), state);
  state = authReducer(state, { type: 'failed', op: 'a', problem: { kind: 'network' } });
  assert.equal(state.password, 'secret');
  assert.equal(state.status === 'editing' && authProblemField(state.problem), null);
});

test('auth: a register with no answer offers signing in with what was typed', () => {
  let state: AuthState = authReducer({ ...initialAuth('me@x.example'), password: 'long-enough' }, { type: 'switch-mode' });
  state = authReducer(state, { type: 'submit', op: 'r' });
  assert.equal(state.status, 'submitting');
  state = authReducer(state, { type: 'failed', op: 'r', problem: { kind: 'timeout' } });
  assert.deepEqual(state, { status: 'editing', mode: 'sign-in', email: 'me@x.example', password: 'long-enough', displayName: '', problem: 'register-unknown' });
  assert.equal(authProblemField(state.status === 'editing' ? state.problem : null), null);
});

test('auth and connect: a submit while one is in flight is not accepted', () => {
  const submitting = authReducer({ ...initialAuth('me@x.example'), password: 'pw' }, { type: 'submit', op: 'a' });
  const again = authReducer(submitting, { type: 'submit', op: 'b' });
  // the page sends a request only when the accepted op is its own
  assert.equal(again.status === 'submitting' && again.op, 'a');
  const checking = connectReducer({ ...initialConnect('r.example'), token: 't' }, { type: 'submit', op: 'a' });
  assert.equal(connectReducer(checking, { type: 'submit', op: 'b' }), checking);
});
