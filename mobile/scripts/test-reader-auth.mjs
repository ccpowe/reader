import assert from 'node:assert/strict';
import { buildSync } from 'esbuild';
import { readFileSync, existsSync } from 'node:fs';
import Module, { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const root = fileURLToPath(new URL('..', import.meta.url));
const require = createRequire(import.meta.url);
function compile(file, mocks = {}) {
  const filename = join(root, file);
  const mod = new Module(filename);
  mod.filename = filename;
  mod.paths = Module._nodeModulePaths(root);
  mod.require = name => Object.hasOwn(mocks, name) ? mocks[name] : require(name);
  mod._compile(buildSync({ entryPoints: [filename], bundle: true, packages: 'external', platform: 'node', format: 'cjs', write: false }).outputFiles[0].text, filename);
  return mod.exports;
}
const { createReaderAuthClient } = compile('lib/readerAuth.ts');
const storageModule = compile('lib/connection/storage.ts');
const memory = () => {
  const values = new Map();
  return { values, getItem: key => values.get(key) ?? null, setItem: (key, value) => { values.set(key, value); }, removeItem: key => { values.delete(key); } };
};
const session = (id, expiresIn = 3600) => ({ access_token: `access-${id}`, refresh_token: `refresh-${id}`, token_type: 'bearer', expires_in: expiresIn, expires_at: Math.floor(Date.now() / 1000) + expiresIn, user: { id: 'user-a', email: 'reader@example.com' } });
const ok = value => new Response(JSON.stringify(value));
const requests = [];
let respond = async () => ok(session('initial'));
const originalFetch = globalThis.fetch;
globalThis.fetch = async (url, options) => {
  const request = { url, options, body: JSON.parse(options.body) };
  requests.push(request);
  assert.equal(new URL(url).origin, 'https://reader.test');
  assert.equal(new Headers(options.headers).get('X-Reader-Server-Token'), 'entry-token');
  assert.equal(options.redirect, 'error', 'all auth credentials reject redirects');
  assert.equal(url.includes('entry-token'), false);
  return respond(request);
};
const clients = [];
function client(storage = memory(), base = 'https://reader.test/prefix') {
  const value = createReaderAuthClient(base, 'entry-token', storage);
  clients.push(value);
  return value;
}
try {
  const stored = memory();
  const auth = client(stored).auth;
  const events = [];
  auth.onAuthStateChange((event, value) => events.push([event, value]));
  const credentials = { email: 'reader@example.com', password: 'a-good-password' };
  const signedUp = await auth.signUp(credentials);
  assert.equal(signedUp.error, null);
  assert.equal(signedUp.data.session.access_token, 'access-initial', 'register immediately returns a usable session');
  assert.equal(requests.at(-1).url, 'https://reader.test/prefix/v1/auth/register');
  assert.deepEqual(requests.at(-1).body, credentials, 'registration has no email callback options');
  assert.equal(events.at(-1)[0], 'SIGNED_IN');
  assert.equal(stored.getItem('refresh'), 'refresh-initial');
  assert.equal(JSON.parse(stored.getItem('session')).refresh_token, undefined, 'refresh credentials have a separate private entry');

  let finishRefresh;
  let refreshCount = 0;
  respond = request => {
    assert.ok(request.url.endsWith('/refresh'));
    refreshCount += 1;
    return new Promise(resolve => { finishRefresh = resolve; });
  };
  const concurrent = Array.from({ length: 8 }, () => auth.refreshSession());
  assert.equal(refreshCount, 1, 'concurrent refresh rotates the stored token once');
  assert.deepEqual(requests.at(-1).body, { refresh_token: 'refresh-initial' });
  finishRefresh(ok(session('rotated')));
  assert.ok((await Promise.all(concurrent)).every(value => value.refresh_token === 'refresh-rotated'));
  assert.equal(stored.getItem('refresh'), 'refresh-rotated');
  assert.equal(events.filter(([event]) => event === 'TOKEN_REFRESHED').length, 1);

  respond = async () => ok(session('login'));
  await auth.signInWithPassword(credentials);
  assert.ok(requests.at(-1).url.endsWith('/login'));
  const password = { current_password: 'a-good-password', password: 'new-good-password' };
  respond = async () => ok(session('password'));
  await auth.updatePassword(password);
  assert.deepEqual(requests.at(-1).body, password);
  assert.equal(requests.at(-1).options.method, 'PATCH');
  assert.equal(new Headers(requests.at(-1).options.headers).get('Authorization'), 'Bearer access-login');
  assert.equal(stored.getItem('refresh'), 'refresh-password');
  respond = async () => ok({ ...session('email'), user: { id: 'user-a', email: 'new@example.com' } });
  await auth.updateEmail({ current_password: 'new-good-password', email: 'new@example.com' });
  assert.ok(requests.at(-1).url.endsWith('/email'));
  assert.equal((await auth.getSession()).data.session.user.email, 'new@example.com', 'email changes immediately without verification state');

  // An account PATCH must await an already-running rotation even with a fresh
  // access JWT, otherwise a failed PATCH can strand the only valid refresh token.
  let finishBackgroundRefresh;
  let patchRequests = 0;
  respond = request => {
    if (request.url.endsWith('/refresh')) return new Promise(resolve => { finishBackgroundRefresh = resolve; });
    patchRequests += 1;
    assert.equal(new Headers(request.options.headers).get('Authorization'), 'Bearer access-before-failed-patch');
    return Promise.resolve(new Response(JSON.stringify({ detail: 'Current password is incorrect.' }), { status: 400 }));
  };
  const backgroundRefresh = auth.refreshSession();
  const failedPatch = auth.updatePassword({ current_password: 'wrong-password', password: 'new-password' });
  await Promise.resolve();
  assert.equal(patchRequests, 0, 'PATCH waits for pending background renewal');
  finishBackgroundRefresh(ok(session('before-failed-patch')));
  await backgroundRefresh;
  assert.ok((await failedPatch).error);
  assert.equal(patchRequests, 1);
  assert.equal(stored.getItem('refresh'), 'refresh-before-failed-patch', 'failed PATCH retains the completed refresh rotation');

  // The reverse ordering is also serialized: refresh waits for an account
  // mutation and uses the replacement family issued by that mutation.
  let finishAccountPatch;
  let patchStarted;
  let refreshStarted;
  const patchReady = new Promise(resolve => { patchStarted = resolve; });
  const refreshReady = new Promise(resolve => { refreshStarted = resolve; });
  let reverseRefreshes = 0;
  respond = request => {
    if (request.url.endsWith('/password')) {
      patchStarted();
      return new Promise(resolve => { finishAccountPatch = resolve; });
    }
    reverseRefreshes += 1;
    assert.deepEqual(request.body, { refresh_token: 'refresh-patch-family' });
    refreshStarted();
    return new Promise(resolve => { finishRefresh = resolve; });
  };
  const activePatch = auth.updatePassword({ current_password: 'current-password', password: 'new-password' });
  await patchReady;
  const queuedRefresh = auth.refreshSession();
  await Promise.resolve();
  assert.equal(reverseRefreshes, 0, 'background refresh cannot use an about-to-be-revoked family during PATCH');
  finishAccountPatch(ok(session('patch-family')));
  assert.equal((await activePatch).error, null);
  await refreshReady;
  finishRefresh(ok(session('patch-family-renewed')));
  await queuedRefresh;
  assert.equal(stored.getItem('refresh'), 'refresh-patch-family-renewed');

  let stopPatchStarted;
  const stopPatchReady = new Promise(resolve => { stopPatchStarted = resolve; });
  let refreshAfterStop = 0;
  respond = request => {
    if (request.url.endsWith('/password')) {
      stopPatchStarted();
      return new Promise(resolve => { finishAccountPatch = resolve; });
    }
    refreshAfterStop += 1;
    return Promise.resolve(ok(session('wrong-runtime')));
  };
  const stoppedPatch = auth.updatePassword({ current_password: 'current-password', password: 'new-password' });
  await stopPatchReady;
  const stoppedQueuedRefresh = auth.refreshSession();
  auth.stopAutoRefresh();
  finishAccountPatch(ok(session('stale-patch-family')));
  await Promise.all([stoppedPatch, stoppedQueuedRefresh]);
  assert.equal(refreshAfterStop, 0, 'stopping a runtime prevents a waiting refresh from restarting network work');
  assert.equal(stored.getItem('refresh'), 'refresh-patch-family-renewed');

  // A refresh completing after logout cannot recreate the account or private storage.
  respond = request => request.url.endsWith('/logout')
    ? Promise.resolve(new Response(null, { status: 204 }))
    : new Promise(resolve => { finishRefresh = resolve; });
  const lateRefresh = auth.refreshSession();
  await auth.signOut();
  assert.equal(stored.getItem('session'), null);
  assert.equal(stored.getItem('refresh'), null);
  finishRefresh(ok(session('too-late')));
  assert.equal(await lateRefresh, null);
  assert.equal((await auth.getSession()).data.session, null);
  assert.equal(events.at(-1)[0], 'SIGNED_OUT');

  // Logout remains local even when the remote revocation fails.
  respond = async () => ok(session('logout-offline'));
  await auth.signInWithPassword(credentials);
  respond = async () => { throw new TypeError('offline'); };
  await assert.rejects(auth.signOut(), /offline/);
  assert.equal((await auth.getSession()).data.session, null);
  assert.equal(stored.values.size, 0);

  // An older login completion and a stopped runtime cannot replace a newer account.
  let finishOldLogin;
  respond = () => new Promise(resolve => { finishOldLogin = resolve; });
  const oldLogin = auth.signInWithPassword(credentials);
  auth.stopAutoRefresh(); // Old runtime is invalidated before the replacement login.
  respond = async () => ok(session('new-login'));
  await auth.signInWithPassword(credentials);
  finishOldLogin(ok(session('old-login')));
  assert.ok((await oldLogin).error);
  assert.equal((await auth.getSession()).data.session.access_token, 'access-new-login');
  respond = () => new Promise(resolve => { finishRefresh = resolve; });
  const switchedRefresh = auth.refreshSession();
  auth.stopAutoRefresh();
  finishRefresh(ok(session('stopped-runtime')));
  await switchedRefresh;
  assert.equal(stored.getItem('refresh'), 'refresh-new-login', 'stopped runtime ignores an old rotation result');

  // Expired sessions share renewal; failed auth clears credentials, while offline
  // cached UI may restore but must never send an expired access token to an API.
  const expiredStore = memory();
  const expired = session('expired', -10);
  expiredStore.setItem('session', JSON.stringify(expired));
  expiredStore.setItem('refresh', expired.refresh_token);
  const expiring = client(expiredStore).auth;
  respond = () => new Promise(resolve => { finishRefresh = resolve; });
  const beforeExpiryRefresh = requests.length;
  const accessResults = [expiring.getAccessToken(), expiring.getAccessToken(), expiring.getSession()];
  assert.equal(requests.length, beforeExpiryRefresh + 1);
  finishRefresh(ok(session('renewed')));
  const [firstToken, secondToken, restored] = await Promise.all(accessResults);
  assert.equal(firstToken, 'access-renewed');
  assert.equal(secondToken, firstToken);
  assert.equal(restored.data.session.access_token, 'access-expired', 'candidate session restoration is read-only and never competes for rotation');
  assert.equal((await expiring.getSession()).data.session.access_token, firstToken);
  respond = async () => new Response(JSON.stringify({ detail: 'Invalid refresh token' }), { status: 401 });
  await assert.rejects(expiring.refreshSession(), error => error.status === 401);
  assert.equal((await expiring.getSession()).data.session, null);
  assert.equal(expiredStore.values.size, 0);

  const offlineStore = memory();
  offlineStore.setItem('session', JSON.stringify(expired));
  offlineStore.setItem('refresh', expired.refresh_token);
  const offline = client(offlineStore).auth;
  respond = async () => { throw new TypeError('offline'); };
  assert.equal((await offline.getSession()).data.session.user.id, expired.user.id);
  await assert.rejects(offline.getAccessToken(), /offline/, 'an expired access token cannot escape during an outage');

  const stoppedExpiredStore = memory();
  stoppedExpiredStore.setItem('session', JSON.stringify(expired));
  stoppedExpiredStore.setItem('refresh', expired.refresh_token);
  const stoppedExpired = client(stoppedExpiredStore).auth;
  let expiredRequests = 0;
  let finishExpiredRefresh;
  respond = () => {
    expiredRequests += 1;
    if (expiredRequests === 1) return new Promise(resolve => { finishExpiredRefresh = resolve; });
    return Promise.resolve(ok(session('must-not-restart')));
  };
  const oldExpiryRefresh = stoppedExpired.refreshSession();
  const waitingExpiryPatch = stoppedExpired.updatePassword({ current_password: 'current-password', password: 'new-password' });
  stoppedExpired.stopAutoRefresh();
  finishExpiredRefresh(ok(session('old-expiry-refresh')));
  await oldExpiryRefresh;
  assert.ok((await waitingExpiryPatch).error);
  assert.equal(expiredRequests, 1, 'a stopped PATCH waiter checks its epoch before expired-token retrieval can start another refresh');
  assert.equal(stoppedExpiredStore.getItem('refresh'), 'refresh-expired');

  // Exact server ID + API path namespacing protects same-host installations.
  const shared = memory();
  const a = storageModule.createNamespacedStorage({ server_id: 'shared', api_base_url: 'https://reader.test/one' }, shared);
  const b = storageModule.createNamespacedStorage({ server_id: 'shared', api_base_url: 'https://reader.test/two' }, shared);
  const serverA = client(a, 'https://reader.test/one').auth;
  const serverB = client(b, 'https://reader.test/two').auth;
  respond = async request => ok(session(request.url.includes('/one/') ? 'one' : 'two'));
  await serverA.signInWithPassword(credentials);
  await serverB.signInWithPassword(credentials);
  assert.equal(a.getItem('refresh'), 'refresh-one');
  assert.equal(b.getItem('refresh'), 'refresh-two');
  respond = async () => new Response(null, { status: 204 });
  await serverA.signOut();
  assert.equal(b.getItem('refresh'), 'refresh-two', 'logout only clears its server namespace');

  // Interrupted private writes never restore mixed session/refresh pairs.
  const interruptedStore = memory();
  let interruptWrites = false;
  const interrupted = client({ ...interruptedStore, setItem: (key, value) => {
    if (interruptWrites && key === 'session') throw new Error('private storage full');
    interruptedStore.setItem(key, value);
  } }).auth;
  respond = async () => ok(session('before-interruption'));
  await interrupted.signInWithPassword(credentials);
  interruptWrites = true;
  respond = async () => ok(session('after-interruption'));
  assert.ok((await interrupted.signInWithPassword(credentials)).error);
  assert.equal((await client(interruptedStore).auth.getSession()).data.session, null, 'restart rejects mismatched private records after partial persistence');

  const failingDeleteStore = memory();
  let failDelete = false;
  const failingDelete = client({ ...failingDeleteStore, removeItem: key => {
    if (failDelete) throw new Error('private storage unavailable');
    failingDeleteStore.removeItem(key);
  } }).auth;
  respond = async () => ok(session('delete-failure'));
  await failingDelete.signInWithPassword(credentials);
  failDelete = true;
  let remoteRevocations = 0;
  respond = async request => { assert.ok(request.url.endsWith('/logout')); remoteRevocations += 1; return new Response(null, { status: 204 }); };
  await assert.rejects(failingDelete.signOut(), /private storage unavailable/);
  assert.equal(remoteRevocations, 1, 'private-storage failure still attempts server-side revocation');
  assert.equal((await failingDelete.getSession()).data.session, null, 'private-storage failure cannot retain the active account');

  assert.equal(existsSync(join(root, 'lib/authCallback.ts')), false);
  assert.equal(existsSync(join(root, 'screens/ResetPasswordScreen.tsx')), false);
  assert.doesNotMatch(readFileSync(join(root, 'App.tsx'), 'utf8'), /consumeAuthCallback|authRedirectUrl|ResetPasswordScreen|Linking/);
} finally {
  clients.forEach(value => value.auth.stopAutoRefresh());
  globalThis.fetch = originalFetch;
}
console.log('Reader direct auth, single-flight renewal, credential isolation and stale account guards passed.');
