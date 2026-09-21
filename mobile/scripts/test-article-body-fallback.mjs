import assert from 'node:assert/strict';
import { createRequire, Module } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { buildSync, transformSync } from 'esbuild';
import { runInNewContext } from 'node:vm';

const require = createRequire(import.meta.url);
const React = require('react');
const { act, create } = require('react-test-renderer');
const localizationPath = fileURLToPath(new URL('../i18n/index.ts', import.meta.url));
const localizationModule = new Module(localizationPath);
localizationModule.filename = localizationPath;
localizationModule.paths = Module._nodeModulePaths(fileURLToPath(new URL('..', import.meta.url)));
localizationModule._compile(buildSync({
  entryPoints: [localizationPath], bundle: true, platform: 'node', format: 'cjs', write: false,
  external: ['react'],
}).outputFiles[0].text, localizationPath);
const localization = localizationModule.exports;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const host = name => ({ children, ...props }) => React.createElement(name, props, children);
const runtime = { generation: 1 };
const session = { user: { id: 'reader-user' } };
const requests = [];
const saves = [];
let contextCurrent = true;
const client = {};
const translation = { enabled: false, targetLocale: 'zh-CN', effectiveEngineId: null };
const titles = [];
const api = {
  displayTitle: article => article.title,
  getArticle: (_session, id) => new Promise((resolve, reject) => requests.push({ id, resolve, reject })),
  setSavedContent: async (_session, id, saved) => { saves.push({ id, saved }); },
};
const originalLoad = Module._load;
function loadTs(relativePath) {
  const filename = fileURLToPath(new URL(relativePath, import.meta.url));
  const module = new Module(filename);
  module.filename = filename;
  module.paths = Module._nodeModulePaths(fileURLToPath(new URL('..', import.meta.url)));
  module._compile(transformSync(readFileSync(filename, 'utf8'), { loader: 'tsx', format: 'cjs', jsx: 'automatic' }).code, filename);
  return module.exports;
}
let readerHtml;
let formatting;
let webViewMounts = 0;
function WebViewHost({ children, ...props }) {
  React.useEffect(() => { webViewMounts += 1; }, []);
  return React.createElement('TranslatableWebView', props, children);
}
Module._load = function (request, parent, isMain) {
  if (request === '../i18n') return localization;
  if (request === 'react-native') return {
    ActivityIndicator: host('ActivityIndicator'), Alert: {}, FlatList: host('FlatList'), Linking: {},
    Pressable: host('Pressable'), Share: {}, StyleSheet: { create: value => value }, Text: host('Text'), View: host('View'),
  };
  if (request === '@expo/vector-icons') return { MaterialCommunityIcons: host('Icon') };
  if (request === '@tanstack/react-query') return { useQueryClient: () => client };
  if (request === '../components/DetailHeader') return { DetailHeader: host('DetailHeader') };
  if (request === '../components/FloatingReaderTools') return { FloatingReaderTools: host('FloatingReaderTools') };
  if (request === '../components/TitleTranslationNotice') return { TitleTranslationNotice: () => null };
  if (request === '../components/TranslatableWebView') return { TranslatableWebView: WebViewHost };
  if (request === '../hooks/useTitleTranslationConvergence') return { useTitleTranslationConvergence: () => titles };
  if (request === '../hooks/useTranslationPreference') return { useTranslationPreference: () => translation };
  if (request === '../lib/connection/react') return { useReaderRuntime: () => runtime };
  if (request === '../lib/connection') return { captureRuntimeContext: () => ({ runtime, serverId: 'reader-server' }), isRuntimeContextCurrent: () => contextCurrent };
  if (request === '../lib/api') return api;
  if (request === '../domain/readerHtml') return readerHtml;
  if (request === '../domain/media') return { youtubeVideoId: () => 'video-id', youtubeEmbedUrl: () => 'https://youtube.test/embed', youtubeWatchUrl: () => 'https://youtube.test/watch' };
  if (request === '../state/savedMutation') return { beginSavedMutation: () => () => {} };
  if (request === '../state/invalidation') return { invalidateAfterSavedMutation: async () => {} };
  if (request === '../ui/tokens') return { colors: {}, radii: {}, touchTarget: 44 };
  if (request === './formatting') return formatting;
  return originalLoad.call(this, request, parent, isMain);
};

