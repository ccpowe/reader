import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import Module from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-web-diagnostics-'));
const require = createRequire(import.meta.url);
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();

try {
  execFileSync(process.execPath, [
    join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
    '--ignoreConfig', 'components/TranslatableWebView.tsx',
    '--target', 'es2022', '--module', 'commonjs', '--jsx', 'react-jsx',
    '--outDir', outputDirectory, '--lib', 'dom,es2022', '--types', 'node', '--skipLibCheck',
  ], { cwd: mobileRoot, stdio: 'inherit' });

  const React = require('react');
  const { act, create } = require('react-test-renderer');
  const { getYouTubeInterfaceMessages } = require(join(outputDirectory, 'domain/youtubeTranslation.js'));
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const originalLoad = Module._load;
  const debugCalls = [];
  const injections = [];
  const cacheClears = [];
  const platform = { OS: 'ios' };
  const plans = [];
  const forgotten = [];
  const originalDebug = console.debug;
  console.debug = (...args) => { if (args[0] === '[web-translation-engine]') debugCalls.push(args); };
  const scheduler = { activeCount: 0, enqueuePlan: plan => plans.push(plan), forgetSegments: ids => forgotten.push(ids), error: null, replaceWindow: () => undefined, reset: () => undefined };
  Module._load = function load(request, parent, isMain) {
    if (request === 'react-native') return { Platform: platform };
    if (request === 'react-native-webview') {
      const WebView = React.forwardRef((props, ref) => {
        React.useImperativeHandle(ref, () => ({ clearCache: disk => cacheClears.push(disk), injectJavaScript: (script) => injections.push(script) }), []);
        return React.createElement('WebView', props);
      });
      return { WebView };
    }
    if (request === '../domain/webTranslation') return {
      createWebTranslationScript: (config) => `bootstrap:${JSON.stringify(config)}`,
    };
    if (request === '../domain/youtubeTranslation') return {
      createYouTubeTranslationScript: () => 'youtube',
      getYouTubeInterfaceMessages,
    };
    if (request === '../hooks/useTranslationPreference') return {
      useTranslationPreference: () => ({ effectiveEngineFingerprint: 'diagnostics-engine', refetch: () => Promise.resolve() }),
    };
    if (request === '../hooks/useRealtimeTranslationScheduler') return {
      useRealtimeTranslationScheduler: () => scheduler,
    };
    return originalLoad.call(this, request, parent, isMain);
  };

  const { TranslatableWebView } = require(join(outputDirectory, 'components/TranslatableWebView.js'));
  let tree;
  let translationState;
  await act(async () => {
    tree = create(React.createElement(TranslatableWebView, {
      bridgeKind: 'web', originWhitelist: ['https://*'],
      session: { access_token: 'token', user: { id: 'diagnostics-user' } }, source: { uri: 'https://example.test/article' },
      targetLocale: 'zh-CN', translationEnabled: true,
      translationMode: 'bilingual', translationPurpose: 'web_segment',
      onTranslationStateChange: (state) => { translationState = state; },
    }));
  });
  const webView = tree.root.findByType('WebView');
  assert.notEqual(webView.props.cacheEnabled, false, 'iOS must retain its persistent login store');
  await act(async () => webView.props.onLoadEnd({ nativeEvent: { url: 'https://example.test/article' } }));
  assert.deepEqual(cacheClears, [], 'iOS clearCache(true) would delete login-related site databases');
  platform.OS = 'android';
  await act(async () => tree.update(React.createElement(TranslatableWebView, {
    ...tree.root.findByType(TranslatableWebView).props, translationEnabled: false,
  })));
  assert.equal(webView.props.cacheEnabled, false);
  assert.equal(webView.props.cacheMode, 'LOAD_NO_CACHE');
  await act(async () => webView.props.onLoadEnd({ nativeEvent: { url: 'https://example.test/article' } }));
  await act(async () => webView.props.onLoadEnd({ nativeEvent: { url: 'https://example.test/next' } }));
  assert.deepEqual(cacheClears, [true], 'old Android resource caches are cleared once per process');
  assert.ok(injections.filter(s => s.includes('function webStorageBootstrap')).length >= 3, 'each navigation installs cleanup even with translation disabled');
  await act(async () => tree.update(React.createElement(TranslatableWebView, {
    ...tree.root.findByType(TranslatableWebView).props, translationEnabled: true,
  })));
  const bootstrap = webView.props.injectedJavaScript;
  const channelToken = bootstrap.match(/"channelToken":"([^"]+)"/)[1];
  const send = async (payload) => act(async () => webView.props.onMessage({
    nativeEvent: { data: JSON.stringify({
      channel_token: channelToken,
      document_epoch: 'document-epoch-test',
      document_url: 'https://example.test/article',
      navigation_id: 'navigation-test',
      ...payload,
    }) },
  }));
  await act(async () => webView.props.onLoadStart({ nativeEvent: { url: 'https://example.test/article' } }));
  await send({ profile: 'generic', type: 'reader_translation_navigation_reset' });
  const challenge = injections.filter((script) => script.includes('reconnect?.(')).at(-1).match(/reconnect\?\.\(("[^"]+")\)/)[1];
  await send({ type: 'reader_translation_handshake', challenge_nonce: JSON.parse(challenge) });
  await send({ type: 'reader_translation_handshake_ready', challenge_nonce: JSON.parse(challenge) });
  const diagnostics = {
    deferred_roots: 2, dirty_roots: 0, paragraphs_detected: 3,
    paragraphs_filtered: 1, paragraphs_queued: 2, pending_roots: 0,
    processing_roots: 1, profile_id: 'generic', rect_reads: 4,
    render_failures: {}, scope_id: 'main', style_reads: 5,
    traversal_ms: 6, tree_nodes: 7, type: 'reader_translation_engine_diagnostics',
  };
  await send(diagnostics);
  assert.equal(debugCalls.length, 1, 'declared deferred/processing diagnostic fields must pass the strict native boundary');
  await send({ ...diagnostics, deferred_roots: '2' });
  assert.equal(debugCalls.length, 1, 'non-numeric deferred roots must fail closed');
  const selectionReplies = [];
  await act(async () => translationState.inspectSelection((message) => selectionReplies.push(message)));
  const selectionScript = injections.filter((script) => script.includes('diagnoseSelection?.(')).at(-1);
  const selectionNonce = JSON.parse(selectionScript.match(/diagnoseSelection\?\.\(("[^"]+")\)/)[1]);
  const selectionEvent = { type: 'reader_translation_selection_diagnostic', challenge_nonce: selectionNonce,
    reason: 'excluded', rule_index: 0, tag_name: 'NAV', profile_id: 'generic', scope_id: 'main' };
  await send({ ...selectionEvent, challenge_nonce: 'unsolicited' });
  await send({ ...selectionEvent, reason: 'private-page-text' });
  await send({ ...selectionEvent, document_epoch: 'obsolete-epoch', display_status: 'clipped' });
  assert.equal(selectionReplies.length, 0, 'unsolicited or unknown selection diagnostics cannot surface UI');
  await send({ ...selectionEvent, display_status: 'private-style-value' });
  assert.equal(selectionReplies.length, 0, 'unknown display status must fail closed');
  await send({ ...selectionEvent, display_status: 'clipped' });
  assert.equal(selectionReplies.length, 1);
  assert.match(selectionReplies[0], /容器裁剪/);
  assert.match(selectionReplies[0], /exclude\[0\]/);
  await send(selectionEvent);
  assert.equal(selectionReplies.length, 1, 'one user inspection may deliver only one result');
  // Teardown must not depend on the per-segment diagnostic rate budget.
  const scopePrefix = 'navigation-test:document-epoch-test:scope-1:';
  const mainId = 'navigation-test:document-epoch-test:main:keep';
  const neighborId = 'navigation-test:document-epoch-test:scope-10:keep';
  const plan = segments => ({ type: 'reader_translation_batch_plan', batches: [{ id: `batch-${plans.length}`, window_id: 'scope-window', priority: 'urgent', segments }] });
  await send(plan([{ segment_id: mainId, text: 'Main scope must survive.' }, { segment_id: neighborId, text: 'Scope ten must survive.' }]));
  const scopeIds = Array.from({ length: 149 }, (_, i) => `${scopePrefix}${i}`);
  for (let i = 0; i < scopeIds.length; i += 5) await send(plan(scopeIds.slice(i, i + 5).map(segment_id => ({ segment_id, text: 'x'.repeat(5000) }))));
  const originalNow = Date.now;
  let fakeNow = originalNow();
  Date.now = () => fakeNow;
  try {
    for (const segment_id of [mainId, neighborId, ...scopeIds]) {
      fakeNow += 1001;
      await send({ type: 'reader_translation_segment_event', segment_id, state: 'failed', reason: 'provider_failure' });
    }
    assert.equal(translationState.failedCount, 151);
    fakeNow += 1001;
    for (const segment_id of scopeIds) await send({ type: 'reader_translation_segment_event', segment_id, state: 'disposed', reason: 'source_removed' });
    assert.equal(translationState.failedCount, 2, 'diagnostic rate limits must not drop accepted disposal state');
    await send({ type: 'reader_translation_segment_event', segment_id: scopeIds[0], state: 'failed', reason: 'provider_failure' });
    assert.equal(translationState.failedCount, 3, 'accepted failure state also survives diagnostic saturation');
    for (const scope_id of ['main', 'scope-', 'scope-1:main', 'scope-999']) await send({ type: 'reader_translation_scope_disposed', scope_id });
    await send({ type: 'reader_translation_scope_disposed', scope_id: 'scope-1', document_epoch: 'obsolete-epoch' });
    assert.equal(translationState.failedCount, 3, 'malformed, unknown, main and obsolete scopes cannot clear accepted work');
    const beforeLimit = plans.length;
    await send(plan([{ segment_id: 'navigation-test:document-epoch-test:scope-2:large', text: 'y'.repeat(8000) }]));
    assert.equal(plans.length, beforeLimit, 'the prior scope owns the character budget until teardown');
    await send({ type: 'reader_translation_scope_disposed', scope_id: 'scope-1' });
    assert.equal(translationState.failedCount, 2, 'aggregate teardown removes all child failures while preserving main');
    assert.deepEqual(forgotten.at(-1), scopeIds);
    await send({ type: 'reader_translation_scope_disposed', scope_id: 'scope-1' });
    assert.equal(forgotten.length, 1, 'duplicate aggregation cannot refund characters or forget work twice');
    await send(plan([{ segment_id: 'navigation-test:document-epoch-test:scope-2:large', text: 'y'.repeat(8000) }]));
    assert.equal(plans.length, beforeLimit + 1, 'scope teardown reclaims accepted source character capacity');
    fakeNow += 1001;
    await send({ type: 'reader_translation_segment_event', segment_id: scopeIds[0], state: 'failed', reason: 'provider_failure' });
    assert.equal(translationState.failedCount, 2, 'late events for cleared accepted source cannot resurrect failures');
  } finally { Date.now = originalNow; }
  await act(async () => { tree.unmount(); });
  console.debug = originalDebug;
} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}
