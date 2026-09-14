import { expect, test } from '@playwright/test';
import { buildSync, transformSync } from 'esbuild';
import { createRequire } from 'node:module';
import Module from 'node:module';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// Mount the production Native component against a real Chromium document. Only
// WebView transport is replaced; lifecycle cases stub provider scheduling.
// The host-expansion case uses the production scheduler with a provider stub.
// Neither endpoint's identity handling nor DOM engine is simulated.
const mobileRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const require = createRequire(import.meta.url);
const React = require('react');
const { act, create } = require('react-test-renderer');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();
const temp = mkdtempSync(join(tmpdir(), 'reader-navigation-'));
const runtime = buildSync({ entryPoints: [join(mobileRoot, 'domain/webTranslationRuntime.ts')], bundle: true,
  format: 'iife', globalName: 'ReaderRuntime', platform: 'browser', write: false }).outputFiles[0].text;
const rulesCode = buildSync({ entryPoints: [join(mobileRoot, 'domain/webTranslationRules.ts')], bundle: true,
  format: 'cjs', platform: 'node', write: false }).outputFiles[0].text;
writeFileSync(join(temp, 'rules.cjs'), rulesCode);
const rules = require(join(temp, 'rules.cjs')).WEB_TRANSLATION_RULE_REGISTRY;
writeFileSync(join(temp, 'component.cjs'), transformSync(readFileSync(join(mobileRoot,
  'components/TranslatableWebView.tsx'), 'utf8'), { loader: 'tsx', format: 'cjs', jsx: 'automatic' }).code);
writeFileSync(join(temp, 'screen.cjs'), transformSync(readFileSync(join(mobileRoot,
  'screens/ArticleReaderScreen.tsx'), 'utf8'), { loader: 'tsx', format: 'cjs', jsx: 'automatic' }).code);
writeFileSync(join(temp, 'web-reader.cjs'), transformSync(readFileSync(join(mobileRoot,
  'domain/webReader.ts'), 'utf8'), { loader: 'ts', format: 'cjs' }).code);
writeFileSync(join(temp, 'request-errors.cjs'), transformSync(readFileSync(join(mobileRoot,
  'domain/translationRequestErrors.ts'), 'utf8'), { loader: 'ts', format: 'cjs' }).code);
writeFileSync(join(temp, 'i18n.cjs'), buildSync({ stdin: {
  contents: `export * from './i18n/index';
    export * from './i18n/message';
    export { createReaderInterfaceScript, buildExtractedReaderHtml } from './domain/readerHtml';
    export { getYouTubeInterfaceMessages } from './domain/youtubeTranslation';`,
  resolveDir: mobileRoot, loader: 'ts',
},
  bundle: true, packages: 'external', format: 'cjs', platform: 'node', write: false }).outputFiles[0].text);
const localization = require(join(temp, 'i18n.cjs'));
await localization.i18n.changeLanguage('zh-CN');
writeFileSync(join(temp, 'realtime-errors.cjs'), transformSync(readFileSync(join(mobileRoot,
  'domain/realtimeTranslationErrors.ts'), 'utf8'), { loader: 'ts', format: 'cjs' }).code);
writeFileSync(join(temp, 'scheduler.cjs'), transformSync(readFileSync(join(mobileRoot,
  'hooks/useRealtimeTranslationScheduler.ts'), 'utf8'), { loader: 'ts', format: 'cjs' }).code);
writeFileSync(join(temp, 'diagnostics.cjs'), transformSync(readFileSync(join(mobileRoot,
  'domain/translationDiagnostics.ts'), 'utf8'), { loader: 'ts', format: 'cjs' }).code);
const diagnostics = require(join(temp, 'diagnostics.cjs'));
const connectionRuntime = { identity: { server_id: 'browser-scheduler' } };
const emptyTitles = [];
const host = name => ({ children, ...props }) => React.createElement(name, props, children);
let current;
const originalLoad = Module._load;
Module._load = function(request, parent, isMain) {
  if (request === '../i18n' || request === '../i18n/message') return localization;
  if (request === '../domain/readerHtml') return localization;
  if (request === '../domain/webReader') return require(join(temp, 'web-reader.cjs'));
  if (request === '../domain/translationRequestErrors' || request === './translationRequestErrors') return require(join(temp, 'request-errors.cjs'));
  if (request === '../domain/translationDiagnostics') return diagnostics;
  if (request === '../lib/connection/react') return {
    useReaderRuntime: () => connectionRuntime, useReaderRuntimeGeneration: () => 1,
  };
  if (request === '../lib/connection') return {
    captureRuntimeContext: () => ({ runtime: connectionRuntime }), isRuntimeContextCurrent: () => true,
  };
  if (request === '../domain/realtimeTranslationErrors') return require(join(temp, 'realtime-errors.cjs'));
  if (request === 'react-native') return { View: host('View'), Text: host('Text'), Pressable: host('Pressable'),
    FlatList: host('FlatList'), ActivityIndicator: host('ActivityIndicator'), StyleSheet: { create: value => value },
    Alert: { alert: () => {} }, Linking: { openURL: async value => current.externalOpens.push(value) },
    Share: { share: async value => current.shares.push(value) } };
  if (request === '@expo/vector-icons') return { MaterialCommunityIcons: host('Icon') };
  if (request === '@tanstack/react-query') return { useQueryClient: () => connectionRuntime };
  if (request === '../components/DetailHeader') return { DetailHeader: host('DetailHeader') };
  if (request === '../components/FloatingReaderTools') return { FloatingReaderTools: host('FloatingReaderTools') };
  if (request === '../components/TitleTranslationNotice') return { TitleTranslationNotice: () => null };
  if (request === '../components/TranslatableWebView') return { TranslatableWebView };
  if (request === '../hooks/useTitleTranslationConvergence') return { useTitleTranslationConvergence: () => emptyTitles };
  if (request === '../domain/media') return { youtubeVideoId: () => null };
  if (request === '../state/savedMutation') return { beginSavedMutation: () => () => {} };
  if (request === '../state/invalidation') return { invalidateAfterSavedMutation: async () => {} };
  if (request === '../ui/tokens') return { colors: {}, radii: {}, touchTarget: 44 };
  if (request === '../lib/api') return { displayTitle: item => item.title,
    getArticle: () => { throw new Error('Web articles must not request stored bodies'); },
    setSavedContent: async (_session, id, saved) => current.saves.push({ id, saved }),
    resolveTranslationSegments: async (_session, segments) => {
    current.requests.push(segments);
    if (current.providerResults) return current.providerResults(segments);
    return segments.map((segment) => ({ segment_id: segment.segment_id,
      translated_text: `已翻译：${segment.text}`, translation_status: 'succeeded' }));
  } };
  if (request === 'react-native-webview') return { WebView: React.forwardRef((props, ref) => {
    React.useImperativeHandle(ref, () => ({ injectJavaScript: (script) => current.scriptTargets
      ? current.scriptTargets.push({ reader: Boolean(props.source?.html), script }) : current.scripts.push(script) }), []);
    return React.createElement('WebView', props);
  }) };
  if (request === '../domain/webTranslation') return { createWebTranslationScript: (config) =>
    `${runtime};ReaderRuntime.webTranslationBootstrap(${JSON.stringify({ ...config, rules })});true;` };
  if (request === '../domain/youtubeTranslation') return {
    createYouTubeTranslationScript: () => '',
    getYouTubeInterfaceMessages: localization.getYouTubeInterfaceMessages,
  };
  if (request === '../hooks/useTranslationPreference') return {
    useTranslationPreference: () => ({ enabled: true, targetLocale: 'zh-CN', effectiveEngineFingerprint: 'navigation-engine', refetch: () => Promise.resolve() }),
  };
  if (request === '../hooks/useRealtimeTranslationScheduler') return { useRealtimeTranslationScheduler: (options) => {
    current.results = options.onResults;
    return current.realScheduler ? useRealScheduler(options) : current.scheduler;
  } };
  return originalLoad.call(this, request, parent, isMain);
};
const { useRealtimeTranslationScheduler: useRealScheduler } = require(join(temp, 'scheduler.cjs'));
const { TranslatableWebView } = require(join(temp, 'component.cjs'));
const { ArticleReaderScreen } = require(join(temp, 'screen.cjs'));
Module._load = originalLoad;

