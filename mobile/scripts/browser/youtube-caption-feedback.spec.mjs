import { expect, test } from '@playwright/test';
import { build } from 'esbuild';
import { readFileSync } from 'node:fs';
import Module, { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(import.meta.url);
const React = require('react');
const { act, create } = require('react-test-renderer');
const generated = readFileSync(resolve(root, 'domain/translationRuntimeSources.generated.ts'), 'utf8');
const runtime = JSON.parse(generated.match(/export const YOUTUBE_TRANSLATION_BOOTSTRAP_SOURCE = (".*");\n/)[1]);
const bundled = (await build({
  entryPoints: [resolve(root, 'hooks/useRealtimeTranslationScheduler.ts')],
  bundle: true, platform: 'node', format: 'cjs', write: false, external: ['react'],
  plugins: [{ name: 'native-adapters', setup(build) {
    build.onResolve({ filter: /lib\/(api|connection(?:\/react)?)$|domain\/translationDiagnostics$/ }, args => ({ path: args.path, namespace: 'adapter' }));
    build.onLoad({ filter: /.*/, namespace: 'adapter' }, ({ path }) => ({ contents:
      path.endsWith('/api') ? 'export const resolveTranslationSegments = (...args) => globalThis.__captionTestResolve(...args);'
        : path.endsWith('/react') ? 'const runtime = {}; export const useReaderRuntime = () => runtime; export const useReaderRuntimeGeneration = () => 1;'
          : path.endsWith('/connection') ? 'export const captureRuntimeContext = runtime => ({runtime}); export const isRuntimeContextCurrent = () => true;'
            : 'export const recordTranslationDiagnostic = () => {};', loader: 'js' }));
  } }],
})).outputFiles[0].text;
const compiled = new Module(resolve(root, 'hooks/__caption_feedback_test.cjs'));
compiled.filename = resolve(root, 'hooks/__caption_feedback_test.cjs');
compiled.paths = Module._nodeModulePaths(root);
compiled._compile(bundled, compiled.filename);
const { useRealtimeTranslationScheduler } = compiled.exports;

async function fixture(page, count) {
  await page.route('https://www.youtube.com/watch?v=testvideo01', route => route.fulfill({ contentType: 'text/html', body: '<div class="html5-video-player" style="position:relative;width:360px;height:200px"><video></video><div class="ytp-right-controls"><button class="ytp-subtitles-button" aria-pressed="true">CC</button></div></div>' }));
  await page.route('**/api/timedtext*', route => route.fulfill({ contentType: 'application/json', body: JSON.stringify({ events: Array.from({ length: count }, (_, index) => ({ tStartMs: index * 4000, dDurationMs: 3800, segs: [{ utf8: `Sentence ${index} about the ocean.` }] })) }) }));
  await page.goto('https://www.youtube.com/watch?v=testvideo01');
}

test('real caption runtime and native scheduler settle success/failure replay and recover reset', async ({ page }) => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  let calls = 0;
  let scheduler;
  let tree;
  let deliveries = 0;
  const messages = [];
  const results = [];
  globalThis.__captionTestResolve = async (_session, segments) => {
    calls += 1;
    return segments.map((segment, index) => ({ segment_id: segment.segment_id, purpose: 'caption', translation_status: index === 0 ? 'failed' : 'succeeded', translated_text: index === 0 ? null : '字幕译文', retry_after_ms: null }));
  };
  const session = { user: { id: 'caption-test' }, access_token: 'test' };
  function Harness() {
    scheduler = useRealtimeTranslationScheduler({ enabled: true, session, targetLocale: 'zh-CN', onResults: value => results.push(value) });
    return null;
  }
  await act(async () => { tree = create(React.createElement(Harness)); });
  try {
    await page.exposeFunction('nativeCaptionMessage', value => { messages.push(JSON.parse(value)); });
    await fixture(page, 10);
    await page.evaluate(source => {
      window.ReactNativeWebView = { postMessage: value => window.nativeCaptionMessage(value) };
      (0, eval)(`(${source})`)({ channelToken: 'caption-test', initialMode: 'bilingual' });
      fetch('/api/timedtext?v=testvideo01&lang=en');
    }, runtime);
    await expect.poll(() => messages.some(m => m.type === 'reader_translation_batch_plan')).toBe(true);
    const drain = async () => {
      for (let round = 0; round < 12; round += 1) {
        const plans = messages.splice(0).filter(m => m.type === 'reader_translation_batch_plan');
        for (const message of plans) {
          deliveries += 1;
          expect(deliveries, 'feedback must stay bounded').toBeLessThan(20);
          await act(async () => {
            const batches = message.batches.map(batch => ({ ...batch, windowId: batch.window_id, segments: batch.segments.map(segment => ({ ...segment, purpose: 'caption' })) }));
            scheduler.replaceWindow(batches[0].windowId);
            scheduler.enqueuePlan(batches);
          });
        }
        const ready = results.splice(0);
        for (const value of ready) await page.evaluate(value => window.__readerTranslationBridge.applyTranslations(value, 1), value);
        await act(async () => { await new Promise(resolve => setTimeout(resolve, 30)); });
      }
      expect(messages.filter(m => m.type === 'reader_translation_batch_plan')).toHaveLength(0);
      expect(results).toHaveLength(0);
    };
    await drain();
    const settledCalls = calls;
    const settledDeliveries = deliveries;
    expect(settledCalls).toBeGreaterThan(0);
    await drain();
    expect(calls).toBe(settledCalls);
    expect(deliveries).toBe(settledDeliveries);
    // Clearing runtime translations must still request the terminal native cache.
    await page.evaluate(() => window.__readerTranslationBridge.resetTranslations());
    await drain();
    expect(deliveries).toBeGreaterThan(settledDeliveries);
    expect(calls).toBe(settledCalls);
    await act(async () => scheduler.retryFailed());
    await page.evaluate(() => window.__readerTranslationBridge.retryFailed());
    await drain();
    expect(calls).toBeGreaterThan(settledCalls);
    await expect(page.locator('.reader-bilingual-caption-source')).toContainText('Sentence');
  } finally {
    await act(async () => tree.unmount());
    delete globalThis.__captionTestResolve;
  }
});

