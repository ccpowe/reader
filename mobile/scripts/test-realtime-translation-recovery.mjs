import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import Module from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-realtime-scheduler-'));
const require = createRequire(import.meta.url);
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();

try {
  execFileSync(process.execPath, [
    join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
    '--ignoreConfig', 'hooks/useRealtimeTranslationScheduler.ts',
    '--target', 'es2022', '--module', 'commonjs', '--jsx', 'react-jsx',
    '--outDir', outputDirectory, '--lib', 'dom,es2022', '--types', 'node', '--skipLibCheck',
  ], { cwd: mobileRoot, stdio: 'inherit' });

  const React = require('react');
  const { act, create } = require('react-test-renderer');
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const requests = [];
  const diagnostics = [];
  let now = 10_000;
  let sequence = 0;
  const timers = new Map();
  const realTimeout = globalThis.setTimeout, realClear = globalThis.clearTimeout, realNow = Date.now, realRandom = Math.random;
  globalThis.setTimeout = (fn, delay) => { const id = ++sequence; timers.set(id, { fn, at: now + delay }); return id; };
  globalThis.clearTimeout = id => timers.delete(id);
  Date.now = () => now;
  Math.random = () => 0;
  const advance = async ms => {
    const target = now + ms;
    while (true) {
      const next = [...timers].filter(([, task]) => task.at <= target).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      now = next[1].at; timers.delete(next[0]);
      await act(async () => { next[1].fn(); });
    }
    now = target;
  };
  const delivered = [];
  const runtime = { generation: 1, identity: { server_id: 'scheduler-test' } };
  const originalLoad = Module._load;
  Module._load = function load(request, parent, isMain) {
    if (request === '../domain/translationDiagnostics') return { recordTranslationDiagnostic: (event, fields) => diagnostics.push({ event, fields }) };
    if (request === '../lib/api') return {
      resolveTranslationSegments: async (_session, segments, signal) => {
        const deferred = { segments, signal };
        requests.push(deferred);
        return await new Promise((resolve, reject) => { deferred.resolve = resolve; deferred.reject = reject; });
      },
    };
    if (request === '../lib/connection/react') return {
      useReaderRuntime: () => runtime,
      useReaderRuntimeGeneration: () => 1,
    };
    if (request === '../lib/connection') return {
      captureRuntimeContext: () => ({ generation: 1, runtime, serverId: 'scheduler-test' }),
      isRuntimeContextCurrent: () => true,
    };
    return originalLoad.call(this, request, parent, isMain);
  };

  const { useRealtimeTranslationScheduler } = require(join(outputDirectory, 'hooks/useRealtimeTranslationScheduler.js'));
  const { i18n } = require(join(outputDirectory, 'i18n/index.js'));
  await i18n.changeLanguage('zh-CN');
  let scheduler;
  const mountScheduler = async () => {
    function Harness() {
      scheduler = useRealtimeTranslationScheduler({
        enabled: true,
        onResults: (results) => delivered.push(...results),
        session: { access_token: 'token' },
        targetLocale: 'zh-CN',
      });
      return null;
    }
    let mounted;
    await act(async () => { mounted = create(React.createElement(Harness)); });
    return mounted;
  };

  let tree;
  const fresh = async () => {
    if (tree) await act(async () => tree.unmount());
    requests.length = 0; delivered.length = 0; diagnostics.length = 0;
    tree = await mountScheduler();
  };
  const segment = id => ({ segment_id: id, purpose: 'web_segment', text: `text-${id}` });
  const enqueue = async (ids, windowId = 'page', id = `batch-${sequence++}`) => act(async () => scheduler.enqueuePlan([
    { id, priority: 'urgent', windowId, segments: ids.map(segment) },
  ]));
  const respond = async (index, results) => act(async () => requests[index].resolve(results));
  const reject = async (index, error = new TypeError('network failed')) => act(async () => requests[index].reject(error));
  const success = id => ({ segment_id: id, translation_status: 'succeeded', translated_text: 'translated' });
  const pending = id => ({ segment_id: id, translation_status: 'pending', retry_after_ms: 750 });

  await fresh(); await enqueue(['a', 'b']);
  await respond(0, [success('a'), pending('b')]);
  await advance(750);
  assert.deepEqual(requests[1].segments.map(s => s.segment_id), ['b']);
  await reject(1);
  assert.equal(scheduler.error, null);
  assert.deepEqual(delivered, [{ ...success('a'), cache_expires_at: '1970-01-01T01:00:10.000Z' }],
    'recovery preserves the original successful receipt deadline and must not notify DOM failure');
  await act(async () => scheduler.replaceWindow('away'));
  await enqueue(['a', 'b'], 'back');
  assert.equal(requests.length, 2);
  await advance(999); assert.equal(requests.length, 2);
  await advance(1); assert.equal(requests.length, 3);
  assert.deepEqual(requests[2].segments.map(s => s.segment_id), ['b']);
  await respond(2, [success('b')]);
  assert.equal(delivered.filter(result => result.translation_status === 'failed').length, 0);
  console.log('PASS mixed success/pending, automatic recovery, viewport cooldown and successful cache');

  await fresh(); await enqueue(['budget']);
  await reject(0); await advance(1000);
  await respond(1, [pending('budget')]); await advance(750);
  await reject(2); await advance(2000);
  await respond(3, [pending('budget')]); await advance(750);
  await reject(4);
  assert.equal(delivered.length, 1); assert.equal(delivered[0].translation_status, 'failed');
  await advance(60_000); await enqueue(['budget']); assert.equal(requests.length, 5);
  assert.deepEqual(diagnostics.filter(d => d.event === 'request_recovery_scheduled').map(d => d.fields.attempt), [1, 2]);
  assert.equal(diagnostics.filter(d => d.event === 'request_recovery_exhausted').length, 1);
  await act(async () => scheduler.retryFailed()); await enqueue(['budget']);
  await reject(5); await advance(1000); assert.equal(requests.length, 7, 'explicit retry starts a fresh bounded budget');
  console.log('PASS pending polls never reset two-recovery budget, terminal replay and explicit retry');

  await fresh(); await enqueue(['old-budget']);
  await reject(0); await advance(1000); await reject(1);
  await act(async () => scheduler.replaceWindow('away'));
  await enqueue(['old-budget', 'fresh-budget'], 'returned'); await advance(2000);
  await reject(2);
  assert.deepEqual(delivered.map(result => result.segment_id), ['old-budget']);
  await advance(1000);
  assert.deepEqual(requests[3].segments.map(segment => segment.segment_id), ['fresh-budget']);
  await reject(3); await advance(2000); await respond(4, [success('fresh-budget')]);
  assert.equal(delivered.at(-1).translation_status, 'succeeded');
  console.log('PASS viewport regrouping preserves each segment own recovery budget');

  await fresh(); await enqueue(['failed-cooldown', 'pending-only']);
  await respond(0, [{ segment_id: 'failed-cooldown', translation_status: 'failed', retry_after_ms: 300_000 }, pending('pending-only')]);
  await advance(750); assert.equal(requests.length, 2);
  assert.deepEqual(requests[1].segments.map(segment => segment.segment_id), ['pending-only']);
  await respond(1, [success('pending-only')]);
  console.log('PASS failed segment cooldown cannot delay unrelated pending polling');

  await fresh(); await enqueue(['rate']);
  const busy = Object.assign(new Error('busy'), { status: 429, retryAfterMs: 5000 });
  await reject(0, busy); await advance(4999); assert.equal(requests.length, 1);
  await advance(1); await reject(1, busy); await advance(5000); await reject(2, busy);
  await act(async () => scheduler.retryFailed()); await enqueue(['rate']);
  await advance(4999); assert.equal(requests.length, 3);
  await advance(1); assert.equal(requests.length, 4, 'manual retry respects final HTTP cooldown');
  console.log('PASS Retry-After throughout automatic and manual recovery');

  for (const status of [401, 422, 501]) {
    await fresh(); await enqueue(['invalid']); await reject(0, Object.assign(new Error('invalid'), { status }));
    assert.equal(delivered[0].translation_status, 'failed');
    await advance(60_000); assert.equal(requests.length, 1);
  }
  for (const kind of ['parse', 'contract', 'abort', 'unknown']) {
    await fresh(); await enqueue(['invalid']); await reject(0, Object.assign(new Error('invalid'), { kind }));
    assert.equal(delivered[0].translation_status, 'failed'); await advance(60_000); assert.equal(requests.length, 1);
  }
  console.log('PASS nonrecoverable HTTP, parse, contract and abort errors have no automatic retry');

  await fresh(); await enqueue(['retired']); await reject(0);
  await act(async () => scheduler.forgetSegments(['retired'])); await advance(60_000);
  assert.equal(requests.length, 1); assert.equal(scheduler.activeCount, 0);
  await enqueue(['old']); await act(async () => scheduler.reset());
  assert.equal(requests[1].signal.aborted, true); assert.equal(scheduler.activeCount, 0);
  await enqueue(['new']); await reject(1);
  assert.equal(scheduler.activeCount, 1, 'old completion cannot decrement current in-flight count');
  assert.equal(delivered.length, 0);
  await act(async () => tree.unmount());
  assert.equal(requests[2].signal.aborted, true); assert.equal(timers.size, 0);
  globalThis.setTimeout = realTimeout; globalThis.clearTimeout = realClear; Date.now = realNow; Math.random = realRandom;
  console.log('PASS retirement, generation cancellation, in-flight accounting and timer cleanup');
} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}
