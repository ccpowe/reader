import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { createRequire, Module } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { mock } from 'node:test';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';

const require = createRequire(import.meta.url);
const React = require('react');
const { act, create } = require('react-test-renderer');
const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const directory = mkdtempSync(join(tmpdir(), 'reader-source-status-'));
const originalLoad = Module._load;
const previousWindow = Object.getOwnPropertyDescriptor(globalThis, 'window');
const previousActEnvironment = Object.getOwnPropertyDescriptor(globalThis, 'IS_REACT_ACT_ENVIRONMENT');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;

// Query disables polling on servers. Model the client environment before loading
// the real library; AppState below remains the hook's foreground authority.
Object.defineProperty(globalThis, 'window', {
  configurable: true,
  value: { addEventListener() {}, removeEventListener() {} },
});
const queryLibrary = require('@tanstack/react-query');
const { QueryClient, QueryClientProvider, notifyManager } = queryLibrary;
const nativeListeners = new Set();
const AppState = {
  currentState: 'active',
  addEventListener(name, listener) {
    assert.equal(name, 'change');
    nativeListeners.add(listener);
    return { remove() { nativeListeners.delete(listener); } };
  },
};
const runtime = { generation: 1, identity: { server_id: 'source-status-test' } };
const session = { access_token: 'access', user: { id: 'reader' } };
const pending = {
  source_id: 'source-1', subscription_id: 'subscription-1', kind: 'web',
  canonical_url: 'https://example.com/blog', display_name: 'Example', avatar_url: null,
  folder_name: null, status: 'pending', sync_phase: 'idle', last_complete_at: null,
  next_scan_at: '2026-09-10T00:00:00Z', gap_detected: false, backlog_cycles: 0,
  last_error_code: null,
};
const healthy = { ...pending, status: 'active', last_complete_at: '2026-09-10T00:00:01Z' };
let response = [];
let calls = 0;
let tree;
let client;
let result;
let props;

function restoreGlobal(name, descriptor) {
  if (descriptor) Object.defineProperty(globalThis, name, descriptor);
  else delete globalThis[name];
}