const url = 'https://navigation.test/article';
const html = '<html><body><main><p>A complete paragraph about reliable translation on dynamic websites.</p><p>Another useful paragraph that should survive a history update.</p></main></body></html>';
async function harness(page, { drop = false, bootstrap = true, dropConfirm = false, dropReady = false, realScheduler = false } = {}) {
  const h = current = { scripts: [], inbox: [], messages: [], plans: [], states: [], requests: [], resets: 0, drop, dropConfirm, dropReady, realScheduler };
  h.scheduler = { activeCount: 0, error: null, enqueuePlan: (plan) => h.plans.push(...plan),
    retryFailed: () => {}, rejectResult: () => {}, replaceWindow: () => {}, reset: () => { h.resets++; } };
  await page.route('https://navigation.test/**', (route) => route.fulfill({ contentType: 'text/html', body: html }));
  await page.exposeFunction('nativePost', (raw) => {
    const message = JSON.parse(raw);
    h.messages.push(message);
    if (h.dropReady && message.type === 'reader_translation_handshake_ready') { h.dropReady = false; return; }
    if (!h.drop) h.inbox.push(message);
  });
  await page.addInitScript(() => { window.ReactNativeWebView = { postMessage: (raw) => window.nativePost(raw) }; });
  await page.goto(url);
  h.props = { bridgeKind: 'web', session: { access_token: 'test', user: { id: 'navigation-user' } }, source: { uri: url },
    targetLocale: 'zh-CN', translationEnabled: true, translationMode: 'bilingual', translationPurpose: 'web_segment',
    onTranslationStateChange: (state) => h.states.push(state) };
  await act(async () => { h.tree = create(React.createElement(TranslatableWebView, h.props)); });
  h.view = () => h.tree.root.findByType('WebView');
  h.send = async (message) => act(async () => h.view().props.onMessage({ nativeEvent: { data: JSON.stringify(message) } }));
  h.loadStart = async (nextUrl = url) => act(async () => h.view().props.onLoadStart({ nativeEvent: { url: nextUrl } }));
  h.loadEnd = async () => act(async () => h.view().props.onLoadEnd({ nativeEvent: { url: page.url() } }));
  h.flush = async () => {
    // Native injections execute in order, while page-to-Native messages can lag
    // behind load events. Tests can deliberately hold either queue.
    for (let rounds = 0; rounds < 15 && (h.scripts.length || h.inbox.length); rounds++) {
      for (const script of h.scripts.splice(0)) {
        if (h.dropConfirm && script.includes('?.confirmEpoch(')) { h.dropConfirm = false; continue; }
        await page.evaluate(script);
      }
      for (const message of h.inbox.splice(0)) await h.send(message);
    }
  };
  h.until = async (predicate) => expect.poll(async () => { await h.flush(); return predicate(); }, { timeout: 10_000 }).toBeTruthy();
  h.start = async () => { await h.loadStart(); await page.evaluate(h.view().props.injectedJavaScript); await h.loadEnd(); };
  if (bootstrap) await h.start();
  return h;
}
test.afterEach(async () => { if (current?.tree) await act(async () => current.tree.unmount()); });
test.afterAll(() => rmSync(temp, { recursive: true, force: true }));

test('host expansion restores completed translations through the production scheduler without new provider requests', async ({ page }) => {
  const h = await harness(page, { realScheduler: true });
  await h.until(() => h.requests.length > 0);
  await h.flush();
  await expect(page.locator('.reader-translation-node')).toHaveCount(2);
  const requests = h.requests.length;
  await page.locator('main').evaluate((main) => main.classList.add('expanded'));
  await page.waitForTimeout(400);
  await expect.poll(async () => {
    await h.flush();
    return page.locator('.reader-translation-node').count();
  }).toBe(2);
  expect(h.requests.length).toBe(requests);
});

async function translateFirst(page, h) {
  await h.until(() => h.plans.length > 0);
  const segment = h.plans[0].segments[0];
  await act(async () => h.results([{ segment_id: segment.segment_id, translated_text: '可靠的网页翻译。', translation_status: 'succeeded' }]));
  await h.flush();
  await expect(page.locator('.reader-translation-node')).toHaveCount(1);
}

test('same-URL Android history load-start preserves translated DOM and in-flight results', async ({ page }) => {
  const h = await harness(page);
  await translateFirst(page, h);
  const resets = h.resets;
  const epoch = h.messages.find((m) => m.type === 'reader_translation_navigation_reset').document_epoch;
  await page.evaluate(() => history.replaceState({}, '', location.href));
  await h.loadStart();
  // A provider result arrives while Native is awaiting the live-document proof.
  const segment = h.plans[0].segments[1];
  await act(async () => h.results([{ segment_id: segment.segment_id, translated_text: '历史更新后仍保留内容。', translation_status: 'succeeded' }]));
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  await expect(page.locator('.reader-translation-node')).toHaveCount(2);
  expect(h.resets).toBe(resets);
  expect(h.messages.filter((m) => m.type === 'reader_translation_handshake').at(-1).document_epoch).toBe(epoch);
  await h.loadEnd();
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  await expect(page.locator('.reader-translation-node')).toHaveCount(2);
  expect(h.resets).toBe(resets);
});

for (const order of ['native-first', 'runtime-first']) test(`SPA change reconnects with ${order} Android events`, async ({ page }) => {
  const h = await harness(page);
  await translateFirst(page, h);
  const old = h.messages.find((m) => m.type === 'reader_translation_navigation_reset');
  const previousPlans = h.plans.length;
  if (order === 'native-first') await h.loadStart('https://navigation.test/next');
  await page.evaluate(() => history.pushState({}, '', '/next'));
  if (order === 'runtime-first') { await h.flush(); await h.loadStart('https://navigation.test/next'); }
  await h.until(() => h.plans.length > previousPlans);
  const live = h.messages.filter((m) => m.type === 'reader_translation_handshake').at(-1);
  expect(live.navigation_id).toBe(old.navigation_id);
  expect(live.document_epoch).not.toBe(old.document_epoch);
  await expect(page.locator('.reader-translation-node')).toHaveCount(0);
  await h.send({ ...old, type: 'reader_translation_status', state: 'stale-result', count: 999 });
  expect(h.states.at(-1)?.bridgeState).not.toBe('stale-result');
});

test('lost initial bridge messages recover through load-end bootstrap', async ({ page }) => {
  const h = await harness(page, { drop: true });
  await h.flush();
  expect(h.plans).toHaveLength(0);
  h.drop = false;
  await h.loadEnd();
  await h.until(() => h.plans.length > 0);
});

test('lost initial messages recover through active probes without another load event', async ({ page }) => {
  const h = await harness(page, { drop: true });
  await h.flush();
  h.drop = false;
  await h.until(() => h.plans.length > 0);
});

for (const lost of ['dropConfirm', 'dropReady']) test(`lost ${lost} completes the handshake through active probes`, async ({ page }) => {
  const h = await harness(page, { [lost]: true });
  await h.until(() => h.plans.length > 0);
  expect(h.states.at(-1).error).toBeNull();
});

test('same-URL reload and redirect reject replayed old proofs, resets and results', async ({ page }) => {
  const h = await harness(page);
  await translateFirst(page, h);
  const oldReset = h.messages.find((m) => m.type === 'reader_translation_navigation_reset');
  const oldProof = h.messages.filter((m) => m.type === 'reader_translation_handshake').at(-1);
  const oldPlan = h.messages.find((m) => m.type === 'reader_translation_batch_plan');
  const oldSegment = h.plans[0].segments[0];
  // Probe may execute in the outgoing document before navigation commits.
  await h.loadStart();
  for (const script of h.scripts.splice(0)) await page.evaluate(script);
  await page.reload();
  await page.evaluate(h.view().props.injectedJavaScript);
  await h.loadEnd();
  const before = h.plans.length;
  await h.until(() => h.plans.length > before);
  let live = h.messages.filter((m) => m.type === 'reader_translation_handshake').at(-1);
  expect(live.navigation_id).not.toBe(oldReset.navigation_id);
  const accepted = h.plans.length;
  await h.send(oldProof);
  await h.send(oldPlan);
  expect(h.plans.length).toBe(accepted);
  await h.send(oldReset);
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  live = h.messages.filter((m) => m.type === 'reader_translation_handshake').at(-1);
  expect(live.navigation_id).not.toBe(oldReset.navigation_id);
  // Even a delayed provider callback cannot make an old segment valid in a new document.
  await act(async () => h.results([{ segment_id: oldSegment.segment_id, translated_text: '陈旧译文', translation_status: 'succeeded' }]));
  await h.flush();
  await expect(page.locator('.reader-translation-node')).toHaveCount(0);
  await h.loadStart('https://navigation.test/requested');
  await page.goto('https://navigation.test/redirected');
  await page.evaluate(h.view().props.injectedJavaScript);
  await h.loadEnd();
  const count = h.plans.length;
  await h.until(() => h.plans.length > count);
  expect(h.messages.filter((m) => m.type === 'reader_translation_handshake').at(-1).document_url).toBe('https://navigation.test/redirected');
});

test('missing runtime times out visibly and exposes a working retry', async ({ page }) => {
  const h = await harness(page, { bootstrap: false });
  await h.loadStart();
  await h.until(() => h.states.at(-1)?.bridgeState === 'bridge_timeout');
  expect(h.states.at(-1).error).toContain('连接超时');
  expect(typeof h.states.at(-1).retry).toBe('function');
  await act(async () => h.states.at(-1).retry());
  await h.until(() => h.plans.length > 0);
  expect(h.states.at(-1).error).toBeNull();
});


