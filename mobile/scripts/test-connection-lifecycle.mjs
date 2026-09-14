import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import Module from 'node:module';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-connection-lifecycle-'));
const require = createRequire(import.meta.url);
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();

function discovery(serverId, baseUrl = `https://${serverId}.example.com/reader`) {
  return {
    api_base_url: baseUrl,
    base_url: baseUrl,
    discovery_url: `${baseUrl}/.well-known/reader.json`,
    protocol_version: 2,
    redirected: false,
    requires_confirmation: false,
    response_url: `${baseUrl}/.well-known/reader.json`,
    server_id: serverId,
  };
}

try {
  execFileSync(
    process.execPath,
    [
      join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
      '--ignoreConfig',
      'lib/connection/errors.ts',
      'lib/connection/types.ts',
      'lib/connection/url.ts',
      'lib/connection/storage.ts',
      'lib/connection/snapshot.ts',
      'lib/connection/guard.ts',
      'lib/connection/state.ts',
      'lib/connection/machine.ts',
      'lib/connection/discovery.ts',
      'lib/connection/runtime.ts',
      'lib/connection/session.ts',
      'lib/api.ts',
      'state/queryClient.ts',
      'state/cacheUpdates.ts',
      'state/invalidation.ts',
      '--target',
      'es2022',
      '--module',
      'commonjs',
      '--outDir',
      outputDirectory,
      '--lib',
      'dom,es2022',
      '--types',
      'node',
      '--skipLibCheck',
    ],
    { cwd: mobileRoot, stdio: 'inherit' },
  );

  const { ConnectionError, redactConnectionText } = require(join(outputDirectory, 'lib/connection/errors.js'));
  const { ConnectionMachine } = require(join(outputDirectory, 'lib/connection/machine.js'));
  const { createNamespacedStorage, readPersistedConnection } = require(join(outputDirectory, 'lib/connection/storage.js'));
  assert.equal(
    redactConnectionText('Authorization: Bearer abc.def token=secret refresh_token=refresh'),
    'Authorization: Bearer [redacted] token=[redacted] refresh_token=[redacted]',
  );
  const machine = new ConnectionMachine();
  const operation = machine.begin('discovering');
  assert.equal(machine.isCurrent(operation), true);
  machine.cancel();
  assert.equal(machine.isCurrent(operation), false, 'cancelled reducer operation must reject late results');

  const authValues = new Map();
  const authBackingStorage = {
    getItem: (key) => authValues.get(key) ?? null,
    removeItem: (key) => { authValues.delete(key); },
    setItem: (key, value) => { authValues.set(key, value); },
  };
  const authNamespaces = [
    createNamespacedStorage(
      { server_id: 'reader%prod', api_base_url: 'https://shared.reader.test' },
      authBackingStorage,
    ),
    createNamespacedStorage(
      { server_id: 'reader_25prod', api_base_url: 'https://shared.reader.test' },
      authBackingStorage,
    ),
    createNamespacedStorage(
      { server_id: 'reader.session.v2.6:reader', api_base_url: 'https://shared.reader.test' },
      authBackingStorage,
    ),
    createNamespacedStorage(
      { server_id: 'reader%prod', api_base_url: 'https://other.reader.test' },
      authBackingStorage,
    ),
  ];
  authNamespaces.forEach((storage, index) => storage.setItem('session', `session-${index}`));
  assert.equal(authValues.size, authNamespaces.length, 'every identity pair must have a unique auth namespace');
  authNamespaces.forEach((storage, index) => {
    assert.equal(storage.getItem('session'), `session-${index}`);
  });

  const {
    activateReaderServer,
    disconnectReaderServer,
    getActiveRuntime,
    getConnectionGeneration,
    restorePersistedReaderRuntime,
    subscribeConnection,
  } = require(join(outputDirectory, 'lib/connection/runtime.js'));
  const { captureRuntimeContext, isRuntimeContextCurrent } = require(join(outputDirectory, 'lib/connection/guard.js'));
  const { logoutReaderSession } = require(join(outputDirectory, 'lib/connection/session.js'));
  const { readerQueryKeys } = require(join(outputDirectory, 'state/queryClient.js'));
  const { setCachedFeedSavedState } = require(join(outputDirectory, 'state/cacheUpdates.js'));
  const { invalidateAfterSavedMutation } = require(join(outputDirectory, 'state/invalidation.js'));
  const { fetchWithTimeout } = require(join(outputDirectory, 'lib/api.js'));
  const { QueryClient } = require('@tanstack/react-query');
  const clients = [];
  const factory = (server, _identity, _storage, clientOptions) => {
    server.candidateAutoRefresh = clientOptions?.autoRefreshToken;
    const auth = {
      getSession: async () => ({ data: { session: null }, error: null }),
      signOut: async () => ({ error: null }),
      startAutoRefresh: () => { server.started = true; },
      stopAutoRefresh: () => { server.stopped = true; },
    };
    const client = { auth };
    clients.push(client);
    return client;
  };
  const options = { serverToken: 'lifecycle-server-token', clientFactory: (manifest, identity, storage, clientOptions) => factory(manifest, identity, storage, clientOptions) };

  const persistedValues = new Map();
  const persistedStorage = {
    getItem: (key) => persistedValues.get(key) ?? null,
    removeItem: (key) => { persistedValues.delete(key); },
    setItem: (key, value) => { persistedValues.set(key, value); },
  };
  const persistedIdentity = {
    server_id: 'persisted-server',
    api_base_url: 'https://persisted.example.com/reader',
  };
  persistedValues.set('reader.connection.v2', JSON.stringify({
    base_url: 'https://persisted.example.com/reader',
    identity: persistedIdentity,
  }));
  persistedValues.set('reader.connection.v1', JSON.stringify({ base_url: 'https://legacy.reader.test', identity: { server_id: 'legacy', supabase_url: 'https://legacy.supabase.test' } }));
  assert.equal(readPersistedConnection({ ...persistedStorage, getItem: key => key === 'reader.connection.v1' ? persistedValues.get(key) : null }), null, 'legacy v1 metadata requires token entry and never activates an old session');
  const persistedMetadataBeforeConfirmation = persistedValues.get('reader.connection.v2');
  const coldStartCandidate = {
    ...discovery('replacement-server'),
    redirected: true,
    requires_confirmation: true,
    response_url: 'https://redirect.example.net/.well-known/reader.json',
    api_base_url: 'https://replacement-server.example.com/reader',
  };
  let candidateFactoryCalls = 0;
  let candidateGetSessionCalls = 0;
  let candidateAuthListenerCalls = 0;
  let candidateRefreshStarts = 0;
  let candidateCommits = 0;
  const unsubscribeCandidateCommit = subscribeConnection(() => { candidateCommits += 1; });
  const candidateFactory = () => {
    candidateFactoryCalls += 1;
    return {
      auth: {
        getSession: async () => {
          candidateGetSessionCalls += 1;
          return { data: { session: null }, error: null };
        },
        onAuthStateChange: () => {
          candidateAuthListenerCalls += 1;
          return { data: { subscription: { unsubscribe: () => {} } } };
        },
        startAutoRefresh: () => { candidateRefreshStarts += 1; },
        stopAutoRefresh: () => {},
      },
    };
  };
  await assert.rejects(
    activateReaderServer(coldStartCandidate, {
      backingStorage: persistedStorage,
      clientFactory: candidateFactory,
      expectedIdentity: persistedIdentity,
    }),
    (error) => error instanceof ConnectionError && error.code === 'redirect_confirmation_required',
  );
  assert.equal(candidateFactoryCalls, 0, 'redirect confirmation must precede candidate creation');
  assert.equal(candidateGetSessionCalls, 0);
  assert.equal(candidateAuthListenerCalls, 0);
  assert.equal(candidateRefreshStarts, 0);
  assert.equal(candidateCommits, 0);
  assert.equal(persistedValues.get('reader.connection.v2'), persistedMetadataBeforeConfirmation);

  await assert.rejects(
    activateReaderServer(coldStartCandidate, {
      backingStorage: persistedStorage,
      clientFactory: candidateFactory,
      confirmedRedirect: true,
      expectedIdentity: persistedIdentity,
    }),
    (error) => error instanceof ConnectionError && error.code === 'identity_confirmation_required',
  );
  assert.equal(candidateFactoryCalls, 0, 'identity confirmation must precede candidate session restore');
  assert.equal(candidateGetSessionCalls, 0);
  assert.equal(candidateAuthListenerCalls, 0);
  assert.equal(candidateRefreshStarts, 0);
  assert.equal(candidateCommits, 0);
  assert.equal(persistedValues.get('reader.connection.v2'), persistedMetadataBeforeConfirmation);

  await activateReaderServer(coldStartCandidate, {
    backingStorage: persistedStorage,
    clientFactory: candidateFactory,
    confirmedIdentity: true,
    confirmedRedirect: true,
    expectedIdentity: persistedIdentity,
  });
  assert.equal(candidateFactoryCalls, 1);
  assert.equal(candidateGetSessionCalls, 1);
  assert.equal(candidateAuthListenerCalls, 0, 'candidate activation does not install an auth listener');
  assert.equal(candidateRefreshStarts, 1, 'refresh starts only after runtime commit');
  assert.equal(candidateCommits, 1);
  assert.notEqual(persistedValues.get('reader.connection.v2'), persistedMetadataBeforeConfirmation);
  const cachedConnection = readPersistedConnection(persistedStorage);
  assert.equal(cachedConnection.identity.server_id, 'replacement-server');
  assert.equal(cachedConnection.discovery.api_base_url, coldStartCandidate.api_base_url, 'a trusted discovery response is cached for the next cold start');
  unsubscribeCandidateCommit();
  disconnectReaderServer();

  const restoredCachedRuntime = await restorePersistedReaderRuntime(cachedConnection, {
    backingStorage: persistedStorage,
    clientFactory: candidateFactory,
  });
  assert.equal(restoredCachedRuntime.runtime.identity.server_id, 'replacement-server');
  assert.equal(getActiveRuntime(), restoredCachedRuntime.runtime, 'cached startup publishes the prior trusted runtime before background discovery');
  disconnectReaderServer();

  const first = await activateReaderServer(discovery('one'), options);
  assert.equal(first.runtime.identity.server_id, 'one');
  assert.equal(getActiveRuntime().base_url, 'https://one.example.com/reader');
  const firstGeneration = getConnectionGeneration();
  assert.equal(first.runtime.discovery.candidateAutoRefresh, false, 'candidate clients must disable auto-refresh');
  assert.equal(first.runtime.discovery.started, true);
  assert.equal(first.runtime.authClient.auth.startAutoRefresh !== undefined, true);

  let releaseSecond;
  let secondManifest;
  const secondPending = new Promise((resolve) => { releaseSecond = resolve; });
  const second = activateReaderServer(discovery('two'), {
    clientFactory: (manifest) => {
      secondManifest = manifest;
      return {
      auth: {
        getSession: () => secondPending,
        startAutoRefresh: () => { manifest.started = true; },
        stopAutoRefresh: () => { manifest.stopped = true; },
      },
      };
    },
  });
  const third = await activateReaderServer(discovery('three'), options);
  assert.equal(getActiveRuntime().identity.server_id, 'three');
  releaseSecond({ data: { session: null }, error: null });
  await assert.rejects(second, (error) => error instanceof ConnectionError && error.code === 'stale_runtime');
  assert.equal(secondManifest.stopped, true, 'stale candidate must stop refresh');
  assert.equal(getActiveRuntime().identity.server_id, 'three');
  assert.ok(getConnectionGeneration() > firstGeneration);

  const oldRuntime = getActiveRuntime();
  let brokenManifest;
  await assert.rejects(
    activateReaderServer(discovery('broken'), {
      clientFactory: (manifest) => {
        brokenManifest = manifest;
        return {
          auth: {
            getSession: async () => ({ data: { session: null }, error: new Error('offline') }),
            stopAutoRefresh: () => { manifest.stopped = true; },
          },
        };
      },
    }),
    (error) => error instanceof ConnectionError && error.code === 'activation_failed',
  );
  assert.equal(brokenManifest.stopped, true, 'candidate session errors must stop refresh');
  assert.equal(getActiveRuntime(), oldRuntime, 'failed activation must preserve the old runtime');

  const throwingStorage = {
    getItem: () => null,
    removeItem: () => {},
    setItem: () => { throw new Error('storage unavailable'); },
  };
  let storageManifest;
  await assert.rejects(
    activateReaderServer(discovery('storage-failure'), {
      backingStorage: throwingStorage,
      clientFactory: (manifest) => {
        storageManifest = manifest;
        return {
          auth: {
            getSession: async () => ({ data: { session: null }, error: null }),
            stopAutoRefresh: () => { manifest.stopped = true; },
          },
        };
      },
    }),
    (error) => error instanceof ConnectionError && error.code === 'activation_failed',
  );
  assert.equal(storageManifest.stopped, true, 'persistence errors must stop refresh');
  assert.equal(getActiveRuntime(), oldRuntime, 'persistence failure must preserve the old runtime');

  let releaseAbort;
  let abortManifest;
  const abortPending = new Promise((resolve) => { releaseAbort = resolve; });
  const abortController = new AbortController();
  const aborted = activateReaderServer(discovery('aborted'), {
    signal: abortController.signal,
    clientFactory: (manifest) => {
      abortManifest = manifest;
      return {
        auth: {
          getSession: () => abortPending,
          stopAutoRefresh: () => { manifest.stopped = true; },
        },
      };
    },
  });
  abortController.abort();
  releaseAbort({ data: { session: null }, error: null });
  await assert.rejects(aborted, (error) => error instanceof ConnectionError && error.code === 'discovery_cancelled');
  assert.equal(abortManifest.stopped, true, 'aborted candidate must stop refresh');

  const contextBeforeSwitch = captureRuntimeContext(oldRuntime);
  assert.equal(isRuntimeContextCurrent(contextBeforeSwitch), true);

  const cache = new QueryClient();
  const feedData = (saved) => ({ pages: [{ items: [{ content_id: 'content-1', is_saved: saved }] }], pageParams: [null] });
  cache.setQueryData(readerQueryKeys.feed('user-1', 'all', 'zh-CN', oldRuntime.identity.server_id), feedData(false));
  cache.setQueryData(readerQueryKeys.feed('user-1', 'all', 'zh-CN', 'other-server'), feedData(false));
  setCachedFeedSavedState(cache, 'user-1', oldRuntime.identity.server_id, 'content-1', true, contextBeforeSwitch);
  assert.equal(cache.getQueryData(readerQueryKeys.feed('user-1', 'all', 'zh-CN', oldRuntime.identity.server_id)).pages[0].items[0].is_saved, true);
  assert.equal(cache.getQueryData(readerQueryKeys.feed('user-1', 'all', 'zh-CN', 'other-server')).pages[0].items[0].is_saved, false);

  const originalFetch = globalThis.fetch;
  let releaseResponse;
  globalThis.fetch = () => new Promise((resolve) => { releaseResponse = resolve; });
  const staleRequest = fetchWithTimeout('https://three.example.com/v1/me', {});
  const sameServerPromise = activateReaderServer(discovery('three'), options);
  releaseResponse(new Response('{}', { status: 200 }));
  await assert.rejects(staleRequest, (error) => error instanceof ConnectionError && error.code === 'stale_runtime');
  const activeSameServer = await sameServerPromise;
  globalThis.fetch = originalFetch;

  assert.equal(isRuntimeContextCurrent(contextBeforeSwitch), false, 'same server with a new generation is stale');
  setCachedFeedSavedState(cache, 'user-1', oldRuntime.identity.server_id, 'content-1', false, contextBeforeSwitch);
  assert.equal(cache.getQueryData(readerQueryKeys.feed('user-1', 'all', 'zh-CN', oldRuntime.identity.server_id)).pages[0].items[0].is_saved, true);
  let invalidationCalls = 0;
  const originalInvalidate = cache.invalidateQueries.bind(cache);
  cache.invalidateQueries = async (...args) => {
    invalidationCalls += 1;
    return originalInvalidate(...args);
  };
  await invalidateAfterSavedMutation(cache, 'user-1', oldRuntime.identity.server_id, contextBeforeSwitch);
  assert.equal(invalidationCalls, 0, 'stale mutation must not invalidate a new generation');

  const logoutEvents = [];
  const generationBeforeLogout = getConnectionGeneration();
  const logoutContext = captureRuntimeContext(activeSameServer.runtime);
  activeSameServer.runtime.authClient.auth.signOut = async () => { throw new Error('remote sign-out unavailable'); };
  await assert.rejects(
    logoutReaderSession({
      cancelQueries: async () => { logoutEvents.push('cancel'); },
      clearCaches: () => { logoutEvents.push('clear'); },
      clearSession: () => { logoutEvents.push('clear-session'); },
      runtime: activeSameServer.runtime,
      stopAuthListener: () => { logoutEvents.push('unsubscribe'); },
    }),
  );
  assert.deepEqual(logoutEvents.slice(0, 2), ['clear-session', 'unsubscribe']);
  assert.ok(logoutEvents.includes('cancel'));
  assert.equal(logoutEvents.at(-1), 'clear');
  assert.ok(getConnectionGeneration() > generationBeforeLogout);
  assert.equal(isRuntimeContextCurrent(logoutContext), false, 'logout invalidates old mutations');
  setCachedFeedSavedState(cache, 'user-1', activeSameServer.runtime.identity.server_id, 'content-1', false, logoutContext);
  assert.equal(cache.getQueryData(readerQueryKeys.feed('user-1', 'all', 'zh-CN', 'three')).pages[0].items[0].is_saved, true);

  disconnectReaderServer();
  assert.equal(getActiveRuntime(), null);
  assert.ok(getConnectionGeneration() > third.runtime.generation);
} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}

console.log('Reader connection lifecycle cancellation and atomic switching passed.');