test('bounded timeline pages reach every late cue, current playback and reject stale page commands', async ({ page }) => {
  await fixture(page, 500);
  await page.evaluate(source => {
    window.captionMessages = [];
    window.ReactNativeWebView = { postMessage: value => window.captionMessages.push(JSON.parse(value)) };
    (0, eval)(`(${source})`)({ channelToken: 'caption-test', initialMode: 'original' });
    fetch('/api/timedtext?v=testvideo01&lang=en');
  }, runtime);
  await expect.poll(() => page.evaluate(() => window.captionMessages.filter(m => m.type === 'reader_translation_caption_timeline').at(-1)?.captions.length ?? 0)).toBeGreaterThan(0);
  const summary = await page.evaluate(() => {
    const latest = () => window.captionMessages.filter(m => m.type === 'reader_translation_caption_timeline').at(-1);
    const initial = latest();
    // Long translations exercise the character cap independently of cue count.
    window.__readerTranslationBridge.applyTranslations(initial.captions.slice(0, 20).map(c => ({ segment_id: c.segment_id, translated_text: '译'.repeat(3000), translation_status: 'succeeded' })), initial.media_epoch);
    const first = latest();
    const seen = new Set();
    const pages = [];
    for (let i = 0; i < 50; i += 1) {
      const page = latest();
      pages.push({ count: page.captions.length, chars: page.captions.reduce((n, c) => n + c.source_text.length + (c.translated_text?.length ?? 0), 0) });
      page.captions.forEach(c => seen.add(c.source_text));
      if (!page.has_next) break;
      window.__readerTranslationBridge.setTimelineWindow('next', page.captions[0].segment_id, page.media_epoch);
    }
    const last = latest();
    window.__readerTranslationBridge.setTimelineWindow('previous', last.captions[0].segment_id, last.media_epoch - 1);
    const staleIgnored = latest() === last;
    window.__readerTranslationBridge.setTimelineWindow('previous', last.captions[0].segment_id, last.media_epoch);
    const previous = latest();
    window.__readerTranslationBridge.seekTo(185 * 4000);
    const current = latest();
    return { seen: seen.size, pages, firstCount: first.captions.length, staleIgnored, previousChanged: previous !== last, currentIncluded: current.captions.some(c => c.segment_id === current.current_segment_id), currentText: current.captions.find(c => c.segment_id === current.current_segment_id)?.source_text };
  });
  expect(summary.seen).toBe(500);
  expect(summary.firstCount).toBeLessThan(180);
  expect(summary.pages.every(p => p.count <= 180 && p.chars <= 40000)).toBe(true);
  expect(summary.staleIgnored).toBe(true);
  expect(summary.previousChanged).toBe(true);
  expect(summary.currentIncluded).toBe(true);
  expect(summary.currentText).toContain('Sentence 185');
});