test('manual retry recovers only the failed paragraph through native scheduler and DOM', async ({ page }) => {
  const h = await harness(page, { realScheduler: true, bootstrap: false });
  let failedId;
  let failOnce = true;
  h.providerResults = (segments) => segments.map((segment, index) => {
    if (failOnce && index === 0) {
      failOnce = false;
      failedId = segment.segment_id;
      return { segment_id: failedId, translation_status: 'failed', error_code: 'invalid_response', error_retryable: false };
    }
    return { segment_id: segment.segment_id, translation_status: 'succeeded', translated_text: '恢复成功的完整译文。' };
  });
  await h.start();
  await h.until(() => h.states.at(-1)?.failedCount === 1);
  const requestsBefore = h.requests.length;
  await act(async () => h.states.at(-1).retry());
  await h.until(() => h.states.at(-1)?.failedCount === 0 && h.requests.length > requestsBefore);
  await h.flush();
  expect(h.requests.slice(requestsBefore).flat().map((segment) => segment.segment_id)).toEqual([failedId]);
  await expect(page.locator('.reader-translation-node')).toHaveCount(2);
});

test('page budget notice survives ready and same-document handshake, clears on new document', async ({ page }) => {
  const h = await harness(page);
  await h.until(() => h.plans.length > 0);
  const live = h.messages.filter((message) => message.type === 'reader_translation_handshake').at(-1);
  await h.send({ ...live, type: 'reader_translation_status', state: 'budget_exhausted' });
  expect(h.states.at(-1).error).toContain('处理上限');
  expect(h.states.at(-1).retry).toBeUndefined();
  await h.send({ ...live, type: 'reader_translation_status', state: 'ready' });
  await h.loadStart();
  await h.flush();
  expect(h.states.at(-1).error).toContain('处理上限');
  const before = h.plans.length;
  await page.evaluate(() => history.pushState({}, '', '/next'));
  await h.loadStart('https://navigation.test/next');
  await h.until(() => h.plans.length > before);
  expect(h.states.at(-1).error).toBeNull();
  await h.send({ ...live, type: 'reader_translation_status', state: 'budget_exhausted' });
  expect(h.states.at(-1).error).toBeNull();
});

test('diagnostic rate limit never drops accepted failure or completion state', async ({ page }) => {
  const h = await harness(page);
  await h.until(() => h.plans.length > 0);
  const live = h.messages.filter((message) => message.type === 'reader_translation_handshake').at(-1);
  const segmentId = h.plans[0].segments[0].segment_id;
  const send = (state) => h.view().props.onMessage({ nativeEvent: { data: JSON.stringify({ ...live,
    type: 'reader_translation_segment_event', segment_id: segmentId, state, reason: 'test_state',
  }) } });
  await act(async () => { for (let i = 0; i < 120; i++) send('translated'); send('failed'); });
  expect(h.states.at(-1).failedCount).toBe(1);
  expect(h.states.at(-1).error).toContain('翻译片段');
  await act(async () => send('translated'));
  expect(h.states.at(-1).failedCount).toBe(0);
  expect(h.states.at(-1).error).toBeNull();
});
test('independent Native scheduler advances beyond ten parts and recovers middle token failure only',async({page})=>{
 const h=await harness(page,{realScheduler:true,bootstrap:false});
 await page.evaluate(()=>document.body.innerHTML='<p id="long"><a href="https://example.org/target">'+('Meaningful long linked sentence with complete source words. '.repeat(1800))+'</a></p>');
 let ordinal=0,failedId;
 h.providerResults=segments=>segments.map(segment=>{ordinal++;if(ordinal===7){failedId=segment.segment_id;return {segment_id:segment.segment_id,translation_status:'succeeded',translated_text:'missing all rich text tokens'};}return {segment_id:segment.segment_id,translation_status:'succeeded',translated_text:segment.text};});
 await h.start();
 await h.until(()=>new Set(h.requests.flat().map(s=>s.segment_id)).size>=15&&h.states.at(-1)?.failedCount===1);
 await page.waitForTimeout(300);await h.flush();
 expect(await page.locator('.reader-translation-node').count()).toBe(0);
 const before=h.requests.length;
 await act(async()=>h.states.at(-1).retry());
 await h.until(()=>h.states.at(-1)?.failedCount===0&&h.requests.length>before);
 await expect.poll(async()=>{await h.flush();return page.locator('.reader-translation-node').count();}).toBe(1);
 expect(h.requests.slice(before).flat().map(s=>s.segment_id)).toEqual([failedId]);
 const text=await page.locator('.reader-translation-node').innerText();
 expect((text.match(/Meaningful long linked sentence/g)||[]).length).toBe(1800);
 console.log('INDEPENDENT multipart Native PASS',{parts:new Set(h.requests.flat().map(s=>s.segment_id)).size,retried:h.requests.slice(before).flat().length});
});


test('transient polling failure recovers silently and preserves already translated DOM', async ({ page }) => {
  const h = await harness(page, { realScheduler: true, bootstrap: false });
  let calls = 0;
  h.providerResults = (segments) => {
    calls++;
    if (calls === 1) return segments.map((segment, index) => ({ segment_id: segment.segment_id,
      translation_status: index ? 'pending' : 'succeeded', translated_text: index ? null : '已完成的第一段。', retry_after_ms: 750 }));
    if (calls === 2) throw new TypeError('Network request failed');
    return segments.map(segment => ({ segment_id: segment.segment_id,
      translation_status: 'succeeded', translated_text: '自动恢复的第二段。' }));
  };
  await h.start();
  await expect.poll(async () => { await h.flush(); return page.locator('.reader-translation-node').count(); }).toBe(1);
  await page.evaluate(() => { window.firstTranslation = document.querySelector('.reader-translation-node'); });
  await h.until(() => calls >= 3);
  await expect.poll(async () => { await h.flush(); return page.locator('.reader-translation-node').count(); }).toBe(2);
  expect(await page.evaluate(() => window.firstTranslation.isConnected)).toBe(true);
  expect(h.states.every(state => !state.error && !state.failedCount)).toBe(true);
  const completed = h.requests[0][0].segment_id;
  expect(h.requests.slice(1).flat().some(segment => segment.segment_id === completed)).toBe(false);
  expect(calls).toBe(3);
});


async function extractReader(page, h) {
  const results = [];
  let cancel;
  await act(async () => { cancel = h.states.at(-1).extractReader(result => results.push(result)); });
  await h.until(() => results.length > 0);
  return { result: results[0], results, cancel };
}

test('reader extracts a short original article with translation disabled and removes injected translations', async ({ page }) => {
  const h = await harness(page, { bootstrap: false });
  h.props = { ...h.props, translationEnabled: false, translationMode: 'original' };
  await act(async () => h.tree.update(React.createElement(TranslatableWebView, h.props)));
  await page.evaluate(() => {
    document.title = 'A short announcement';
    document.body.innerHTML = '<article><h1>A short announcement</h1><p class="reader-source-hidden">A complete short announcement in the original language.</p><span class="reader-translation-node">这不是原文。</span><p><span class="reader-source-layout">The closing sentence is here.</span></p></article><footer>Unrelated page footer</footer>';
  });
  await h.start();
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  const { result } = await extractReader(page, h);
  expect(result.ok).toBe(true);
  expect(result.article.text).toContain('The closing sentence is here.');
  expect(result.article.text.length).toBeLessThan(500);
  expect(result.article.text).not.toContain('这不是原文');
  expect(result.article.html).not.toContain('reader-source');
  expect(h.requests).toHaveLength(0);
  expect(await page.locator('.reader-translation-node').count()).toBe(1);
});

test('reader snapshots ignore unrelated footer updates but invalidate same-URL article replacement', async ({ page }) => {
  const h = await harness(page, { bootstrap: false });
  const invalidated = [];
  h.props = { ...h.props, onReaderSnapshotInvalidated: id => invalidated.push(id) };
  await act(async () => h.tree.update(React.createElement(TranslatableWebView, h.props)));
  await page.evaluate(() => { document.body.innerHTML = '<article><h1>Snapshot article</h1><p>A complete article paragraph containing the original snapshot content.</p><p>The final paragraph finishes this short article.</p></article><footer id="footer">Footer</footer>'; });
  await h.start();
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  const { result } = await extractReader(page, h);
  expect(result.ok).toBe(true);
  await page.locator('#footer').evaluate(node => { node.textContent = 'A changing footer counter'; });
  await h.flush();
  expect(invalidated).toEqual([]);
  await page.locator('article').evaluate(node => node.replaceChildren(Object.assign(document.createElement('p'), { textContent: 'The new article has replaced the original content at the same URL.' })));
  await h.until(() => invalidated.length === 1);
  expect(invalidated).toEqual([result.requestId]);
});

async function contextualReader(page) {
  const h = await harness(page, { bootstrap: false });
  h.invalidated = [];
  h.props = { ...h.props, translationEnabled: false, translationMode: 'original',
    onReaderSnapshotInvalidated: id => h.invalidated.push(id) };
  await act(async () => h.tree.update(React.createElement(TranslatableWebView, h.props)));
  await page.evaluate(() => {
    document.documentElement.lang = 'en';
    document.head.innerHTML = '<title>Original article title with words</title><base href="https://navigation.test/old/"><meta name="author" content="Original Author"><style id="unrelated-style">body { color: black }</style><script id="unrelated-script" type="application/json">{"counter":1}</script>';
    document.body.className = 'author-page';
    document.body.innerHTML = '<div id="outside-candidate">External Author</div><main id="article-parent"><article><h1>A stable article heading</h1><p>A complete paragraph explains the original content and gives this article enough readable prose for extraction.</p><p>The closing paragraph includes a <a href="target">relative source link</a> that depends on document context.</p></article></main><footer id="footer"><p>A footer counter</p></footer>';
  });
  return h;
}

