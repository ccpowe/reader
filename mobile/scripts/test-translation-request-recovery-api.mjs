import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import Module from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const out = mkdtempSync(join(tmpdir(), 'reader-request-recovery-'));
const require = createRequire(import.meta.url);
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();
const originalLoad = Module._load, originalFetch = globalThis.fetch;
const originalTimeout = globalThis.setTimeout, originalClear = globalThis.clearTimeout;
try {
  execFileSync(process.execPath, [join(mobileRoot, 'node_modules/typescript/bin/tsc'), '--ignoreConfig',
    'lib/api.ts', '--target', 'es2022', '--module', 'commonjs', '--outDir', out,
    '--lib', 'dom,es2022', '--types', 'node', '--skipLibCheck'], { cwd: mobileRoot, stdio: 'inherit' });
  globalThis.__DEV__ = true;
  const runtime = { generation: 1, api_base_url: 'https://reader.test' };
  let generation = 1;
  Module._load = function(request, parent, isMain) {
    if (request === './connection/snapshot') return { getActiveRuntimeSnapshot: () => runtime, getConnectionGenerationSnapshot: () => generation, getReaderSessionEpochSnapshot: () => 0, subscribeReaderSessionEpochSnapshot: () => () => {} };
    return originalLoad.call(this, request, parent, isMain);
  };
  const { resolveTranslationSegments } = require(join(out, 'lib/api.js'));
  const { translationRequestFailure, translationRecoveryDelay } = require(join(out, 'domain/translationRequestErrors.js'));
  const { clearTranslationDiagnostics, exportTranslationDiagnostics } = require(join(out, 'domain/translationDiagnostics.js'));
  const session = { access_token: 'private-access-token-never-log' };
  const segments = [{ segment_id: 'one', purpose: 'web_segment', text: 'sensitive source paragraph never log' }];
  const result = { segment_id: 'one', purpose: 'web_segment', translated_text: '译文', error_code: null, translation_status: 'succeeded', retry_after_ms: null };
  const invoke = signal => resolveTranslationSegments(session, segments, signal, runtime);
  const events = () => JSON.parse(exportTranslationDiagnostics()).events;
  const finished = () => events().findLast(e => e.event === 'request_finished').fields;
  const checkFailure = async (kind, retryable, status) => {
    await assert.rejects(invoke(), error => {
      assert.equal(translationRequestFailure(error).kind, kind);
      assert.equal(translationRequestFailure(error).retryable, retryable);
      return true;
    });
    assert.equal(finished().reason, kind);
    if (status) assert.equal(finished().httpStatus, status);
  };
  globalThis.fetch = async () => new Response(JSON.stringify([result]));
  assert.deepEqual(await invoke(), [result]);
  assert.equal(finished().httpStatus, 200); assert.equal(finished().status, 'succeeded');
  assert.equal(events()[0].fields.requestId, finished().requestId);
  for (const status of [408, 429, 500, 502, 503, 504, 401, 422, 501]) {
    clearTranslationDiagnostics();
    globalThis.fetch = async () => new Response('<html>error</html>', { status, headers: { 'Retry-After': '7' } });
    await checkFailure('http', ![401, 422, 501].includes(status), status);
    assert.equal(finished().retryAfterMs, 7000);
  }
  for (const body of ['not json', JSON.stringify([]), JSON.stringify([{...result, segment_id: 'wrong'}]),
    JSON.stringify([{...result, translated_text: null}]), JSON.stringify([{...result, translation_status: 'invalid'}])]) {
    clearTranslationDiagnostics(); globalThis.fetch = async () => new Response(body);
    await checkFailure(body === 'not json' ? 'parse' : 'contract', false, 200);
  }
  clearTranslationDiagnostics(); globalThis.fetch = async () => { throw new TypeError('network secret body'); };
  await checkFailure('network', true);

  const timers = new Map(); let timerId = 0;
  globalThis.setTimeout = (fn, delay) => { const id = ++timerId; timers.set(id, { fn, delay }); return id; };
  globalThis.clearTimeout = id => timers.delete(id);
  let fetchSignal;
  // Headers arrive; only the actual fetch signal can release the hung body.
  globalThis.fetch = async (_url, options) => {
    fetchSignal = options.signal;
    return { ok: true, status: 200, text: () => new Promise((_resolve, reject) => {
      fetchSignal.addEventListener('abort', () => reject(new TypeError('RN body cancelled')), { once: true });
    }) };
  };
  clearTranslationDiagnostics();
  const timeoutPromise = checkFailure('timeout', true, 200);
  await Promise.resolve(); await Promise.resolve();
  assert.equal([...timers.values()][0].delay, 15_000);
  [...timers.values()][0].fn(); await timeoutPromise;
  assert.equal(fetchSignal.aborted, true); assert.equal(timers.size, 0);
  clearTranslationDiagnostics();
  const controller = new AbortController();
  const abortPromise = assert.rejects(invoke(controller.signal), error => error.code === 'discovery_cancelled');
  await Promise.resolve(); await Promise.resolve(); controller.abort(); await abortPromise;
  assert.equal(finished().reason, 'abort'); assert.equal(timers.size, 0);
  globalThis.fetch = async () => { generation = 2; return new Response(JSON.stringify([result])); };
  clearTranslationDiagnostics(); await checkFailure('abort', false); generation = 1;
  assert.equal(timers.size, 0);
  const exported = exportTranslationDiagnostics();
  assert.ok(!exported.includes(session.access_token)); assert.ok(!exported.includes(segments[0].text));
  assert.ok(!exported.includes('reader.test')); assert.ok(!exported.includes('RN body'));
  assert.equal(translationRecoveryDelay(1, 0, 0), 1000);
  assert.equal(translationRecoveryDelay(2, 0, 1), 2250);
  assert.equal(translationRecoveryDelay(1, 5000, 1), 5000);
  console.log('PASS API error classification, request correlation/privacy, body timeout, RN cancellation, stale runtime and bounded jitter');
} finally {
  Module._load = originalLoad; globalThis.fetch = originalFetch;
  globalThis.setTimeout = originalTimeout; globalThis.clearTimeout = originalClear;
  rmSync(out, { recursive: true, force: true });
}