const item = { content_id: 'one', source_kind: 'web', external_url: 'https://example.test/one', title: 'One', fetched_at: '2026-09-09T00:00:00Z', is_saved: false };
const article = (id = 'one', body_html = null, body_text = null) => ({ content_id: id, external_url: `https://example.test/${id}`, title: id, body_html, body_text });
let tree;
try {
  formatting = loadTs('../domain/formatting.ts');
  readerHtml = loadTs('../domain/readerHtml.ts');
  const { ArticleReaderScreen } = loadTs('../screens/ArticleReaderScreen.tsx');
  const render = nextItem => React.createElement(ArticleReaderScreen, { item: nextItem, session, onBack() {}, renderReaderHtml: readerHtml.buildReaderHtml });
  const mount = async (nextItem = item) => { await act(async () => { tree = create(render(nextItem)); }); };
  const unmount = async () => { await act(async () => tree.unmount()); };
  const views = () => tree.root.findAllByType('TranslatableWebView');
  const source = () => views().at(-1).props.source;
  const switchMode = async () => { await act(async () => tree.root.findByType('FloatingReaderTools').props.onSwitchMode()); };
  const resolve = async value => { await act(async () => requests.shift().resolve(value)); };

  for (const sourceKind of ['web', 'rss']) {
    await mount({ ...item, source_kind: sourceKind });
    assert.equal(source().uri, item.external_url, 'Web and RSS open the original page immediately');
    assert.equal(views()[0].props.setSupportMultipleWindows, false, 'ordinary webpages do not open OAuth popup windows');
    assert.equal(requests.length, 0, 'Web and RSS do not request stored article bodies');
    const original = views()[0];
    let receive;
    let cancellations = 0;
    const identity = { navigationId: 'web-document-one', epoch: 'epoch-document-one', url: item.external_url };
    await act(async () => {
      original.props.onWebDocumentChange(identity);
      original.props.onTranslationStateChange({ activeCount: 0, error: null, bridgeState: 'ready',
        extractReader(callback) { receive = callback; return () => { cancellations++; }; } });
    });
    await switchMode();
    assert.equal(tree.root.findByType('FloatingReaderTools').props.modeBusy, true);
    const extracted = { ok: true, requestId: 'reader-one', document: identity,
      article: { url: item.external_url, title: 'Current page', byline: null, lang: 'en', html: '<p>Current original body.</p>', text: 'Current original body.' } };
    await act(async () => receive(extracted));
    assert.equal(views().length, 2, 'reader overlays a retained original WebView');
    assert.strictEqual(views()[0], original);
    assert.equal(views()[1].props.readerContent.html, extracted.article.html);
    assert.ok(!source().html.includes(extracted.article.html), 'untrusted candidate is never interpolated into the trusted shell');
    assert.equal(views()[1].props.translationProfile, 'reader');
    assert.equal(views()[1].props.translationPurpose, 'web_segment', 'local page content stays in user scoped translation');
    assert.equal(views()[1].props.source.baseUrl, 'about:blank');
    assert.equal(original.props.translationMode, 'original', 'hidden page translation is paused');
    await act(async () => {
      assert.equal(views()[1].props.onShouldStartLoadWithRequest({ url: item.external_url, isTopFrame: true }), false, 'same-URL links cannot navigate the trusted reader even before load-end');
    });
    assert.equal(views().length, 1);
    assert.strictEqual(views()[0], original, 'switching back retains the original document');
    await switchMode();
    const delayed = receive;
    await switchMode();
    await act(async () => delayed(extracted));
    assert.equal(views().length, 1, 'cancelled extraction cannot replace the webpage');
    assert.ok(cancellations > 0);
    await switchMode();
    await act(async () => receive({ ok: false, reason: 'unavailable' }));
    assert.equal(views().length, 1, 'failed extraction leaves the webpage visible');
    await act(async () => original.props.onWebDocumentChange({ ...identity, epoch: 'epoch-document-two', url: 'https://example.test/two' }));
    assert.equal(tree.root.findByType('DetailHeader').props.onSave, undefined, 'another webpage is not the original saved item');
    await switchMode();
    await act(async () => receive({ ...extracted, requestId: 'reader-two', article: { ...extracted.article, title: 'Page Two', url: 'https://example.test/two' } }));
    assert.match(source().html, /Page Two/);
    await act(async () => original.props.onReaderSnapshotInvalidated('reader-two'));
    assert.equal(views().length, 1, 'same-URL body replacement invalidates an open snapshot');
    await switchMode();
    await act(async () => {
      receive({ ...extracted, requestId: 'reader-racing' });
      original.props.onReaderSnapshotInvalidated('reader-racing');
    });
    assert.equal(views().length, 1, 'invalidation in the same React batch as a result cannot mount a stale snapshot');
    await unmount();
  }

  await mount();
  const anchorIdentity = { navigationId: 'anchor-document', epoch: 'anchor-epoch', url: item.external_url };
  await act(async () => {
    views()[0].props.onWebDocumentChange(anchorIdentity);
    views()[0].props.onWebDocumentChange({ ...anchorIdentity, url: `${item.external_url}#section` });
  });
  assert.equal(typeof tree.root.findByType('DetailHeader').props.onSave, 'function', 'a confirmed original document anchor retains saving');
  await act(async () => tree.update(React.createElement(ArticleReaderScreen, {
    item, session: { user: { id: 'another-reader-user' } }, onBack() {}, renderReaderHtml: readerHtml.buildReaderHtml,
  })));
  assert.equal(tree.root.findByType('DetailHeader').props.onSave, undefined, 'session changes clear the original-document association');
  await act(async () => views()[0].props.onWebDocumentChange({ ...anchorIdentity, url: `${item.external_url}#another-section` }));
  assert.equal(tree.root.findByType('DetailHeader').props.onSave, undefined, 'an old epoch cannot restore an association after session change');
  await act(async () => views()[0].props.onWebDocumentChange(anchorIdentity));
  assert.equal(typeof tree.root.findByType('DetailHeader').props.onSave, 'function', 'the exact original URL can establish a fresh association');
  await act(async () => tree.update(render({ ...item, content_id: 'another-item' })));
  await act(async () => views()[0].props.onWebDocumentChange({ ...anchorIdentity, url: `${item.external_url}#section` }));
  assert.equal(tree.root.findByType('DetailHeader').props.onSave, undefined, 'another item cannot inherit an old anchor association');
  await unmount();

  await mount({ ...item, source_kind: 'x' });
  assert.equal(source().uri, item.external_url, 'X opens web immediately');
  assert.equal(views()[0].props.setSupportMultipleWindows, true, 'X enables the OAuth popup window used by Google sign-in');
  await resolve(article('one', '<p>Post</p>'));
  assert.equal(source().uri, item.external_url);
  await switchMode();
  assert.equal(views()[0].props.setSupportMultipleWindows, false, 'X reader mode does not enable webpage popup windows');
  await unmount();
  await mount({ ...item, source_kind: 'reddit' });
  assert.equal(source().uri, item.external_url);
  assert.equal(views()[0].props.setSupportMultipleWindows, true, 'Reddit keeps its OAuth popup window behavior');
  assert.equal(requests.length, 0, 'Reddit skips article fetching');
  assert.equal(tree.root.findByType('FloatingReaderTools').props.onSwitchMode, undefined);
  await unmount();

  // Exercise the mounted screen with the real language runtime and dictionaries.
  // Native WebView rendering is the adapter; its source and mount identity are observable.
  await mount();
  let receiveOldItem;
  await act(async () => views()[0].props.onTranslationStateChange({ activeCount: 0, error: null, bridgeState: 'ready',
    extractReader(callback) { receiveOldItem = callback; return () => {}; } }));
  await switchMode();
  await act(async () => tree.root.findByType('DetailHeader').props.onSave());
  assert.equal(tree.root.findByType('DetailHeader').props.saved, true);
  await act(async () => tree.update(render({ ...item, content_id: 'two', external_url: 'https://example.test/two' })));
  await act(async () => receiveOldItem({ ok: true, requestId: 'reader-obsolete-item',
    document: { navigationId: 'web-old', epoch: 'epoch-old', url: item.external_url },
    article: { url: item.external_url, title: 'Obsolete article', byline: null, lang: null, html: '<p>Obsolete body</p>', text: 'Obsolete body' } }));
  assert.equal(tree.root.findByType('DetailHeader').props.saved, false, 'another item cannot inherit a locally changed saved state');
  assert.equal(source().uri, 'https://example.test/two', 'the old item extraction cannot replace a new item webpage');
  assert.equal(views().length, 1);
  await unmount();

  translation.enabled = true;
  await mount({ ...item, source_kind: 'x' });
  const sourceBody = '<p>Original source 中文 &amp; English stays unchanged.</p>';
  await resolve(article('one', sourceBody));
  await switchMode();
  await act(async () => tree.root.findByType('FloatingReaderTools').props.onTranslate());
  const originalSource = source();
  const originalWebView = tree.root.findByType('TranslatableWebView');
  const originalMounts = webViewMounts;
  assert.equal(originalWebView.props.translationMode, 'bilingual');
  await act(async () => localization.i18n.changeLanguage('en'));
  assert.equal(tree.root.findByType('DetailHeader').props.title, 'Post');
  assert.strictEqual(source(), originalSource, 'interface language changes must not replace the article document source');
  assert.strictEqual(tree.root.findByType('TranslatableWebView'), originalWebView, 'interface language changes must not remount the reader');
  assert.equal(webViewMounts, originalMounts, 'no new native WebView instance is created');
  assert.ok(source().html.includes(sourceBody), 'article HTML remains source content');
  assert.equal(originalWebView.props.translationMode, 'bilingual', 'interface language preserves the chosen bilingual display mode');
  assert.equal(originalWebView.props.targetLocale, 'zh-CN', 'interface language preserves the content translation target');
  assert.equal(requests.length, 0, 'interface language does not reload the article');
  await act(async () => tree.root.findByType('DetailHeader').props.onSave());
  assert.deepEqual(saves.at(-1), { id: 'one', saved: true }, 'saving still performs the authenticated content mutation');
  assert.equal(tree.root.findByType('DetailHeader').props.saved, true, 'the native header reflects the saved state');
  assert.strictEqual(source(), originalSource, 'saving after an interface change must not rebuild the old-language HTML source');
  assert.strictEqual(tree.root.findByType('TranslatableWebView'), originalWebView);
  assert.equal(webViewMounts, originalMounts, 'saving after an interface change cannot remount the document');
  await unmount();
  translation.enabled = false;

  // Run the production shell script at its DOM boundary. Unexpected selectors or
  // HTML insertion fail, and the source-content node is deliberately separate.
  const textNode = textContent => ({ textContent, lang: 'zh-CN', set innerHTML(_value) { throw new Error('Shell copy must use textContent'); } });
  const dateNode = textNode('old date');
  const authorNode = textNode('订阅源');
  const emptyNode = textNode('此文章没有可阅读正文。');
  const contentNode = { ...textNode(sourceBody), lang: 'ja' };
  const shellDocument = {
    documentElement: { lang: '' },
    querySelector(selector) {
      const nodes = {
        'body > article > .meta > .byline > [data-reader-date]': dateNode,
        'body > article > .meta > .byline > [data-reader-fallback-author]': authorNode,
        'body > article > .content > [data-reader-empty-body]': emptyNode,
      };
      assert.ok(Object.hasOwn(nodes, selector), `Shell updates must stay inside app-owned metadata: ${selector}`);
      return nodes[selector];
    },
  };
  runInNewContext(readerHtml.createReaderInterfaceScript(item.fetched_at), { document: shellDocument });
  assert.equal(shellDocument.documentElement.lang, '', 'interface copy must not label the whole source document as English');
  assert.equal(dateNode.lang, 'en');
  assert.equal(authorNode.lang, 'en');
  assert.equal(emptyNode.lang, 'en');
  assert.equal(authorNode.textContent, 'Source');
  assert.equal(emptyNode.textContent, 'This article has no readable content.');
  assert.equal(dateNode.textContent, formatting.formatArticleDate(item.fetched_at));
  assert.equal(contentNode.textContent, sourceBody, 'localized shell updates leave article content untouched');
  assert.equal(contentNode.lang, 'ja', 'existing source language information stays untouched');

  const unsafeCopy = '</script><script>globalThis.executed = true</script> & "quoted" \'single\'\u2028\u2029';
  const originalEmptyCopy = localization.i18n.t('reader:emptyBody');
  const originalAuthorCopy = localization.i18n.t('reader:fallbackSource');
  try {
    localization.i18n.addResource('en', 'reader', 'emptyBody', unsafeCopy);
    localization.i18n.addResource('en', 'reader', 'fallbackSource', unsafeCopy);
    const escapedHtml = readerHtml.buildReaderHtml(article(), item, false);
    assert.match(escapedHtml, /<html>/, 'the source document language stays unknown when no source locale is provided');
    assert.match(escapedHtml, /data-reader-fallback-author lang="en"/);
    assert.match(escapedHtml, /data-reader-date lang="en"/);
    assert.ok(escapedHtml.includes(formatting.escapeHtml(unsafeCopy)), 'translated shell copy is escaped in HTML contexts');
    assert.ok(!escapedHtml.includes(unsafeCopy), 'raw translated markup cannot become HTML');
    assert.match(escapedHtml, /data-reader-empty-body translate="no" lang="en"/, 'only app copy has interface language and is excluded from content translation');
    const escapedScript = readerHtml.createReaderInterfaceScript(item.fetched_at);
    assert.ok(!escapedScript.includes('</script>'), 'script payloads cannot close an enclosing script element');
    assert.ok(!escapedScript.includes('\u2028') && !escapedScript.includes('\u2029'), 'script payloads escape JavaScript line separators');
    const executionContext = { document: shellDocument, executed: false };
    runInNewContext(escapedScript, executionContext);
    assert.equal(executionContext.executed, false, 'translation messages remain text, never executable code');
    assert.equal(emptyNode.textContent, unsafeCopy, 'escaped JavaScript payloads preserve exact displayed text');
    assert.equal(authorNode.textContent, unsafeCopy);
    assert.equal(contentNode.textContent, sourceBody);
    assert.equal(contentNode.lang, 'ja');
    assert.equal(shellDocument.documentElement.lang, '');
  } finally {
    localization.i18n.addResource('en', 'reader', 'emptyBody', originalEmptyCopy);
    localization.i18n.addResource('en', 'reader', 'fallbackSource', originalAuthorCopy);
  }
  await act(async () => localization.i18n.changeLanguage('zh-CN'));
  console.log('Web/RSS default pages, local extraction, retained documents, cancellation, failure, navigation, and social modes passed');
  console.log('Reader language switching preserves documents, modes and source text; shell copy uses safe HTML/JavaScript escaping.');
} finally {
  Module._load = originalLoad;
}