const contextChanges = [
  { name: 'base href', mutate: () => { document.querySelector('base').href = 'https://navigation.test/new/'; }, check: article => expect(article.html).toContain('https://navigation.test/new/target') },
  { name: 'base removal', mutate: () => { document.querySelector('base').remove(); }, check: article => expect(article.html).toContain('https://navigation.test/target') },
  { name: 'base insertion', mutate: () => { const base = document.createElement('base'); base.href = 'https://navigation.test/first/'; document.head.prepend(base); }, check: article => expect(article.html).toContain('https://navigation.test/first/target') },
  { name: 'document title', mutate: () => { document.title = 'Corrected article title with words'; }, check: article => expect(article.title).toBe('Corrected article title with words') },
  { name: 'meta author content', mutate: () => { document.querySelector('meta[name="author"]').content = 'Corrected Author'; }, check: article => expect(article.byline).toBe('Corrected Author') },
  { name: 'meta author removal', mutate: () => { document.querySelector('meta[name="author"]').remove(); }, check: article => expect(article.byline).toBeNull() },
  { name: 'meta author insertion', mutate: () => { const meta = document.createElement('meta'); meta.name = 'author'; meta.content = 'Inserted Author'; document.head.append(meta); }, check: article => expect(article.byline).toBe('Inserted Author') },
  { name: 'meta attribute promotion', prepare: () => { const meta = document.createElement('meta'); meta.id = 'promoted-meta'; meta.name = 'unrelated'; meta.content = 'Promoted Author'; document.head.append(meta); }, mutate: () => { document.querySelector('#promoted-meta').setAttribute('name', 'author'); }, check: article => expect(article.byline).toBe('Promoted Author') },
  { name: 'HTML language', mutate: () => { document.documentElement.lang = 'fr'; }, check: article => expect(article.lang).toBe('fr') },
  { name: 'JSON-LD text', prepare: () => { const script = document.createElement('script'); script.id = 'article-jsonld'; script.type = 'application/ld+json'; script.textContent = JSON.stringify({ '@context': 'https://schema.org', '@type': 'Article', headline: 'Original JSON title with words' }); document.head.append(script); }, mutate: () => { document.querySelector('#article-jsonld').textContent = JSON.stringify({ '@context': 'https://schema.org', '@type': 'Article', headline: 'Corrected JSON title with words' }); }, check: article => expect(article.title).toBe('Corrected JSON title with words') },
  { name: 'JSON-LD type promotion', prepare: () => { const script = document.createElement('script'); script.id = 'article-jsonld'; script.type = 'application/json'; script.textContent = JSON.stringify({ '@context': 'https://schema.org', '@type': 'Article', headline: 'Promoted JSON title with words' }); document.body.append(script); }, mutate: () => { document.querySelector('#article-jsonld').type = 'application/ld+json'; }, check: article => expect(article.title).toBe('Promoted JSON title with words') },
  { name: 'outside byline text', prepare: () => { document.querySelector('meta[name="author"]').remove(); document.querySelector('#outside-candidate').setAttribute('rel', 'author'); }, mutate: () => { document.querySelector('#outside-candidate').textContent = 'Corrected External Author'; }, check: article => expect(article.byline).toBe('Corrected External Author') },
  { name: 'outside byline attribute promotion', prepare: () => { document.querySelector('meta[name="author"]').remove(); }, mutate: () => { document.querySelector('#outside-candidate').setAttribute('rel', 'author'); }, check: article => expect(article.byline).toBe('External Author') },
  { name: 'ancestor language', mutate: () => { document.body.lang = 'de'; } },
  { name: 'ancestor direction', mutate: () => { document.body.dir = 'rtl'; } },
  { name: 'ancestor hidden state', mutate: () => { document.body.hidden = true; }, invalidationOnly: true },
];
for (const change of contextChanges) test(`reader context invalidates ${change.name} without a URL or epoch change`, async ({ page }) => {
  const h = await contextualReader(page);
  if (change.prepare) await page.evaluate(change.prepare);
  await h.start();
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  const first = await extractReader(page, h);
  expect(first.result.ok).toBe(true);
  await page.evaluate(() => {
    const clone = document.cloneNode;
    window.contextCloneCalls = 0;
    document.cloneNode = function (...args) { window.contextCloneCalls++; return Reflect.apply(clone, this, args); };
  });
  await page.evaluate(change.mutate);
  await h.until(() => h.invalidated.length > 0);
  expect(h.invalidated).toEqual([first.result.requestId]);
  expect(page.url()).toBe(url);
  expect(await page.evaluate(() => window.contextCloneCalls)).toBe(0);
  if (change.invalidationOnly) return;
  const next = await extractReader(page, h);
  expect(next.result.ok).toBe(true);
  expect(next.result.document.epoch).toBe(first.result.document.epoch);
  change.check?.(next.result.article);
});

test('reader context ignores footer, unrelated head and owned updates and bounds large new branches', async ({ page }) => {
  const h = await contextualReader(page);
  await h.start();
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  const first = await extractReader(page, h);
  expect(first.result.ok).toBe(true);
  await page.evaluate(() => {
    document.querySelector('#footer p').textContent = 'A changed footer counter';
    document.querySelector('#unrelated-style').textContent = 'body { color: gray }';
    document.querySelector('#unrelated-script').textContent = '{"counter":2}';
    document.body.setAttribute('data-analytics-counter', '2');
    const translation = Object.assign(document.createElement('span'), { className: 'reader-translation-node', textContent: 'An injected translation' });
    document.querySelector('article p').append(translation);
    translation.setAttribute('lang', 'fr');
  });
  await h.flush();
  expect(h.invalidated).toEqual([]);
  await page.evaluate(() => {
    const branch = document.createElement('div');
    for (let index = 0; index < 2100; index++) branch.appendChild(document.createElement('span'));
    document.querySelector('#footer').append(branch);
  });
  await h.until(() => h.invalidated.length > 0);
  expect(h.invalidated).toEqual([first.result.requestId]);
  const second = await extractReader(page, h);
  await page.evaluate(() => {
    for (let index = 0; index < 2100; index++) document.querySelector('#footer').setAttribute('data-counter', String(index));
  });
  await h.until(() => h.invalidated.length === 2);
  expect(h.invalidated[1]).toBe(second.result.requestId);
});

for (const delay of ['injection', 'response', 'injection after cancel']) test(`reader context observer is cancelled after Native timeout with delayed ${delay}`, async ({ page }) => {
  const h = await contextualReader(page);
  await h.start();
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  await page.evaluate(() => {
    window.readerContextObservers = new Set();
    const observe = MutationObserver.prototype.observe;
    const disconnect = MutationObserver.prototype.disconnect;
    MutationObserver.prototype.observe = function (target, options) {
      if (target === document) window.readerContextObservers.add(this);
      return Reflect.apply(observe, this, [target, options]);
    };
    MutationObserver.prototype.disconnect = function () {
      window.readerContextObservers.delete(this);
      return Reflect.apply(disconnect, this, []);
    };
  });
  const results = [];
  await act(async () => h.states.at(-1).extractReader(result => results.push(result)));
  const injections = h.scripts.splice(0);
  expect(injections.some(script => script.includes('?.extractReader?.'))).toBe(true);
  if (delay === 'response') {
    for (const script of injections) await page.evaluate(script);
    await expect.poll(() => h.inbox.some(message => message.type === 'reader_extraction_result')).toBe(true);
  }
  // Keep either Native injection or page response pending past the real timer.
  await expect.poll(() => results, { timeout: 6_500 }).toEqual([{ ok: false, reason: 'timeout' }]);
  const cancellations = h.scripts.filter(script => script.includes('?.cancelReader?.'));
  expect(cancellations).toHaveLength(1);
  if (delay === 'injection') h.scripts.unshift(...injections);
  await h.flush();
  if (delay === 'injection after cancel') {
    for (const script of injections) await page.evaluate(script);
    await h.flush();
  }
  expect(h.messages.some(message => message.type === 'reader_extraction_result')).toBe(delay !== 'injection after cancel');
  expect(results).toEqual([{ ok: false, reason: 'timeout' }]);
  expect(await page.evaluate(() => window.readerContextObservers.size)).toBe(0);
  await page.evaluate(() => { document.title = 'A title change after timeout'; });
  await h.flush();
  expect(h.messages.filter(message => message.type === 'reader_snapshot_invalidated')).toEqual([]);
  const next = await extractReader(page, h);
  expect(next.result.ok).toBe(true);
  await page.evaluate(({ id, epoch }) => {
    window.__readerTranslationBridge.cancelReader(id, Number.NaN);
    window.__readerTranslationBridge.cancelReader(id, Number.MAX_SAFE_INTEGER + 1);
    window.__readerTranslationBridge.extractReader('reader-unsafe', epoch, Number.POSITIVE_INFINITY);
  }, { id: next.result.requestId, epoch: next.result.document.epoch });
  for (const script of [...cancellations, ...injections]) await page.evaluate(script);
  await h.flush();
  expect(await page.evaluate(() => window.readerContextObservers.size)).toBe(1);
  await page.evaluate(() => { document.title = 'The current snapshot must remain observed'; });
  await h.until(() => h.invalidated.length === 1);
  expect(h.invalidated).toEqual([next.result.requestId]);
});

