import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import Module from 'node:module';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-youtube-epoch-'));
const require = createRequire(import.meta.url);
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();

try {
  execFileSync(
    process.execPath,
    [
      join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
      '--ignoreConfig',
      'components/TranslatableWebView.tsx',
      'hooks/useSegmentTranslationQueue.ts',
      'domain/youtubeTranslation.ts',
      'domain/translationConvergence.ts',
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
  const requests = [];
  const injections = [];
  const states = [];
  const timelines = [];
  const runtime = {
    generation: 1,
    identity: { server_id: 'youtube-test', api_base_url: 'https://reader.test' },
  };
  let currentGeneration = 1;
  const originalLoad = Module._load;
  Module._load = function load(request, parent, isMain) {
    if (request === 'react-native-webview') {
      const WebView = React.forwardRef((props, ref) => {
        React.useImperativeHandle(ref, () => ({ injectJavaScript: (script) => { injections.push(script); } }), []);
        return React.createElement('WebView', props);
      });
      return { WebView };
    }
    if (request === '../hooks/useTranslationPreference') {
      return { useTranslationPreference: () => ({ effectiveEngineFingerprint: null, refetch: () => Promise.resolve() }) };
    }
    if (request === '../domain/webTranslation') {
      return { createWebTranslationScript: () => 'web-translation-script' };
    }
    if (request === '../lib/api') {
      return {
        resolveTranslationSegments: async (...args) => {
          const deferred = { args };
          requests.push(deferred);
          return await new Promise((resolve, reject) => {
            deferred.resolve = resolve;
            deferred.reject = reject;
          });
        },
      };
    }
    if (request === '../domain/translationRetry') {
      return {
        translationTransportRetryDecision: (_cause, previousFailureCount) => ({
          delayMs: null,
          failureCount: previousFailureCount + 1,
          retry: false,
        }),
      };
    }
    if (request === '../lib/connection/react') {
      return {
        useReaderRuntime: () => runtime,
        useReaderRuntimeGeneration: () => currentGeneration,
      };
    }
    if (request === '../lib/connection') {
      return {
        captureRuntimeContext: (candidate) => candidate === runtime
          ? { generation: currentGeneration, runtime, serverId: runtime.identity.server_id }
          : null,
        isRuntimeContextCurrent: (context) => context?.runtime === runtime && context.generation === currentGeneration,
      };
    }
    return originalLoad.call(this, request, parent, isMain);
  };

  const { TranslatableWebView } = require(join(outputDirectory, 'components/TranslatableWebView.js'));
  const { i18n } = require(join(outputDirectory, 'i18n/index.js'));
  await i18n.changeLanguage('zh-CN');
  function Harness(overrides = {}) {
    return React.createElement(TranslatableWebView, {
      bridgeKind: 'youtube',
      onCaptionChange: (caption) => { if (caption === null) states.push({ caption: null }); },
      onCaptionTimelineChange: (timeline) => { timelines.push(timeline); },
      onTranslationStateChange: (state) => { states.push(state); },
      originWhitelist: ['https://*'],
      session: { access_token: 'test-token', user: { id: 'user-1' } },
      source: { uri: 'https://www.youtube.com/embed/test' },
      targetLocale: 'zh-CN',
      translationEnabled: true,
      translationMode: 'bilingual',
      translationPurpose: 'caption',
      ...overrides,
    });
  }

  let tree;
  await act(async () => { tree = create(React.createElement(Harness)); });
  const initialInjections = injections.length;
  const webView = () => tree.root.findByType('WebView');
  const bootstrap = webView().props.injectedJavaScript;
  const tokenMarker = '"channelToken":"';
  const tokenStart = bootstrap.indexOf(tokenMarker);
  assert.ok(tokenStart >= 0, 'production bootstrap must include a channel token');
  const channelToken = bootstrap.slice(tokenStart + tokenMarker.length).split('"', 1)[0];
  const navigationId = 'youtube-navigation-test';
  const message = async (payload) => {
    await act(async () => {
      webView().props.onMessage({ nativeEvent: { data: JSON.stringify({
        channel_token: channelToken,
        navigation_id: navigationId,
        ...payload,
      }) } });
      await Promise.resolve();
    });
  };
  const flushQueue = async () => {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
  };
  const result = (epoch, segmentId, text = `translated-${epoch}`) => ({
    error_code: null,
    error_retryable: null,
    engine_id: 'test-engine',
    engine_label: 'test',
    purpose: 'caption',
    retry_after_ms: null,
    segment_id: segmentId,
    translated_text: text,
    translation_locale: 'zh-CN',
    translation_status: 'succeeded',
  });

  await message({ count: 1, media_epoch: 1, state: 'ready', type: 'reader_translation_status' });
  await message({
    media_epoch: 1,
    batches: [{ id: 'a', priority: 'urgent', window_id: '1:0', segments: [{ segment_id: `${navigationId}:a`, text: 'caption A' }] }],
    type: 'reader_translation_batch_plan',
  });
  await flushQueue();
  assert.equal(requests.length, 1, 'epoch A must create one queue request');

  await message({ captions: [], current_segment_id: null, media_epoch: 2, type: 'reader_translation_caption_timeline' });
  await flushQueue();
  assert.ok(timelines.some((timeline) => timeline.captions.length === 0), 'new epoch clears caption timeline first');
  assert.ok(states.some((state) => state && state.activeCount === 0), 'new epoch resets activeCount');
  requests[0].resolve([result(1, `${navigationId}:a`, 'late A success')]);
  await flushQueue();
  assert.equal(injections.length, initialInjections, 'late epoch A success must not be injected after epoch B reset');

  await message({
    media_epoch: 2,
    batches: [{ id: 'b', priority: 'urgent', window_id: '2:0', segments: [{ segment_id: `${navigationId}:b`, text: 'caption B' }] }],
    type: 'reader_translation_batch_plan',
  });
  await flushQueue();
  assert.equal(requests.length, 2, 'epoch B must create a fresh queue request');

  // A -> B -> A: epoch is monotonic even when the video identity returns.
  await message({ count: 1, media_epoch: 1, state: 'stale', type: 'reader_translation_status' });
  await message({ count: 1, media_epoch: 3, state: 'ready', type: 'reader_translation_status' });
  await flushQueue();
  requests[1].reject(new Error('late epoch B error'));
  await flushQueue();
  assert.equal(injections.length, initialInjections, 'late epoch B error/finally must be a no-op');

  await message({
    media_epoch: 3,
    batches: [{ id: 'c', priority: 'urgent', window_id: '3:0', segments: [{ segment_id: `${navigationId}:c`, text: 'caption C' }] }],
    type: 'reader_translation_batch_plan',
  });
  await flushQueue();
  assert.equal(requests.length, 3, 'returned media identity receives a fresh epoch queue');
  requests[2].resolve([result(3, `${navigationId}:c`)]);
  await flushQueue();
  assert.equal(injections.length, initialInjections + 1, 'current epoch result is the only result injected');

  // Translating the transcript must schedule captions while keeping the player overlay off.
  await act(async () => { tree.update(React.createElement(Harness, {
    translationMode: 'original', captionTimelineMode: 'bilingual',
  })); });
  assert.ok(injections.some(script => script.includes('setMode("original")') && script.includes('setTimelineMode?.("bilingual")')));
  await message({
    media_epoch: 3,
    batches: [{ id: 'timeline', priority: 'urgent', window_id: '3:1', segments: [{ segment_id: `${navigationId}:timeline`, text: 'timeline only' }] }],
    type: 'reader_translation_batch_plan',
  });
  await flushQueue();
  assert.equal(requests.length, 4, 'timeline-only mode still requests caption translations');
  requests[3].resolve([result(3, `${navigationId}:timeline`, '字幕轴译文')]);
  await flushQueue();
  assert.ok(injections.some(script => script.includes('字幕轴译文')));
  await act(async () => { tree.unmount(); });

  // Every same-position click is a new command, never an alternating null.
  await act(async () => { tree = create(React.createElement(Harness, { seekCommand: { positionMs: 4000, sequence: 1 } })); });
  const seeksBefore = injections.filter(script => script.includes('?.seekTo(4000)')).length;
  for (let sequence = 2; sequence <= 4; sequence += 1) {
    await act(async () => tree.update(React.createElement(Harness, { seekCommand: { positionMs: 4000, sequence } })));
  }
  assert.equal(injections.filter(script => script.includes('?.seekTo(4000)')).length, seeksBefore + 3);
  await act(async () => tree.unmount());

  // The full YouTube page owns independent page and caption channels and namespaces.
  const requestedModes = [];
  await act(async () => { tree = create(React.createElement(Harness, {
    bridgeKind: 'web', youtubePage: true, translationMode: 'original',
    playerTranslationMode: 'bilingual', translationPurpose: 'web_segment',
    onPlayerTranslationModeRequest: mode => requestedModes.push(mode),
  })); });
  const combined = webView().props.injectedJavaScript;
  assert.ok(combined.includes('web-translation-script'));
  assert.ok(combined.includes('__readerCaptionBridge'), 'caption runtime cannot overwrite the webpage runtime');
  const secondaryToken = combined.slice(combined.indexOf(tokenMarker) + tokenMarker.length).split('"', 1)[0];
  await message({ channel_token: secondaryToken, media_epoch: 0, mode: 'original', type: 'reader_translation_mode_request' });
  assert.deepEqual(requestedModes, ['original'], 'in-player control reaches its own mode callback in web mode');
  await message({
    channel_token: secondaryToken, media_epoch: 0,
    batches: [{ id: 'web-player', priority: 'urgent', window_id: '0:0', segments: [{ segment_id: `${navigationId}:web-player`, text: 'player on webpage' }] }],
    type: 'reader_translation_batch_plan',
  });
  await flushQueue();
  assert.equal(requests.length, 5, 'player captions translate even with webpage translation off');
  requests[4].resolve([result(0, `${navigationId}:web-player`, '网页播放器字幕')]);
  await flushQueue();
  assert.ok(injections.some(script => script.includes('__readerCaptionBridge?.applyTranslations') && script.includes('网页播放器字幕')));
  await act(async () => { tree.unmount(); });

} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}

console.log('Mounted YouTube media-epoch A→B→A behavior passed.');
