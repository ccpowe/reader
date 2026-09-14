import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import Module from 'node:module';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-title-convergence-'));
const require = createRequire(import.meta.url);
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();
let originalNow;
let originalSetTimeout;
let originalClearTimeout;

try {
  execFileSync(
    process.execPath,
    [
      join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
      '--ignoreConfig',
      'hooks/useTitleTranslationConvergence.ts',
      '--target',
      'es2022',
      '--module',
      'commonjs',
      '--jsx',
      'react-jsx',
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

  const React = require('react');
  const { act, create } = require('react-test-renderer');
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const start = 10_000;
  let now = start;
  const timers = [];
  let queryOptions;
  const immediateQueries = [];
  let resolveCalls = 0;
  let currentRuntime = {
    generation: 1,
    identity: { server_id: 'title-test', api_base_url: 'https://reader.test' },
  };
  const originalLoad = Module._load;
  originalNow = Date.now;
  originalSetTimeout = globalThis.setTimeout;
  originalClearTimeout = globalThis.clearTimeout;
  Date.now = () => now;
  globalThis.setTimeout = (callback, delay) => {
    const timer = { callback, delay };
    timers.push(timer);
    return timer;
  };
  globalThis.clearTimeout = () => {};
  Module._load = function load(request, parent, isMain) {
    if (request === '@tanstack/react-query') {
      return {
        useQueryClient: () => ({
          fetchQuery: async (options) => {
            immediateQueries.push(options);
            return options.queryFn();
          },
          setQueryData: () => {},
        }),
        useQuery: (options) => {
          queryOptions = options;
          return { data: [], state: { data: [], error: null } };
        },
      };
    }
    if (request === '../lib/api') {
      return {
        resolveTitleTranslations: async () => {
          resolveCalls += 1;
          return [{
            content_id: 'content-1',
            error_code: null,
            engine_id: 'engine-1',
            engine_label: 'test',
            retry_after_ms: 1_000,
            translated_title: null,
            translation_locale: 'zh-CN',
            translation_status: 'pending',
          }];
        },
      };
    }
    if (request === '../state/cacheUpdates') {
      return { mergeCachedTitleTranslations: () => {} };
    }
    if (request === '../state/queryClient') {
      return { readerQueryKeys: { titleTranslations: (...args) => ['titles', ...args] } };
    }
    if (request === '../lib/connection/react') {
      return {
        useReaderRuntime: () => currentRuntime,
        useReaderRuntimeGeneration: () => currentRuntime.generation,
      };
    }
    if (request === '../lib/connection') {
      return {
        captureRuntimeContext: (runtime) => runtime === currentRuntime
          ? { generation: runtime.generation, runtime, serverId: runtime.identity.server_id }
          : null,
        isRuntimeContextCurrent: (context) => context?.runtime === currentRuntime && context.generation === currentRuntime.generation,
      };
    }
    return originalLoad.call(this, request, parent, isMain);
  };

  const { clearTitleConvergenceRegistry, useTitleTranslationConvergence } = require(join(outputDirectory, 'hooks/useTitleTranslationConvergence.js'));
  clearTitleConvergenceRegistry();
  let result;
  const items = [{ content_id: 'content-1', translation_status: 'pending' }];
  function Harness({ value }) {
    result = useTitleTranslationConvergence(
      { user: { id: 'user-1' } },
      value,
      true,
      'zh-CN',
      'engine-1',
      'feed:home',
    );
    return React.createElement('Text', null, result.timedOutContentIds.join(','));
  }

  let tree;
  await act(async () => { tree = create(React.createElement(Harness, { value: items })); });
  assert.equal(timers.length, 1);
  assert.equal(timers[0].delay, 45_000, 'first pending title receives one 45-second window');
  assert.equal(queryOptions.enabled, true);
  const deadlinePoll = queryOptions.refetchInterval({ state: { data: [{ content_id: 'content-1', translation_status: 'running', retry_after_ms: 90_000 }], error: null } });
  now = start + 44_000;
  const boundedPoll = queryOptions.refetchInterval({ state: { data: [{ content_id: 'content-1', translation_status: 'running', retry_after_ms: 90_000 }], error: null } });
  assert.ok(deadlinePoll <= 45_000);
  assert.equal(boundedPoll, 1_000, 'retry_after is capped at the fixed deadline');

  now = start + 45_000;
  await act(async () => { timers[0].callback(); });
  assert.deepEqual(result.timedOutContentIds, ['content-1']);
  assert.equal(queryOptions.enabled, false, 'timeout stops automatic polling');

  // A new array, rerender, and re-entry with the same scope/content cannot
  // implicitly reopen the timed-out window.
  await act(async () => { tree.update(React.createElement(Harness, { value: [...items] })); });
  assert.deepEqual(result.timedOutContentIds, ['content-1']);
  assert.equal(queryOptions.enabled, false);

  // Only the explicit action reopens the fixed window and performs an
  // immediate production status query.
  now = start + 45_000;
  await act(async () => { await result.retryTimedOut(); });
  assert.deepEqual(result.timedOutContentIds, []);
  assert.equal(queryOptions.enabled, true);
  assert.equal(immediateQueries.length, 1);
  assert.deepEqual(immediateQueries[0].queryKey[4], ['content-1']);
  assert.equal(resolveCalls, 1);
  assert.equal(timers.length, 2);
  assert.equal(timers[1].delay, 45_000, 'explicit retry receives a fresh 45-second window');

  // A terminal result retires that generation; a later pending state is a new
  // demand and receives a fresh deadline only because it became terminal first.
  await act(async () => { tree.update(React.createElement(Harness, { value: [{ content_id: 'content-1', translation_status: 'succeeded' }] })); });
  await act(async () => { tree.update(React.createElement(Harness, { value: items })); });
  assert.equal(timers.length, 3);
  assert.equal(timers[2].delay, 45_000);
} finally {
  Date.now = originalNow;
  globalThis.setTimeout = originalSetTimeout;
  globalThis.clearTimeout = originalClearTimeout;
  rmSync(outputDirectory, { force: true, recursive: true });
}

console.log('Mounted title-convergence deadline behavior passed.');