for (const change of ['many added nodes', 'owned siblings', 'byline text']) test(`reader context budgets helper work before scanning ${change}`, async ({ page }) => {
  const h = await contextualReader(page);
  await page.evaluate(() => {
    window.readerWork = { nodeLists: 0, filterNodes: 0, characters: 0 };
    const Observer = MutationObserver;
    window.MutationObserver = class extends Observer {
      constructor(callback) {
        super((records, observer) => {
          if (!observer.isReaderContextObserver) return callback(records, observer);
          const nodes = NodeList.prototype[Symbol.iterator];
          const text = String.prototype[Symbol.iterator];
          const createWalker = Document.prototype.createTreeWalker;
          NodeList.prototype[Symbol.iterator] = function* () {
            for (const node of Reflect.apply(nodes, this, [])) { window.readerWork.nodeLists++; yield node; }
          };
          String.prototype[Symbol.iterator] = function* () {
            for (const character of Reflect.apply(text, this, [])) { window.readerWork.characters++; yield character; }
          };
          Document.prototype.createTreeWalker = function (root, what, filter) {
            return Reflect.apply(createWalker, this, [root, what, filter ? { acceptNode: node => {
              window.readerWork.filterNodes++;
              return filter.acceptNode(node);
            } } : filter]);
          };
          try { return callback(records, observer); } finally {
            NodeList.prototype[Symbol.iterator] = nodes;
            String.prototype[Symbol.iterator] = text;
            Document.prototype.createTreeWalker = createWalker;
          }
        });
      }
      observe(target, options) { this.isReaderContextObserver = target === document; return super.observe(target, options); }
    };
  });
  await h.start();
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  const first = await extractReader(page, h);
  expect(first.result.ok).toBe(true);
  await page.evaluate(kind => {
    const branch = document.createElement('div');
    if (kind === 'byline text') {
      for (let index = 0; index < 50; index++) {
        const candidate = document.createElement('div');
        candidate.setAttribute('rel', 'author');
        candidate.textContent = ' '.repeat(200);
        branch.append(candidate);
      }
    } else for (let index = 0; index < 10_000; index++) {
      const node = document.createElement('span');
      if (kind === 'owned siblings') node.className = 'reader-translation-node';
      branch.append(node);
    }
    if (kind === 'many added nodes') document.querySelector('#footer').append(...branch.childNodes);
    else document.querySelector('#footer').append(branch);
  }, change);
  await h.until(() => h.invalidated.length === 1);
  expect(h.invalidated).toEqual([first.result.requestId]);
  const work = await page.evaluate(() => window.readerWork);
  if (change === 'many added nodes') expect(work.nodeLists).toBe(0);
  expect(work.nodeLists + work.filterNodes + work.characters).toBeLessThanOrEqual(2_001);
});

test('reader context reads author source through wrappers while excluding injected translations', async ({ page }) => {
  const h = await contextualReader(page);
  await page.evaluate(() => {
    document.querySelector('meta[name="author"]').remove();
    const author = document.querySelector('#outside-candidate');
    author.setAttribute('rel', 'author');
    author.innerHTML = '<span class="reader-source-layout"><span id="author-source">External Author</span></span><span class="reader-translation-node">' + 'Long injected author translation '.repeat(10) + '</span>';
  });
  await h.start();
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  const first = await extractReader(page, h);
  expect(first.result.ok).toBe(true);
  expect(first.result.article.byline).toBe('External Author');
  await page.evaluate(() => { document.querySelector('#outside-candidate .reader-translation-node').textContent = 'A changed translation'; });
  await h.flush();
  expect(h.invalidated).toEqual([]);
  await page.evaluate(() => { document.querySelector('#author-source').textContent = 'Corrected Source Author'; });
  await h.until(() => h.invalidated.length === 1);
  expect(h.invalidated).toEqual([first.result.requestId]);
});

test('reader context marks exhausted initial dependency collection incomplete', async ({ page }) => {
  const h = await contextualReader(page);
  await page.evaluate(() => {
    let branch = document.createElement('div');
    document.querySelector('#footer').append(branch);
    for (let index = 0; index < 200; index++) {
      const nested = document.createElement('div');
      nested.setAttribute('rel', 'author');
      branch.append(nested);
      branch = nested;
    }
    for (let index = 0; index < 1_000; index++) branch.append(document.createElement('span'));
  });
  await h.start();
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  const first = await extractReader(page, h);
  expect(first.result.ok).toBe(true);
  // Normally unrelated (covered above), but a partial dependency set cannot
  // prove this mutation harmless. Conservatively discard the snapshot.
  await page.evaluate(() => { document.querySelector('#unrelated-style').textContent = 'body { color: gray }'; });
  await h.until(() => h.invalidated.length === 1);
  expect(h.invalidated).toEqual([first.result.requestId]);
});

test('reader context observer stays singular across bootstrap and disconnects on cancellation, navigation and cleanup', async ({ page }) => {
  const h = await contextualReader(page);
  await page.evaluate(() => {
    window.readerContextObservers = new Set();
    const observe = MutationObserver.prototype.observe;
    const disconnect = MutationObserver.prototype.disconnect;
    MutationObserver.prototype.observe = function (target, options) {
      if (target === document) window.readerContextObservers.add(this);
      return Reflect.apply(observe, this, [target, options]);
    };
    MutationObserver.prototype.disconnect = function () {
      window.readerContextObservers.delete(this);
      return Reflect.apply(disconnect, this, []);
    };
  });
  await h.start();
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  const baseline = await page.evaluate(() => window.readerContextObservers.size);
  const first = await extractReader(page, h);
  expect(await page.evaluate(() => window.readerContextObservers.size)).toBe(baseline + 1);
  await page.evaluate(h.view().props.injectedJavaScript);
  await h.flush();
  expect(await page.evaluate(() => window.readerContextObservers.size)).toBe(baseline + 1);
  await act(async () => first.cancel());
  await h.flush();
  expect(await page.evaluate(() => window.readerContextObservers.size)).toBe(baseline);
  await page.evaluate(() => { document.title = 'A new title after cancellation'; });
  await h.flush();
  expect(h.invalidated).toEqual([]);
  await extractReader(page, h);
  await page.evaluate(() => history.pushState({}, '', '/context-next-page'));
  await h.flush();
  expect(await page.evaluate(() => window.readerContextObservers.size)).toBe(baseline);
  await h.until(() => h.messages.some(message => message.type === 'reader_translation_handshake_ready' && message.document_url.endsWith('/context-next-page')));
  await extractReader(page, h);
  expect(await page.evaluate(() => window.readerContextObservers.size)).toBe(baseline + 1);
  await page.evaluate(() => window.__readerTranslationBridge.cleanup());
  expect(await page.evaluate(() => window.readerContextObservers.size)).toBe(0);
});

for (const change of [{ selector: 'ol', attribute: 'start', value: '10' },
  { selector: 'td', attribute: 'colspan', value: '2' }]) test(`reader snapshot invalidates a retained ${change.attribute} change without text or epoch changes`, async ({ page }) => {
  const h = await harness(page, { bootstrap: false });
  const invalidated = [];
  h.props = { ...h.props, onReaderSnapshotInvalidated: id => invalidated.push(id) };
  await act(async () => h.tree.update(React.createElement(TranslatableWebView, h.props)));
  await page.evaluate(() => { document.body.innerHTML = '<article><h1>Semantic snapshot</h1><p>This complete article describes the numbered instructions and their tabular details.</p><ol start="1"><li>Follow the first instruction carefully.</li><li>Finish with this complete second instruction.</li></ol><table><thead><tr><th>Item</th><th>Value</th></tr></thead><tbody><tr><td colspan="1">Entry</td><td>Detail</td></tr></tbody></table><p>The final paragraph concludes the original article.</p></article><footer><ol start="1"><li>Footer entry</li></ol></footer>'; });
  await h.start();
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  const { result } = await extractReader(page, h);
  expect(result.ok).toBe(true);
  expect(result.article.html).toContain(`${change.attribute}="1"`);
  await page.evaluate(() => {
    document.querySelector('footer ol').start = 10;
    const translated = Object.assign(document.createElement('span'), { className: 'reader-translation-node', textContent: 'Injected translation' });
    document.querySelector('article p').after(translated);
    translated.lang = 'fr';
  });
  await h.flush();
  expect(invalidated).toEqual([]);
  await page.locator(`article ${change.selector}`).first().evaluate((node, change) => node.setAttribute(change.attribute, change.value), change);
  await h.until(() => invalidated.length === 1);
  expect(invalidated).toEqual([result.requestId]);
  const next = await extractReader(page, h);
  expect(next.result.ok).toBe(true);
  expect(next.result.document.epoch).toBe(result.document.epoch);
  expect(next.result.article.text).toBe(result.article.text);
  expect(next.result.article.html).toContain(`${change.attribute}="${change.value}"`);
});

