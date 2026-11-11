import assert from 'node:assert/strict';
import { test } from 'node:test';

import type { ApiError } from '../api/errors.ts';
import type { Persisted, Server } from './session.ts';
import { createSessionStore, type SessionStorage } from './session-store.ts';

const server: Server = { base: 'https://r.example', apiBaseUrl: 'https://r.example', serverId: 'srv', serverToken: 'st' };
const json = (status: number, body: unknown) => new Response(JSON.stringify(body), { status });
const tokens = (n: number) => ({ access_token: `a${n}`, refresh_token: `r${n}`, expires_at: n, user: { id: 'u1', email: 'me@x' } });

function memoryStorage(initial: Persisted | null = null) {
  const saved: Persisted[] = [];
  const storage: SessionStorage = {
    load: async () => initial,
    save: async (p) => {
      saved.push(p);
    },
  };
  return { storage, saved };
}

type Route = (body: unknown, headers: Record<string, string>) => Response | Promise<Response>;
function server_(routes: Record<string, Route | Route[]>) {
  const calls: string[] = [];
  const impl = (async (url: string, init: RequestInit) => {
    const path = new URL(url).pathname;
    calls.push(path);
    const route = routes[path];
    const handler = Array.isArray(route) ? route.shift() : route;
    if (!handler) throw new TypeError(`no route ${path}`);
    return handler(init.body ? JSON.parse(String(init.body)) : undefined, init.headers as Record<string, string>);
  }) as unknown as typeof fetch;
  return { impl, calls };
}

test('restore, sign in, sign out: state, storage and cache identity', async () => {
  const { storage, saved } = memoryStorage({ server, account: null });
  const ended: string[] = [];
  const { impl, calls } = server_({ '/auth/login': () => json(200, tokens(1)), '/auth/logout': () => new Response(null, { status: 204 }) });
  const store = createSessionStore({ storage, fetch: impl, onIdentityEnded: (id) => ended.push(id) });

  await store.restore();
  assert.equal(store.getSnapshot().status, 'signed-out');
  await store.signIn('me@x', 'pw');
  const signedIn = store.getSnapshot();
  assert.equal(signedIn.status, 'signed-in');
  store.signOut();
  assert.equal(store.getSnapshot().status, 'signed-out');
  await store.flush();
  assert.deepEqual(ended, ['srv/u1']);
  assert.deepEqual(saved.map((p) => p.account?.tokens.access ?? null), ['a1', null]);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(calls, ['/auth/login', '/auth/logout']);
});

test('one refresh for many requests that hit an expired token', async () => {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => (release = resolve));
  const { impl, calls } = server_({
    '/feed': (_, headers) =>
      headers.Authorization === 'Bearer a1' ? json(401, { error: { code: 'not_authenticated', message: '' } }) : json(200, { ok: headers.Authorization }),
    '/auth/refresh': async (body) => {
      assert.deepEqual(body, { refresh_token: 'r1' });
      await gate;
      return json(200, tokens(2));
    },
  });
  const { storage } = memoryStorage({ server, account: { user: { id: 'u1', email: 'me@x' }, tokens: { access: 'a1', refresh: 'r1', expiresAt: 1 } } });
  const signed = createSessionStore({ storage, fetch: impl });
  await signed.restore();
  const both = Promise.all([signed.api().request('/feed'), signed.api().request('/feed')]);
  await new Promise((resolve) => setTimeout(resolve, 0));
  release();
  assert.deepEqual(await both, [{ ok: 'Bearer a2' }, { ok: 'Bearer a2' }]);
  assert.equal(calls.filter((c) => c === '/auth/refresh').length, 1);
});

test('a refresh the server refuses signs out with reason "expired"', async () => {
  const { impl } = server_({
    '/feed': () => json(401, { error: { code: 'not_authenticated', message: '' } }),
    '/auth/refresh': () => json(401, { error: { code: 'not_authenticated', message: '' } }),
  });
  const { storage } = memoryStorage({ server, account: { user: { id: 'u1', email: 'me@x' }, tokens: { access: 'a1', refresh: 'r1', expiresAt: 1 } } });
  const store = createSessionStore({ storage, fetch: impl });
  await store.restore();
  await assert.rejects(store.api().request('/feed'));
  assert.deepEqual(store.getSnapshot(), { status: 'signed-out', server, reason: 'expired' });
});

