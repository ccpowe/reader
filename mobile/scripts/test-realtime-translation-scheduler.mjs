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
  const delivered = [];
  const runtime = { generation: 1, identity: { server_id: 'scheduler-test' } };
  const originalLoad = Module._load;
  Module._load = function load(request, parent, isMain) {
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
  const { localizedMessage } = require(join(outputDirectory, 'i18n/message.js'));
  const { TranslationRequestError } = require(join(outputDirectory, 'domain/translationRequestErrors.js'));
  await i18n.changeLanguage('zh-CN');
  let scheduler;
  const mountScheduler = async () => {
    const session = { access_token: 'token' };
    function Harness() {
      scheduler = useRealtimeTranslationScheduler({
        enabled: true,
        onResults: (results) => delivered.push(...results),
        session,
        targetLocale: 'zh-CN',
      });
      return null;
    }
    let mounted;
    await act(async () => { mounted = create(React.createElement(Harness)); });
    return mounted;
  };
  let tree = await mountScheduler();
  const batch = (id, priority, windowId, segmentId) => ({
    id, priority, windowId,
    segments: [{ purpose: 'web_segment', segment_id: segmentId, text: `text-${segmentId}` }],
  });
  const succeeded = (segmentId) => ({ segment_id: segmentId, translated_text: `translated-${segmentId}`, translation_status: 'succeeded' });
  const failed = (segmentId) => ({ segment_id: segmentId, translated_text: null, translation_status: 'failed' });

  await act(async () => {
    scheduler.enqueuePlan(Array.from({ length: 10 }, (_, index) => batch(`active-${index}`, 'urgent', 'window-active', `active-${index}`)));
    await Promise.resolve();
  });
  assert.equal(requests.length, 10, 'the concurrency ceiling must leave later window work unsent');

  await act(async () => {
    scheduler.enqueuePlan([batch('old-prefetch', 'prefetch', 'window-old', 'fold-segment')]);
    scheduler.replaceWindow('window-away');
    scheduler.enqueuePlan([batch('return-urgent', 'urgent', 'window-return', 'fold-segment')]);
    requests[0].resolve([failed('active-0')]);
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  assert.equal(requests.length, 11);
  assert.equal(requests[10].segments[0].segment_id, 'fold-segment', 'a cancelled unsent segment must reattach when the viewport returns');
  assert.equal(delivered[0].translation_status, 'failed', 'terminal failure must return to the DOM runtime state machine');
  assert.equal(scheduler.error, 'AI 翻译失败，请稍后重试。');
  await act(async () => { await i18n.changeLanguage('en'); });
  assert.equal(scheduler.error, 'AI translation failed. Please try again later.');
  assert.equal(requests.length, 11, 'interface language changes must not enqueue another provider request');
  assert.ok(requests.slice(1).every((request) => !request.signal.aborted),
    'interface language changes must preserve in-flight translation requests');
  await act(async () => { await i18n.changeLanguage('zh-CN'); });
  assert.equal(scheduler.error, 'AI 翻译失败，请稍后重试。');
  await act(async () => {
    requests[10].resolve([succeeded('fold-segment')]);
    await new Promise((resolve) => setTimeout(resolve, 20));
    scheduler.enqueuePlan([batch('terminal-race', 'urgent', 'window-race', 'fold-segment')]);
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  assert.equal(requests.length, 11, 'terminal identity must remain attached while result injection converges');
  assert.equal(delivered.filter((result) => result.segment_id === 'fold-segment').length, 2,
    'a rebuilt DOM must receive the cached terminal result without another provider request');

  await act(async () => { tree.unmount(); });
  tree = await mountScheduler();
  requests.splice(0);
  await act(async () => {
    scheduler.enqueuePlan(Array.from({ length: 10 }, (_, index) => batch(`hold-${index}`, 'urgent', 'hold-window', `hold-${index}`)));
    scheduler.enqueuePlan([
      batch('upgrade-prefetch', 'prefetch', 'upgrade-window', 'upgrade-segment'),
      batch('later-prefetch', 'prefetch', 'upgrade-window', 'later-segment'),
    ]);
    scheduler.enqueuePlan([batch('upgrade-urgent', 'urgent', 'upgrade-window-new', 'upgrade-segment')]);
    requests[0].resolve([succeeded('hold-0')]);
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  assert.equal(requests[10].segments[0].segment_id, 'upgrade-segment', 'queued prefetch must upgrade ahead of remaining prefetch work');

  await act(async () => { tree.unmount(); });
  tree = await mountScheduler();
  requests.splice(0);
  const heldTransportError = new TranslationRequestError(localizedMessage('errors:translationSegmentResponseInvalid'), 'contract');
  await act(async () => {
    scheduler.enqueuePlan([batch('transport-failure', 'urgent', 'transport-window', 'transport-segment')]);
    await Promise.resolve();
    requests[0].reject(heldTransportError);
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  assert.equal(delivered.at(-1).segment_id, 'transport-segment');
  assert.equal(delivered.at(-1).translation_status, 'failed', 'transport failure must terminate the DOM entity without viewport retries');
  assert.equal(scheduler.error, '翻译服务请求失败，请稍后重试。');
  await act(async () => { await i18n.changeLanguage('en'); });
  assert.equal(scheduler.error, 'The translation request failed. Please try again later.');
  assert.equal(heldTransportError.message, 'The segment translation API returned invalid data.');
  assert.equal(requests.length, 1, 'translating a held transport error must not retry it');
  await act(async () => { await i18n.changeLanguage('zh-CN'); });
  await act(async () => {
    scheduler.enqueuePlan([batch('transport-repeat', 'urgent', 'transport-window-2', 'transport-segment')]);
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  assert.equal(requests.length, 1, 'transport failure must not retry on every viewport plan');
  const deliveredBeforeReset = delivered.length;
  await act(async () => {
    scheduler.reset();
    scheduler.enqueuePlan([batch('transport-after-reset', 'urgent', 'transport-window-3', 'transport-segment')]);
    await Promise.resolve();
  });
  assert.equal(requests.length, 2, 'explicit reset must release a terminal transport identity for a user retry');
  assert.equal(delivered.length, deliveredBeforeReset, 'reset must discard cached results from the old generation');
  await act(async () => { tree.unmount(); });

  tree = await mountScheduler();
  requests.splice(0);
  await act(async () => {
    scheduler.enqueuePlan([batch('old-generation', 'urgent', 'old-window', 'generation-segment')]);
    await Promise.resolve();
    scheduler.reset();
    scheduler.enqueuePlan([batch('new-generation', 'urgent', 'new-window', 'generation-segment')]);
    await Promise.resolve();
    requests[0].reject(new TypeError('obsolete request failed late'));
    await new Promise((resolve) => setTimeout(resolve, 20));
    scheduler.enqueuePlan([batch('third-plan', 'urgent', 'third-window', 'generation-segment')]);
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  assert.equal(requests.length, 2, 'an obsolete generation rejection must not release the new owner for the same segment');
  await act(async () => { tree.unmount(); });
  tree = await mountScheduler();
  requests.splice(0);
  await act(async () => {
    scheduler.enqueuePlan([batch('pending-delay', 'urgent', 'pending-window', 'pending-segment')]);
    await Promise.resolve();
    requests[0].resolve([{ segment_id: 'pending-segment', translation_status: 'pending', retry_after_ms: 900 }]);
    await new Promise((resolve) => setTimeout(resolve, 80));
  });
  assert.equal(requests.length, 1, 'finally must not bypass the server retry deadline');
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 950)); });
  assert.equal(requests.length, 2, 'pending shared task polls after its retry deadline');
  await act(async () => { tree.unmount(); });
  tree = await mountScheduler();
  requests.splice(0);
  await act(async () => {
    scheduler.enqueuePlan([batch('cooldown-first', 'urgent', 'cooldown-window', 'cooldown-segment')]);
    await Promise.resolve();
    requests[0].resolve([{ segment_id: 'cooldown-segment', translation_status: 'failed', retry_after_ms: 900 }]);
    await new Promise((resolve) => setTimeout(resolve, 20));
    scheduler.retryFailed();
    scheduler.enqueuePlan([batch('cooldown-explicit', 'urgent', 'cooldown-window', 'cooldown-segment')]);
    await new Promise((resolve) => setTimeout(resolve, 80));
  });
  await act(async () => {
    scheduler.replaceWindow('scrolled-away');
    scheduler.enqueuePlan([batch('cooldown-returned', 'urgent', 'returned-window', 'cooldown-segment')]);
    await new Promise((resolve) => setTimeout(resolve, 20));
  });
  assert.equal(requests.length, 1, 'scrolling away and back retains the explicit retry cooldown');
  assert.ok(scheduler.retryAt > Date.now(), 'waiting state exposes the retry deadline');
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 950)); });
  assert.equal(requests.length, 2, 'one explicit retry is sent after cooldown');
  await act(async () => {
    requests[1].resolve([{ segment_id: 'cooldown-segment', translation_status: 'failed', retry_after_ms: 1000 }]);
    await new Promise((resolve) => setTimeout(resolve, 20));
    scheduler.retryFailed();
    scheduler.enqueuePlan([batch('cooldown-navigation', 'urgent', 'cooldown-window', 'cooldown-segment')]);
    scheduler.reset();
  });
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 1100)); });
  assert.equal(requests.length, 2, 'navigation cancels the scheduled explicit retry');
  await act(async () => { tree.unmount(); });
  tree = await mountScheduler();
  requests.splice(0); delivered.splice(0);
  await act(async () => {
    const mixed = batch('scope-mixed', 'prefetch', 'scope-window', 'scope-retired');
    mixed.segments.push({ ...mixed.segments[0], segment_id: 'main-retained' });
    scheduler.enqueuePlan([mixed]);
    await Promise.resolve();
    scheduler.enqueuePlan([batch('scope-upgrade', 'urgent', 'upgraded-window', 'scope-retired')]);
    scheduler.forgetSegments(['scope-retired']);
    requests[0].resolve([
      { segment_id: 'scope-retired', translation_status: 'pending', retry_after_ms: 750 },
      { segment_id: 'main-retained', translation_status: 'succeeded', translated_text: 'retained' },
    ]);
    await new Promise(resolve => setTimeout(resolve, 20));
  });
  assert.deepEqual(delivered.map(result => result.segment_id), ['main-retained'], 'mixed in-flight scope teardown retains only current segments');
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 800)); });
  assert.equal(requests.length, 1, 'retired pending segments cannot keep a mixed batch polling');
  await act(async () => {
    scheduler.enqueuePlan([batch('retired-all', 'urgent', 'scope-window', 'only-retired')]);
    await Promise.resolve();
    scheduler.forgetSegments(['only-retired']);
    requests[1].reject(new Error('late retired scope failure'));
    await new Promise(resolve => setTimeout(resolve, 20));
  });
  assert.equal(scheduler.error, null, 'late failures of a completely retired scope cannot poison current page state');
  assert.deepEqual(delivered.map(result => result.segment_id), ['main-retained']);
  // A cache hit carries the original server deadline; replay never extends it.
  const originalNow = Date.now;
  let now = originalNow();
  Date.now = () => now;
  try {
    await act(async () => {
      scheduler.reset();
      requests.splice(0); delivered.splice(0);
      scheduler.enqueuePlan([batch('ttl-first', 'urgent', 'ttl-window', 'ttl-segment')]);
      await Promise.resolve();
      requests[0].resolve([{ ...succeeded('ttl-segment'), cache_expires_at: new Date(now + 1000).toISOString() }]);
      await Promise.resolve();
    });
    now += 900;
    await act(async () => { scheduler.enqueuePlan([batch('ttl-hit', 'urgent', 'ttl-window', 'ttl-segment')]); });
    assert.equal(requests.length, 1, 'unexpired translations replay without another request');
    now += 101;
    await act(async () => { scheduler.enqueuePlan([batch('ttl-expired', 'urgent', 'ttl-window', 'ttl-segment')]); });
    assert.equal(requests.length, 2, 'cache hit must not extend the successful write deadline');
    assert.equal(delivered.length, 2, 'expired result must not be replayed into the document');
    await act(async () => {
      scheduler.reset();
      requests[1].resolve([succeeded('ttl-segment')]);
      await Promise.resolve();
    });
    assert.equal(delivered.length, 2, 'reset rejects a late result from the retired engine generation');
  } finally { Date.now = originalNow; }
  await act(async () => { tree.unmount(); });
} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}