test('reader invalidation wins over a delayed result and cancellation ignores late messages', async ({ page }) => {
  const h = await harness(page);
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  const results = [];
  await act(async () => h.states.at(-1).extractReader(result => results.push(result)));
  for (const script of h.scripts.splice(0)) await page.evaluate(script);
  await expect.poll(() => h.inbox.some(message => message.type === 'reader_extraction_result')).toBe(true);
  const held = h.inbox.find(message => message.type === 'reader_extraction_result');
  h.inbox = h.inbox.filter(message => message !== held);
  await page.locator('main').evaluate(node => node.replaceChildren(Object.assign(document.createElement('p'), { textContent: 'This is another complete article, replacing the previous text before its result arrived.' })));
  await h.until(() => results.length === 1);
  expect(results[0]).toEqual({ ok: false, reason: 'changed' });
  await h.send(held);
  expect(results).toHaveLength(1);
  let cancel;
  await act(async () => { cancel = h.states.at(-1).extractReader(result => results.push(result)); cancel(); });
  await h.flush();
  expect(results.at(-1)).toEqual({ ok: false, reason: 'cancelled' });
  expect(results).toHaveLength(2);
});

test('reader follows an SPA to its current URL and ignores a previous document result', async ({ page }) => {
  const h = await harness(page);
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  const first = await extractReader(page, h);
  const firstEnvelope = h.messages.filter(message => message.type === 'reader_extraction_result').at(-1);
  await page.evaluate(() => {
    history.pushState({}, '', '/second-article');
    document.title = 'The second article';
    document.querySelector('main').innerHTML = '<h1>The second article</h1><p>This complete new article belongs to the second page.</p><p>Its closing paragraph is different from the original article.</p>';
  });
  await h.until(() => h.messages.some(message => message.type === 'reader_translation_handshake_ready' && message.document_url.endsWith('/second-article')));
  const second = await extractReader(page, h);
  expect(second.result.ok).toBe(true);
  expect(second.result.article.url).toBe('https://navigation.test/second-article');
  expect(second.result.article.title).toBe('The second article');
  expect(second.result.document.epoch).not.toBe(first.result.document.epoch);
  await h.send(firstEnvelope);
  expect(second.results).toHaveLength(1);
});

test('trusted reader purifies hostile bridge HTML before mounting and translates through web_segment scope', async ({ page }, testInfo) => {
  const h = await harness(page, { bootstrap: false, realScheduler: true });
  const candidate = { url, title: 'Safe heading', byline: null, lang: 'en', text: 'A complete article.',
    html: '<p>A complete readable paragraph with enough original words for the translation scheduler.</p><p>The closing paragraph remains available in the reader.</p><script>window.readerAttack = true</script><svg onload="window.readerAttack=true"></svg><img src="data:image/svg+xml,unsafe" onerror="window.readerAttack=true"><a href="javascript:window.readerAttack=true">Unsafe link</a><iframe srcdoc="<script>parent.readerAttack=true</script>"></iframe><form action="https://attacker.test"><input name="cookie"></form><p style="background:url(https://attacker.test/track)">Plain text survives.</p>' };
  const shell = localization.buildExtractedReaderHtml(candidate);
  expect(shell).not.toContain(candidate.html);
  const imageRequests = [];
  await page.route('https://navigation.test/reader-image.png', route => {
    imageRequests.push(route.request().url());
    return route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII=', 'base64') });
  });
  await page.goto('about:blank');
  await page.setContent(shell);
  h.props = { ...h.props, source: { html: shell, baseUrl: 'about:blank' }, translationProfile: 'reader', translationPurpose: 'web_segment', readerContent: { html: candidate.html + '<img src="/reader-image.png" alt="Loaded illustration">', url } };
  await act(async () => h.tree.update(React.createElement(TranslatableWebView, h.props)));
  await h.start();
  await h.until(() => h.requests.length > 0);
  expect(page.url()).toBe('about:blank');
  expect(await page.evaluate(() => document.baseURI)).toBe(url);
  expect(h.props.readerContent.url).toBe(url);
  await expect.poll(() => page.locator('img[alt="Loaded illustration"]').evaluate(image => image.naturalWidth)).toBe(1);
  expect(imageRequests).toEqual(['https://navigation.test/reader-image.png']);
  expect(h.requests.flat().every(segment => segment.purpose === 'web_segment')).toBe(true);
  const payloadPath = testInfo.outputPath('reader-translation-segments.json');
  writeFileSync(payloadPath, JSON.stringify({ segments: h.requests[0] }));
  await testInfo.attach('reader-translation-segments', { path: payloadPath, contentType: 'application/json' });
  expect(await page.evaluate(() => window.readerAttack)).toBeUndefined();
  expect(await page.locator('.content script,.content svg,.content iframe,.content form,.content [onerror],.content [style*="attacker.test"]').count()).toBe(0);
  expect(await page.locator('.content a[href]').count()).toBe(0);
  expect(await page.locator('.content img').first().getAttribute('src')).toBeNull();
  await expect(page.locator('.reader-translation-node').first()).toBeVisible();
  await page.evaluate(() => { window.retainedParagraph = document.querySelector('.content p'); });
  await h.loadEnd();
  await h.flush();
  expect(await page.evaluate(() => window.retainedParagraph === document.querySelector('.content p'))).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('reader.png') });
});

test('reader rejects oversized UTF-8 results explicitly without truncating article content', async ({ page }) => {
  const h = await harness(page, { bootstrap: false });
  h.props = { ...h.props, translationEnabled: false, translationMode: 'original' };
  await act(async () => h.tree.update(React.createElement(TranslatableWebView, h.props)));
  await page.evaluate(() => { document.body.innerHTML = '<article><h1>Long article</h1><p>' + '完整正文。'.repeat(24000) + '</p></article>'; });
  await h.start();
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  const { result } = await extractReader(page, h);
  expect(result).toEqual({ ok: false, reason: 'too_large' });
});


test('reader uses original text after real translated-only rendering and pauses then resumes page scheduling', async ({ page }) => {
  const h = await harness(page, { realScheduler: true });
  await h.until(() => h.requests.length > 0);
  await h.flush();
  await expect(page.locator('.reader-translation-node')).toHaveCount(2);
  h.props = { ...h.props, translationMode: 'translated' };
  await act(async () => h.tree.update(React.createElement(TranslatableWebView, h.props)));
  await h.flush();
  expect(await page.locator('.reader-source-hidden,.reader-source-layout').count()).toBeGreaterThan(0);
  const { result } = await extractReader(page, h);
  expect(result.ok).toBe(true);
  expect(result.article.text).toContain('A complete paragraph about reliable translation');
  expect(result.article.text).not.toContain('已翻译');
  h.props = { ...h.props, translationMode: 'original' };
  await act(async () => h.tree.update(React.createElement(TranslatableWebView, h.props)));
  await h.flush();
  const before = h.requests.length;
  await page.locator('main').evaluate(main => main.appendChild(Object.assign(document.createElement('p'), { textContent: 'A new original paragraph arrives while the original webpage translation is paused.' })));
  await page.waitForTimeout(400);
  await h.flush();
  expect(h.requests.length).toBe(before);
  h.props = { ...h.props, translationMode: 'bilingual' };
  await act(async () => h.tree.update(React.createElement(TranslatableWebView, h.props)));
  await h.until(() => h.requests.length > before);
  expect(h.requests.flat().every(segment => segment.purpose === 'web_segment')).toBe(true);
});

test('reader accepts an existing document anchor without requiring a new epoch', async ({ page }) => {
  const h = await harness(page);
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  const epoch = h.messages.filter(message => message.type === 'reader_translation_handshake_ready').at(-1).document_epoch;
  await page.evaluate(() => { document.querySelector('p').id = 'section'; history.pushState({}, '', '#section'); });
  const { result } = await extractReader(page, h);
  expect(result.ok).toBe(true);
  expect(result.article.url).toBe(url + '#section');
  expect(result.document.epoch).toBe(epoch);
});

test('reader checks large head attributes before cloning a small visible article', async ({ page }) => {
  const h = await harness(page);
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  await page.evaluate(() => {
    const meta = document.createElement('meta');
    meta.content = 'a'.repeat(1_000_001);
    document.head.appendChild(meta);
    window.readerCloneCalls = 0;
    const clone = document.cloneNode;
    document.cloneNode = function (...args) { window.readerCloneCalls++; return Reflect.apply(clone, this, args); };
  });
  const { result } = await extractReader(page, h);
  expect(result).toEqual({ ok: false, reason: 'too_large' });
  expect(await page.evaluate(() => window.readerCloneCalls)).toBe(0);
});