test('an offline refresh keeps the session', async () => {
  const { impl } = server_({ '/feed': () => json(401, { error: { code: 'not_authenticated', message: '' } }), '/auth/refresh': [] });
  const { storage } = memoryStorage({ server, account: { user: { id: 'u1', email: 'me@x' }, tokens: { access: 'a1', refresh: 'r1', expiresAt: 1 } } });
  const store = createSessionStore({ storage, fetch: impl });
  await store.restore();
  await assert.rejects(store.api().request('/feed'), (error: ApiError) => error.problem.kind === 'network');
  assert.equal(store.getSnapshot().status, 'signed-in');
});

test('a rejected server token sends the user back to connect', async () => {
  const { impl } = server_({ '/feed': () => json(401, { error: { code: 'invalid_server_token', message: '' } }) });
  const { storage } = memoryStorage({ server, account: { user: { id: 'u1', email: 'me@x' }, tokens: { access: 'a1', refresh: 'r1', expiresAt: 1 } } });
  const ended: string[] = [];
  const store = createSessionStore({ storage, fetch: impl, onIdentityEnded: (id) => ended.push(id) });
  await store.restore();
  await assert.rejects(store.api().request('/feed'));
  assert.deepEqual(store.getSnapshot(), { status: 'no-server', lastBase: server.base, tokenRejected: true });
  assert.deepEqual(ended, ['srv/u1']);
});

test('unreadable storage starts over instead of crashing', async () => {
  const store = createSessionStore({
    storage: { load: async () => { throw new Error('corrupt'); }, save: async () => undefined },
  });
  await store.restore();
  assert.deepEqual(store.getSnapshot(), { status: 'no-server' });
});

test("a late 401 from the previous server never receives the new server's token", async () => {
  let answerOld!: (r: Response) => void;
  const oldAnswer = new Promise<Response>((resolve) => (answerOld = resolve));
  const seen: string[] = [];
  const fetchImpl = (async (url: string, init: RequestInit) => {
    const { host, pathname } = new URL(url);
    seen.push(`${host}${pathname} ${(init.headers as Record<string, string>).Authorization ?? '-'}`);
    if (host === 'a.example' && pathname === '/feed') return oldAnswer;
    if (pathname === '/auth/logout') return new Response(null, { status: 204 });
    if (host === 'b.example' && pathname === '/auth/login') return json(200, { ...tokens(9), access_token: 'access-b' });
    throw new TypeError(`unexpected ${url}`);
  }) as unknown as typeof fetch;
  const a: Server = { base: 'https://a.example', apiBaseUrl: 'https://a.example', serverId: 'a', serverToken: 'ta' };
  const b: Server = { base: 'https://b.example', apiBaseUrl: 'https://b.example', serverId: 'b', serverToken: 'tb' };
  const { storage } = memoryStorage({ server: a, account: { user: { id: 'u1', email: 'me@x' }, tokens: { access: 'access-a', refresh: 'r-a', expiresAt: 1 } } });
  const store = createSessionStore({ storage, fetch: fetchImpl });
  await store.restore();
  const pending = store.api().request('/feed');
  store.forgetServer();
  store.connect(b);
  await store.signIn('me@x', 'pw');
  answerOld(json(401, { error: { code: 'not_authenticated', message: '' } }));
  await assert.rejects(pending);
  assert.ok(!seen.some((line) => line.startsWith('a.example') && line.includes('access-b')), seen.join('\n'));
});

test('a refresh that finishes after the user signed in again does not hand out the new token', async () => {
  let finishRefresh!: () => void;
  const gate = new Promise<void>((resolve) => (finishRefresh = resolve));
  const seen: string[] = [];
  const fetchImpl = (async (url: string, init: RequestInit) => {
    const { pathname } = new URL(url);
    const auth = (init.headers as Record<string, string>).Authorization ?? '-';
    seen.push(`${pathname} ${auth}`);
    if (pathname === '/feed') return json(401, { error: { code: 'not_authenticated', message: '' } });
    if (pathname === '/auth/refresh') {
      await gate;
      return json(200, tokens(2));
    }
    if (pathname === '/auth/logout') return new Response(null, { status: 204 });
    if (pathname === '/auth/login') return json(200, { ...tokens(7), user: { id: 'u2', email: 'other@x' } });
    throw new TypeError(pathname);
  }) as unknown as typeof fetch;
  const { storage } = memoryStorage({ server, account: { user: { id: 'u1', email: 'me@x' }, tokens: { access: 'a1', refresh: 'r1', expiresAt: 1 } } });
  const store = createSessionStore({ storage, fetch: fetchImpl });
  await store.restore();
  const pending = store.api().request('/feed');
  await new Promise((resolve) => setTimeout(resolve, 0));
  store.signOut();
  await store.signIn('other@x', 'pw');
  finishRefresh();
  await assert.rejects(pending);
  assert.ok(!seen.includes('/feed Bearer a7'), seen.join('\n'));
});