try {
  buildSync({
    entryPoints: [join(mobileRoot, 'hooks/useSources.ts')],
    outfile: join(directory, 'sources.cjs'),
    bundle: true,
    platform: 'node',
    format: 'cjs',
    packages: 'external',
    external: ['../lib/api', '../lib/connection/react', '../lib/connection/runtime', '../i18n'],
  });
  Module._load = function load(request, parent, isMain) {
    if (request === 'react') return React;
    if (request === '@tanstack/react-query') return queryLibrary;
    if (request === 'react-native') return { AppState };
    if (request === '../lib/api') return {
      getSources: async (currentSession, currentRuntime) => {
        assert.equal(currentSession, session);
        assert.equal(currentRuntime, runtime);
        calls += 1;
        if (response instanceof Error) throw response;
        return response;
      },
    };
    if (request === '../lib/connection/react') return { useReaderRuntime: () => runtime };
    if (request === '../lib/connection/runtime') return { getActiveRuntime: () => runtime };
    if (request === '../i18n') return { i18n: { t: key => key } };
    return originalLoad.call(this, request, parent, isMain);
  };
  const { useSources } = require(join(directory, 'sources.cjs'));
  mock.timers.enable({ apis: ['Date', 'setTimeout', 'setInterval'], now: Date.parse('2026-09-10T00:00:00Z') });
  // Deliver real query notifications within act without spending simulated time
  // on their zero-delay scheduling. Query's polling timers remain unchanged.
  notifyManager.setScheduler(queueMicrotask);

  function Harness(input) {
    result = useSources(session, input.active, input.refreshStatus);
    return null;
  }
  const render = () => React.createElement(QueryClientProvider, { client }, React.createElement(Harness, props));
  async function unmount() {
    if (tree) {
      await act(async () => { tree.unmount(); });
      tree = undefined;
    }
    client?.clear();
    client = undefined;
    assert.equal(nativeListeners.size, 0, 'unmount removes the AppState subscription');
  }
  async function mount(items, overrides = {}) {
    await unmount();
    response = items;
    calls = 0;
    AppState.currentState = 'active';
    props = { active: true, refreshStatus: true, ...overrides };
    client = new QueryClient({
      defaultOptions: {
        queries: { staleTime: 60_000, gcTime: Infinity, retry: false, refetchOnWindowFocus: false },
      },
    });
    await act(async () => { tree = create(render()); });
  }
  async function advance(milliseconds) {
    await act(async () => { mock.timers.tick(milliseconds); });
  }
  async function update(overrides) {
    props = { ...props, ...overrides };
    await act(async () => { tree.update(render()); });
  }
  async function changeAppState(state) {
    await act(async () => {
      AppState.currentState = state;
      for (const listener of [...nativeListeners]) listener(state);
    });
  }

  const repeatedPending = [pending];
  await mount(repeatedPending);
  assert.equal(calls, 1, 'mount fetches subscriptions');
  assert.equal(result.items, repeatedPending);
  await advance(2_999);
  assert.equal(calls, 1, 'pending status does not poll before three seconds');
  await advance(1);
  assert.equal(calls, 2);
  await advance(3_000);
  assert.equal(calls, 3, 'an identical pending response must not cancel the next refresh');
  await advance(3_000);
  assert.equal(calls, 4, 'polling survives multiple referentially identical responses');
  assert.equal(result.items, repeatedPending, 'exercise Query structural sharing without changing the data reference');

  response = [healthy];
  await advance(3_000);
  assert.equal(calls, 5);
  assert.equal(result.items[0].status, 'active', 'the mounted hook receives the completed subscription status');
  await advance(29_999);
  assert.equal(calls, 5, 'pending-to-active transition stops the three-second cadence');
  await advance(1);
  assert.equal(calls, 6, 'healthy subscriptions continue refreshing at thirty seconds');
  console.log('PASS identical pending responses keep polling and completion reduces refresh frequency');

  await mount([{ ...healthy, sync_phase: 'degraded', last_error_code: 'web_rule_repairing' }]);
  await advance(2_999);
  assert.equal(calls, 1);
  await advance(1);
  assert.equal(calls, 2, 'an active source under rule repair uses the transient cadence');
  await advance(3_000);
  assert.equal(calls, 3, 'unchanged repair state continues polling');

  for (const code of ['web_rule_authoring_failed', 'web_rule_agent_unavailable']) {
    await mount([{ ...pending, sync_phase: 'degraded', last_error_code: code }]);
    await advance(29_999);
    assert.equal(calls, 1, `${code} does not poll rapidly merely because the source remains pending`);
    await advance(1);
    assert.equal(calls, 2, `${code} retains the thirty-second status refresh`);
  }
  console.log('PASS rule repair stays responsive and terminal failures use the slower cadence');

  await mount(repeatedPending);
  const temporaryError = new Error('Temporary source status request failure');
  response = temporaryError;
  await advance(3_000);
  assert.equal(calls, 2, 'exercise a failed status refresh after a successful initial response');
  assert.equal(result.items, repeatedPending, 'a temporary API failure preserves the last subscription items');
  assert.equal(result.error, temporaryError);
  assert.equal(result.message, temporaryError.message);
  response = [healthy];
  await advance(29_999);
  assert.equal(calls, 2, 'an API failure backs off even when retained items still have pending status');
  assert.equal(result.items, repeatedPending);
  await advance(1);
  assert.equal(calls, 3, 'a failed status refresh retries after thirty seconds');
  assert.equal(result.items[0].status, 'active', 'the retry replaces retained items with the recovered response');
  assert.equal(result.error, null, 'successful recovery clears the query error');
  assert.equal(result.message, '', 'successful recovery clears the error message');
  console.log('PASS temporary API errors preserve subscription items and recover after thirty seconds');

  await mount(repeatedPending);
  let finishRequest;
  response = new Promise(resolve => { finishRequest = resolve; });
  await advance(3_000);
  assert.equal(calls, 2, 'start a status request that remains unresolved');
  assert.equal(result.isFetching, true);
  for (let cycle = 0; cycle < 3; cycle += 1) {
    await advance(3_000);
    assert.equal(calls, 2, 'multiple polling cycles share the existing unfinished request');
    assert.equal(result.items, repeatedPending, 'items stay visible while a refresh is unfinished');
  }
  response = repeatedPending;
  await act(async () => { finishRequest(response); });
  assert.equal(result.isFetching, false);
  assert.equal(result.items, repeatedPending);
  await advance(2_999);
  assert.equal(calls, 2, 'settling the request schedules the next polling interval');
  await advance(1);
  assert.equal(calls, 3, 'polling continues after the shared unfinished request settles');
  console.log('PASS pending API requests do not overlap and polling continues after completion');

  await mount([pending]);
  await update({ active: false });
  await advance(60_000);
  assert.equal(calls, 1, 'leaving the subscription page stops scheduled refreshes');
  await update({ active: true });
  const callsAfterActivation = calls;
  await advance(3_000);
  assert.equal(calls, callsAfterActivation + 1, 'returning to the page restarts status refreshes');

  await mount([pending]);
  await changeAppState('background');
  await advance(60_000);
  assert.equal(calls, 1, 'backgrounding the app stops polling even while the page remains active');
  await changeAppState('active');
  const callsAfterForeground = calls;
  await advance(3_000);
  assert.equal(calls, callsAfterForeground + 1, 'foregrounding the app restarts status refreshes');
  console.log('PASS inactive pages and background apps stop polling and resume on return');

  await mount([]);
  await advance(90_000);
  assert.equal(calls, 1, 'an empty subscription list has no status polling');
  assert.deepEqual(result.items, []);
  await mount([pending], { refreshStatus: false });
  await advance(90_000);
  assert.equal(calls, 1, 'other useSources consumers do not opt into status polling by default');
  await unmount();
  console.log('PASS empty lists and consumers without status refresh do not poll');
} finally {
  if (tree) await act(async () => { tree.unmount(); });
  client?.clear();
  notifyManager.setScheduler(callback => setTimeout(callback, 0));
  mock.timers.reset();
  Module._load = originalLoad;
  restoreGlobal('window', previousWindow);
  restoreGlobal('IS_REACT_ACT_ENVIRONMENT', previousActEnvironment);
  rmSync(directory, { recursive: true, force: true });
}