test('reader reconnect invalidates an existing snapshot when content changes before the same-epoch handshake is ready', async ({ page }) => {
  const h = await harness(page, { bootstrap: false });
  const invalidated = [];
  h.props = { ...h.props, translationMode: 'original', onReaderSnapshotInvalidated: id => invalidated.push(id) };
  await act(async () => h.tree.update(React.createElement(TranslatableWebView, h.props)));
  await h.start();
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  const { result } = await extractReader(page, h);
  expect(result.ok).toBe(true);
  h.dropReady = true;
  await h.loadStart();
  await h.flush();
  await page.locator('main').evaluate(main => {
    main.replaceChildren(Object.assign(document.createElement('p'), { textContent: 'This replacement article arrived during the same-document native handshake.' }));
  });
  await h.flush();
  await h.loadEnd();
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  const ready = h.messages.filter(message => message.type === 'reader_translation_handshake_ready').at(-1);
  expect(ready.document_epoch).toBe(result.document.epoch);
  console.log('reader-reconnect-evidence', { invalidated, snapshotInvalidationMessages: h.messages.filter(message => message.type === 'reader_snapshot_invalidated').length, sameEpoch: ready.document_epoch === result.document.epoch });
  expect(invalidated).toEqual([result.requestId]);
  const next = await extractReader(page, h);
  expect(next.result.ok).toBe(true);
  expect(next.result.article.text).toContain('replacement article arrived during');
});

for (const nested of [false, true]) test(`reader budgets ${nested ? 'nested' : 'direct'} template contents before a deep document clone`, async ({ page }) => {
  const h = await harness(page);
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  await page.evaluate(nested => {
    const template = document.createElement('template');
    const payload = document.createElement('div');
    if (nested) {
      const inner = document.createElement('template');
      payload.setAttribute('data-large-value', 'x'.repeat(1_000_001));
      inner.content.appendChild(payload);
      template.content.appendChild(inner);
    } else payload.textContent = 'x'.repeat(1_000_001);
    if (!nested) template.content.appendChild(payload);
    document.head.appendChild(template);
    window.readerCloneCalls = 0;
    const clone = document.cloneNode;
    document.cloneNode = function (...args) { window.readerCloneCalls++; return Reflect.apply(clone, this, args); };
  }, nested);
  const { result } = await extractReader(page, h);
  expect(result).toEqual({ ok: false, reason: 'too_large' });
  expect(await page.evaluate(() => window.readerCloneCalls)).toBe(0);
});

for (const scenario of [
  { name: 'reader link back to the unchanged source URL navigates the retained live page and keeps share and save aligned', beforeLoadEnd: false, navigationType: 'click' },
  { name: 'reader self-link before load-end navigates the original page with an iOS click event', beforeLoadEnd: true, navigationType: 'click' },
  { name: 'reader self-link before load-end navigates the original page without Android navigationType', beforeLoadEnd: true },
  { name: 'reader and original page anchors preserve item saving while hash routes and other articles do not', beforeLoadEnd: false, navigationType: 'click', anchor: true },
]) test(scenario.name, async ({ page, context }) => {
  const h = await harness(page, { bootstrap: false });
  await act(async () => h.tree.unmount());
  await page.reload();
  h.scripts = [];
  h.inbox = [];
  h.messages = [];
  h.scriptTargets = [];
  h.shares = [];
  h.saves = [];
  h.externalOpens = [];
  const item = { content_id: 'source-item-a', source_kind: 'rss', external_url: url,
    title: 'Article A', fetched_at: '2026-09-13T00:00:00Z', is_saved: false };
  await act(async () => { h.tree = create(React.createElement(ArticleReaderScreen, {
    item, session: h.props.session, onBack() {}, renderReaderHtml() { throw new Error('Stored RSS body must not render'); },
  })); });
  const views = () => h.tree.root.findAllByType('WebView');
  const original = () => views().find(view => view.props.source.uri);
  const reader = () => views().find(view => view.props.source.html);
  const header = () => h.tree.root.findByType('DetailHeader');
  h.view = original;
  const retained = original();
  const readerPage = await context.newPage();
  const sourcePageUrl = scenario.anchor ? url : 'https://navigation.test/article-b';
  const targetUrl = scenario.anchor ? `${url}#section` : scenario.beforeLoadEnd ? sourcePageUrl : url;
  let heldImage;
  await readerPage.route('**/reader-slow-image.png', route => { heldImage = route; });
  const readerInbox = [];
  let readerReady = false;
  await readerPage.exposeFunction('readerNativePost', raw => readerInbox.push(JSON.parse(raw)));
  h.flush = async () => {
    for (let round = 0; round < 15; round++) {
      const commands = h.scriptTargets.splice(0);
      let progressed = false;
      for (const command of commands) {
        if (command.reader && !readerReady) { h.scriptTargets.push(command); continue; }
        if (command.reader && !reader()) continue;
        progressed = true;
        await (command.reader ? readerPage : page).evaluate(command.script);
      }
      for (const message of h.inbox.splice(0)) { progressed = true; await h.send(message); }
      for (const message of readerInbox.splice(0)) {
        if (!reader()) continue;
        progressed = true;
        await act(async () => reader().props.onMessage({ nativeEvent: { data: JSON.stringify(message) } }));
      }
      if (!progressed) break;
    }
  };
  try {
    await h.start();
    await h.until(() => h.messages.some(message => message.type === 'reader_translation_handshake_ready' && message.document_url === url));
    await page.evaluate(({ sourcePageUrl, targetUrl }) => {
      if (location.href !== sourcePageUrl) history.pushState({}, '', sourcePageUrl);
      document.title = 'Current Article';
      document.querySelector('main').innerHTML = '<h1>Current Article</h1><p id="first-section">The current article contains a complete paragraph of original text.</p><p id="section">This closing paragraph includes a <a href="' + targetUrl + '">open this article</a> link.</p><p><img src="/reader-slow-image.png" alt="Article illustration"></p>';
    }, { sourcePageUrl, targetUrl });
    await h.until(() => h.messages.some(message => message.type === 'reader_translation_handshake_ready' && message.document_url === sourcePageUrl));
    expect(original().props.source.uri).toBe(url);
    expect(page.url()).toBe(sourcePageUrl);
    if (scenario.anchor) {
      expect(typeof header().props.onSave).toBe('function');
      await page.evaluate(() => {
        const link = document.createElement('a');
        link.href = '#first-section';
        document.body.append(link);
        link.click();
        link.remove();
      });
      await h.until(() => h.messages.some(message => message.type === 'reader_translation_location_changed' && message.document_url === `${url}#first-section`));
      expect(typeof header().props.onSave).toBe('function');
    } else expect(header().props.onSave).toBeUndefined();
    await act(async () => header().props.onShare());
    expect(h.shares.at(-1).url).toBe(page.url());

    await act(async () => h.tree.root.findByType('FloatingReaderTools').props.onSwitchMode());
    await h.until(() => views().length === 2);
    const readerView = reader();
    expect(readerView.props.source.baseUrl).toBe('about:blank');
    expect(readerView.props.onShouldStartLoadWithRequest({ url: 'about:blank', isTopFrame: true, navigationType: 'other' })).toBe(true);
    await readerPage.setContent(readerView.props.source.html);
    await readerPage.evaluate(() => { window.ReactNativeWebView = { postMessage: raw => window.readerNativePost(raw) }; });
    readerReady = true;
    await act(async () => readerView.props.onLoadStart({ nativeEvent: { url: 'about:blank' } }));
    await readerPage.evaluate(readerView.props.injectedJavaScript);
    if (!scenario.beforeLoadEnd) await act(async () => readerView.props.onLoadEnd({ nativeEvent: { url: 'about:blank' } }));
    await h.flush();
    await expect(readerPage.locator('.content a')).toHaveAttribute('href', targetUrl);
    await expect.poll(() => Boolean(heldImage)).toBe(true);
    expect(await readerPage.locator('.content img').evaluate(image => image.complete)).toBe(false);
    const navigationDecisions = [];
    await readerPage.exposeFunction('readerRequestNavigation', target => act(async () => {
      const decision = readerView.props.onShouldStartLoadWithRequest({ url: target, isTopFrame: true,
        ...(scenario.navigationType ? { navigationType: scenario.navigationType } : {}) });
      navigationDecisions.push(decision);
      return decision;
    }));
    await readerPage.evaluate(() => document.addEventListener('click', event => {
      const link = event.target.closest('a');
      if (!link) return;
      event.preventDefault();
      window.readerRequestNavigation(link.href);
    }));
    await readerPage.locator('.content a').click();
    await expect.poll(() => views().length).toBe(1);
    expect(navigationDecisions).toEqual([false]);
    expect(readerPage.url()).toBe('about:blank');
    expect(original()).toBe(retained);
    expect(original().props.source.uri).toBe(url);
    expect(page.url()).toBe(scenario.anchor ? `${url}#first-section` : sourcePageUrl);
    if (scenario.anchor) expect(typeof header().props.onSave).toBe('function');
    else expect(header().props.onSave).toBeUndefined();
    await act(async () => header().props.onShare());
    expect(h.shares.at(-1).url).toBe(page.url());

    const previousNavigation = h.messages.filter(message => message.type === 'reader_translation_handshake_ready').at(-1).navigation_id;
    const navigated = page.waitForEvent('framenavigated', frame => frame === page.mainFrame());
    await h.flush();
    await navigated;
    await page.waitForURL(targetUrl, { waitUntil: 'load' });
    if (scenario.anchor) {
      await h.until(() => h.messages.some(message => message.type === 'reader_translation_location_changed' && message.document_url === targetUrl));
      expect(h.messages.filter(message => message.type === 'reader_translation_handshake_ready').at(-1).navigation_id).toBe(previousNavigation);
    } else {
      await h.start();
      await h.until(() => h.messages.some(message => message.type === 'reader_translation_handshake_ready' &&
        message.navigation_id !== previousNavigation && message.document_url === targetUrl));
    }
    expect(page.url()).toBe(targetUrl);
    expect(original()).toBe(retained);
    await act(async () => { header().props.onShare(); header().props.onExternalOpen(); });
    expect(h.shares.at(-1).url).toBe(targetUrl);
    expect(h.externalOpens.at(-1)).toBe(targetUrl);
    if (targetUrl === url || scenario.anchor) {
      await act(async () => header().props.onSave());
      expect(h.saves.at(-1)).toEqual({ id: item.content_id, saved: true });
    } else expect(header().props.onSave).toBeUndefined();
    if (scenario.anchor) {
      for (const next of [`${url}#/another-article`, 'https://navigation.test/article-b', 'https://navigation.test/article-b#section']) {
        await page.evaluate(target => history.pushState({}, '', target), next);
        await h.until(() => h.messages.some(message => ['reader_translation_handshake_ready', 'reader_translation_location_changed'].includes(message.type) && message.document_url === next));
        expect(header().props.onSave).toBeUndefined();
        await act(async () => { header().props.onShare(); header().props.onExternalOpen(); });
        expect(h.shares.at(-1).url).toBe(next);
        expect(h.externalOpens.at(-1)).toBe(next);
      }
      await page.evaluate(target => history.pushState({}, '', target), url);
      const oldEpoch = h.messages.filter(message => message.type === 'reader_translation_handshake_ready').at(-1).document_epoch;
      await h.until(() => h.messages.some(message => message.type === 'reader_translation_handshake_ready' && message.document_url === url && message.document_epoch !== oldEpoch));
      await act(async () => header().props.onSave());
      expect(h.saves.at(-1)).toEqual({ id: item.content_id, saved: false });
    }
  } finally {
    if (heldImage) await heldImage.abort();
    await readerPage.close();
  }
});

