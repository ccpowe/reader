import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import Module, { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('..', import.meta.url));
const out = mkdtempSync(join(tmpdir(), 'reader-api-body-'));
const require = createRequire(import.meta.url);
process.env.NODE_PATH = join(root, 'node_modules');
Module._initPaths();
const originalFetch = globalThis.fetch;
const originalTimeout = globalThis.setTimeout;
const originalClearTimeout = globalThis.clearTimeout;
const flush = async () => { for (let i = 0; i < 16; i += 1) await Promise.resolve(); };

try {
  execFileSync(process.execPath, [join(root, 'node_modules/typescript/bin/tsc'), '--ignoreConfig',
    'lib/api.ts', '--target', 'es2022', '--module', 'commonjs', '--outDir', out,
    '--lib', 'dom,es2022', '--types', 'node', '--skipLibCheck'], { cwd: root, stdio: 'inherit' });
  const api = require(join(out, 'lib/api.js'));
  const snapshot = require(join(out, 'lib/connection/snapshot.js'));
  const { readJsonResponse, HttpResponseError } = require(join(out, 'lib/http.js'));
  const session = { access_token: 'test-token' };
  let generation = 1;
  let runtime = { generation, api_base_url: 'https://reader.test', serverToken: 'api-entry-token', authClient: { auth: { getAccessToken: async () => 'live-access-token', stopAutoRefresh: () => {}  } } };
  const resetRuntime = () => {
    runtime = { ...runtime, generation: ++generation };
    snapshot.commitRuntimeSnapshot(runtime, generation);
  };
  const timers = new Map();
  let timerId = 0;
  globalThis.setTimeout = (fn, delay) => { const id = ++timerId; timers.set(id, { fn, delay }); return id; };
  globalThis.clearTimeout = id => timers.delete(id);
  const endpoints = [
    ['checkBackend', () => api.checkBackend(session), { id: 'user', email: null }],
    ['getProfile', () => api.getProfile(session), { id: 'user' }],
    ['updateProfile', () => api.updateProfile(session, { displayName: 'New name' }), { id: 'user' }],
    ['uploadAvatar', () => api.uploadAvatar(session, { contentType: 'image/png', body: new ArrayBuffer(20) }), { id: 'user', avatar_url: 'https://reader.test/v1/profiles/user/avatar' }],
    ['getTranslationPreference', () => api.getTranslationPreference(session), { target_locale: 'zh-CN' }],
    ['updateTranslationPreference', () => api.updateTranslationPreference(session, { isEnabled: true }), { target_locale: 'zh-CN' }],
    ['getFeedPage', () => api.getFeedPage(session), { items: [], next_cursor: null }],
    ['getArticle', () => api.getArticle(session, 'article'), { id: 'article' }],
    ['getSavedContentPage', () => api.getSavedContentPage(session), { items: [], next_cursor: null }],
    ['resolveTitleTranslations', () => api.resolveTitleTranslations(session, ['article']), []],
    ['getSources', () => api.getSources(session), []],
    ['getRanking', () => api.getRanking(session, 'hackernews'), { items: [] }],
    ['removeSourceSubscription', () => api.removeSourceSubscription(session, 'source'), null],
    ['updateSourceSubscriptionFolder', () => api.updateSourceSubscriptionFolder(session, 'source', 'folder'), { source_id: 'source' }],
    ['setSavedContent', () => api.setSavedContent(session, 'article', false), null],
    ['addRssSource', () => api.addRssSource(session, 'https://rss.test'), {}],
    ['addWebSource', () => api.addWebSource(session, 'https://web.test'), {}],
    ['addRedditSource', () => api.addRedditSource(session, 'news'), {}],
    ['addYouTubeSource', () => api.addYouTubeSource(session, 'channel'), {}],
    ['addXSource', () => api.addXSource(session, 'handle'), {}],
    ['getRankingSavedState', () => api.getRankingSavedState(session, 'https://article.test'), {}],
    ['saveRankingContent', () => api.saveRankingContent(session, { url: 'https://article.test' }), {}],
  ];
  for (const [name, invoke, payload] of endpoints) {
    resetRuntime();
    let completeBody;
    let requestSignal;
    let bodyStarted = false;
    globalThis.fetch = async (_url, options) => {
      requestSignal = options.signal;
      assert.equal(new Headers(options.headers).get('X-Reader-Server-Token'), 'api-entry-token', name);
      assert.equal(new Headers(options.headers).get('Authorization'), 'Bearer live-access-token', 'API requests use live tokens after renewal');
      assert.equal(options.redirect, 'error', name);
      return { ok: true, status: 200, text: () => {
        bodyStarted = true;
        return new Promise(resolve => { completeBody = resolve; });
      } };
    };
    const timedOut = assert.rejects(invoke(), error => error.kind === 'timeout', name);
    await flush();
    assert.equal(bodyStarted, true, name);
    assert.equal(timers.size, 1, `${name}: timer covers body`);
    const timer = [...timers.values()][0];
    assert.equal(timer.delay, 15_000);
    timer.fn();
    await timedOut;
    assert.equal(requestSignal.aborted, true);
    assert.equal(timers.size, 0);
    // The body deliberately ignores abort. Late completion cannot revive the request.
    completeBody(JSON.stringify(payload));
    await flush();

    const stale = assert.rejects(invoke(), error => error.code === 'stale_runtime', name);
    await flush();
    resetRuntime();
    completeBody(JSON.stringify(payload));
    await stale;
    assert.equal(timers.size, 0);

    globalThis.fetch = async () => payload === null
      ? new Response(null, { status: 204 })
      : new Response(JSON.stringify(payload));
    await invoke();
    assert.equal(timers.size, 0, `${name}: successful request cleanup`);
  }
  globalThis.fetch = async () => new Response('Bad gateway', { status: 502 });
  await assert.rejects(api.getProfile(session), error => error instanceof HttpResponseError && error.status === 502);
  globalThis.fetch = async () => new Response('{"detail":"Cannot delete"}', { status: 403 });
  await assert.rejects(api.removeSourceSubscription(session, 'source'), /Cannot delete/);
  globalThis.fetch = async () => new Response('not json');
  await assert.rejects(api.getProfile(session), error => error instanceof HttpResponseError && error.status === 200);

  const { subscribeServerTokenInvalid } = require(join(out, 'lib/connection/tokenEvents.js'));
  const invalidTokenEvents = [];
  const unsubscribeInvalidToken = subscribeServerTokenInvalid(value => invalidTokenEvents.push(value));
  let apiRefreshes = 0;
  runtime.authClient.auth.refreshSession = async () => { apiRefreshes += 1; return { access_token: 'renewed-api-token' }; };
  let invalidTokenFetches = 0;
  globalThis.fetch = async () => { invalidTokenFetches += 1; return new Response(JSON.stringify({ detail: 'invalid_server_token' }), { status: 401 }); };
  await assert.rejects(api.getProfile(session), /invalid_server_token/);
  assert.deepEqual(invalidTokenEvents, [runtime], 'entry-token rejection is routed to the connection form boundary');
  assert.equal(apiRefreshes, 0, 'entry-token errors must not trigger user refresh or retry');
  await assert.rejects(api.getProfile(session), error => error.code === 'server_token_invalid');
  assert.equal(invalidTokenFetches, 1, 'a rejected entry token is never automatically reused');
  assert.equal(invalidTokenEvents.length, 1, 'entry-token failure notifies the UI once');
  unsubscribeInvalidToken();
  resetRuntime();
  let apiAttempts = 0;
  globalThis.fetch = async (_url, options) => {
    apiAttempts += 1;
    assert.equal(new Headers(options.headers).get('X-Reader-Server-Token'), 'api-entry-token');
    assert.equal(options.redirect, 'error');
    if (apiAttempts === 1) return new Response(JSON.stringify({ detail: 'Session expired' }), { status: 401 });
    assert.equal(new Headers(options.headers).get('Authorization'), 'Bearer renewed-api-token');
    return new Response(JSON.stringify({ id: 'user' }));
  };
  assert.equal((await api.getProfile(session)).id, 'user');
  assert.equal(apiRefreshes, 1, 'user JWT rejection renews once');
  assert.equal(apiAttempts, 2, 'authenticated retry uses the newly issued access token');
  assert.equal(timers.size, 0);

  const upstream = new AbortController();
  let requestSignal;
  globalThis.fetch = async (_url, options) => {
    requestSignal = options.signal;
    return { ok: true, status: 200, text: () => new Promise(() => {}) };
  };
  const cancelled = assert.rejects(api.fetchWithTimeout('https://reader.test', { signal: upstream.signal }, undefined,
    async response => { await readJsonResponse(response); }), error => error.code === 'discovery_cancelled');
  await flush();
  upstream.abort();
  await cancelled;
  assert.equal(requestSignal.aborted, true);
  assert.equal(timers.size, 0);
  let fetches = 0;
  globalThis.fetch = async () => { fetches += 1; return new Response('{}'); };
  await assert.rejects(api.fetchWithTimeout('https://reader.test', { signal: upstream.signal }), error => error.code === 'discovery_cancelled');
  assert.equal(fetches, 0);
  assert.equal(timers.size, 0);
  for (const url of ['https://external.test/v1/me', 'https://reader.test.evil.test/v1/me', 'https://reader.test/public/image', 'https://reader.test/v1/../outside']) {
    await assert.rejects(api.fetchWithTimeout(url, { headers: { Authorization: 'Bearer private' } }), error => error.code === 'stale_runtime');
    await assert.rejects(api.fetchWithTimeout(url, { headers: { 'X-Reader-Server-Token': 'private' } }), error => error.code === 'stale_runtime');
  }
  assert.equal(fetches, 0, 'out-of-origin and out-of-API-path credentials are rejected before transport');
  await api.fetchWithTimeout('https://external.test/public/image', {});
  assert.equal(fetches, 1, 'public external requests remain available without credentials');
  snapshot.updateReaderSessionIdentity(runtime, 'account-a');
  globalThis.fetch = async (_url, options) => {
    requestSignal = options.signal;
    return { ok: true, status: 200, text: () => new Promise(() => {}) };
  };
  const changedAccount = assert.rejects(api.getProfile(session), error => error.code === 'stale_runtime');
  await flush();
  const accountEpoch = snapshot.getReaderSessionEpochSnapshot();
  assert.equal(snapshot.updateReaderSessionIdentity(runtime, 'account-a'), false);
  assert.equal(snapshot.getReaderSessionEpochSnapshot(), accountEpoch);
  assert.equal(requestSignal.aborted, false, 'same user token refresh preserves work');
  snapshot.updateReaderSessionIdentity(runtime, 'account-b');
  await changedAccount;
  assert.equal(requestSignal.aborted, true, 'account changes cancel even a hung body');
  assert.equal(timers.size, 0);
  console.log(`PASS ${endpoints.length} ordinary API body timeouts, stale completion guards, success/204 cleanup, JSON error classification and cancellation`);
} finally {
  globalThis.fetch = originalFetch;
  globalThis.setTimeout = originalTimeout;
  globalThis.clearTimeout = originalClearTimeout;
  rmSync(out, { recursive: true, force: true });
}