test('reader Screen publishes the ready URL after an anchor changes during handshake and rejects stale documents', async ({ page }) => {
  const h = await harness(page, { bootstrap: false });
  await act(async () => h.tree.unmount());
  h.scripts = []; h.inbox = []; h.messages = [];
  h.shares = []; h.externalOpens = []; h.saves = [];
  const item = { content_id: 'anchor-handshake-item', source_kind: 'rss', external_url: url,
    title: 'Anchor handshake fixture', fetched_at: '2026-09-13T00:00:00Z', is_saved: false };
  await act(async () => { h.tree = create(React.createElement(ArticleReaderScreen, {
    item, session: h.props.session, onBack() {}, renderReaderHtml() { throw new Error('Unexpected stored body rendering'); },
  })); });
  h.view = () => h.tree.root.findByType('WebView');
  const header = () => h.tree.root.findByType('DetailHeader');
  const assertPublishedUrl = async expected => {
    await act(async () => { header().props.onShare(); header().props.onExternalOpen(); });
    expect(h.shares.at(-1).url).toBe(expected);
    expect(h.externalOpens.at(-1)).toBe(expected);
  };
  await page.evaluate(() => {
    const paragraphs = document.querySelectorAll('p');
    paragraphs[0].id = 'old'; paragraphs[1].id = 'new';
  });
  await h.start();
  await h.until(() => h.messages.some(message => message.type === 'reader_translation_handshake_ready'));
  const firstReady = h.messages.filter(message => message.type === 'reader_translation_handshake_ready').at(-1);
  await page.evaluate(() => history.pushState({}, '', '#old'));
  await h.until(() => h.messages.some(message => message.type === 'reader_translation_location_changed' && message.document_url.endsWith('#old')));
  const oldLocation = h.messages.filter(message => message.type === 'reader_translation_location_changed').at(-1);
  await assertPublishedUrl(`${url}#old`);
  await h.loadStart(page.url());
  for (const script of h.scripts.splice(0)) await page.evaluate(script);
  await expect.poll(() => h.inbox.some(message => message.type === 'reader_translation_handshake')).toBe(true);
  const proof = h.inbox.find(message => message.type === 'reader_translation_handshake');
  h.inbox = h.inbox.filter(message => message !== proof);
  await h.send(proof);
  const heldConfirm = h.scripts.splice(0);
  expect(heldConfirm.some(script => script.includes('?.confirmEpoch('))).toBe(true);
  await page.evaluate(() => history.pushState({}, '', '#new'));
  await expect.poll(() => h.inbox.some(message => message.type === 'reader_translation_location_changed')).toBe(true);
  const changed = h.inbox.find(message => message.type === 'reader_translation_location_changed');
  h.inbox = h.inbox.filter(message => message !== changed);
  await h.send(changed);
  for (const script of heldConfirm) await page.evaluate(script);
  await expect.poll(() => h.inbox.some(message => message.type === 'reader_translation_handshake_ready')).toBe(true);
  const ready = h.inbox.find(message => message.type === 'reader_translation_handshake_ready');
  expect(proof.document_url).toBe(`${url}#old`);
  expect(ready.document_url).toBe(`${url}#new`);
  expect(ready.document_epoch).toBe(proof.document_epoch);
  expect(ready.navigation_id).toBe(proof.navigation_id);
  await h.flush();
  await assertPublishedUrl(`${url}#new`);
  expect(typeof header().props.onSave).toBe('function');

  // Replayed real ready messages must not complete a newer challenge or move
  // the mounted Screen back to a stale URL, even within the same epoch.
  await h.loadStart(page.url());
  await h.send(firstReady);
  await h.send(ready);
  await assertPublishedUrl(`${url}#new`);
  await h.flush();
  await assertPublishedUrl(`${url}#new`);
  const nextUrl = 'https://navigation.test/another-document';
  await page.evaluate(target => history.pushState({}, '', target), nextUrl);
  await h.until(() => h.messages.some(message => message.type === 'reader_translation_handshake_ready' && message.document_url === nextUrl));
  expect(h.messages.filter(message => message.type === 'reader_translation_handshake_ready').at(-1).document_epoch).not.toBe(ready.document_epoch);
  await h.send(ready);
  await h.send(oldLocation);
  await h.send(changed);
  await assertPublishedUrl(nextUrl);
  expect(header().props.onSave).toBeUndefined();
});

test('reader snapshot survives paused translation, preference changes and a duplicate same-document bootstrap', async ({ page }) => {
  const h = await harness(page, { bootstrap: false });
  const invalidated = [];
  h.props = { ...h.props, onReaderSnapshotInvalidated: id => invalidated.push(id) };
  await act(async () => h.tree.update(React.createElement(TranslatableWebView, h.props)));
  await h.start();
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  const { result } = await extractReader(page, h);
  expect(result.ok).toBe(true);
  h.props = { ...h.props, translationMode: 'original', targetLocale: 'en' };
  await act(async () => h.tree.update(React.createElement(TranslatableWebView, h.props)));
  await h.flush();
  await page.evaluate(h.view().props.injectedJavaScript);
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  expect(invalidated).toEqual([]);
  await page.locator('main p').first().evaluate(p => { p.textContent = 'The first source paragraph changed after the reader remained open.'; });
  await h.until(() => invalidated.length === 1);
  expect(invalidated).toEqual([result.requestId]);
});

test('reader rejects old session extraction commands and ignores a result delivered after unmount', async ({ page }) => {
  const h = await harness(page);
  await h.until(() => h.states.at(-1)?.bridgeState === 'ready');
  const oldExtract = h.states.at(-1).extractReader;
  const oldResults = [];
  await act(async () => oldExtract(result => oldResults.push(result)));
  h.props = { ...h.props, session: { ...h.props.session, user: { id: 'next-reader-user' } } };
  await act(async () => h.tree.update(React.createElement(TranslatableWebView, h.props)));
  expect(oldResults).toEqual([{ ok: false, reason: 'changed' }]);
  const queued = h.scripts.length;
  const obsoleteResults = [];
  await act(async () => oldExtract(result => obsoleteResults.push(result)));
  expect(obsoleteResults).toEqual([{ ok: false, reason: 'cancelled' }]);
  expect(h.scripts.length).toBe(queued);
  await h.flush();

  const results = [];
  const extract = h.states.at(-1).extractReader;
  await act(async () => extract(result => results.push(result)));
  for (const script of h.scripts.splice(0)) await page.evaluate(script);
  await expect.poll(() => h.inbox.some(message => message.type === 'reader_extraction_result')).toBe(true);
  const messages = h.inbox.splice(0);
  const receive = h.view().props.onMessage;
  await act(async () => h.tree.unmount());
  h.tree = null;
  for (const message of messages) await act(async () => receive({ nativeEvent: { data: JSON.stringify(message) } }));
  expect(results).toEqual([]);
  const detached = [];
  const detachedQueue = h.scripts.length;
  await act(async () => extract(result => detached.push(result)));
  expect(detached).toEqual([{ ok: false, reason: 'cancelled' }]);
  expect(h.scripts.length).toBe(detachedQueue);
});
