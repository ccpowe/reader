import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';

const screenPath = fileURLToPath(new URL('../screens/ArticleReaderScreen.tsx', import.meta.url));
const source = readFileSync(screenPath, 'utf8');

assert.doesNotMatch(source, /<Text style=\{styles\.source\}>/, 'the video page must not put a title above the player');
assert.doesNotMatch(source, /styles\.videoTitle/, 'the video page must not reserve a title block above the player');
assert.match(source, /youtubeMode === 'player' \? styles\.videoStage : styles\.youtubeWebStage/, 'the player and full YouTube page must use dedicated stages');
assert.match(source, /'play-box-outline'/, 'YouTube must retain the compact embedded-player mode');
assert.match(source, /modeLabel=\{youtubeMode === 'player' \? t\('switchToWeb'\) : t\('switchToPlayer'\)\}/, 'YouTube must expose its full watch page with localized playback mode labels');
assert.match(source, /youtubeWatchUrl\(videoId\)/, 'web mode must load the canonical YouTube watch page');
assert.match(source, /onTranslationModeRequest=\{requestPlayerTranslationMode\}/, 'the in-player bilingual control must update native translation state');
assert.match(source, /<View style=\{styles\.timelineStage\}/, 'the transcript must live in its own stage');
assert.match(source, /<FlatList[\s\S]*style=\{styles\.timelineList\}/, 'only the caption list should own scrolling');
assert.doesNotMatch(source, /timelineExpanded|timelineStageCollapsed|timelineExpandButton|timelinePreview/, 'the transcript must remain visible without a fold state');
assert.match(source, /visibleTimelineSegmentIds=\{youtubeMode === 'player' \? visibleTimelineSegmentIds : undefined\}/, 'visible transcript captions must still drive translation scheduling');
assert.match(source, /data=\{captionTimeline\.captions\}/, 'the transcript data must exist independently of translation mode');
assert.doesNotMatch(source, /data=\{translationMode !== 'original' && translation\.enabled \? captionTimeline\.captions : \[\]\}/, 'original mode must not hide the source transcript');
assert.match(source, /translated=\{translation.enabled && timelineTranslationMode !== 'original'\}/, 'timeline text has its own bilingual display state');
assert.match(source, /translated && caption.translatedText/, 'timeline rows render translations when enabled');
assert.match(source, /youtubePage=\{youtubeMode === 'web'\}/, 'YouTube webpage translation coexists with the caption controller');
assert.match(source, /videoStage:\s*\{[^}]*flexGrow:\s*0[^}]*flexShrink:\s*0/, 'the player stage must not shrink with transcript scrolling');
assert.match(source, /youtubeWebStage:\s*\{[^}]*flex:\s*1/, 'the full YouTube watch page must consume the remaining viewport');
assert.match(source, /timelineStage:\s*\{[^}]*flex:\s*1/, 'the transcript stage must consume the remaining viewport');
assert.match(source, /timelineList:\s*\{[^}]*flex:\s*1/, 'the caption list must fill the transcript stage');

const videoStageIndex = source.indexOf("youtubeMode === 'player' ? styles.videoStage");
const timelineStageIndex = source.indexOf('<View style={styles.timelineStage}');
const listIndex = source.indexOf('<FlatList\n', timelineStageIndex);
assert.ok(videoStageIndex < timelineStageIndex && timelineStageIndex < listIndex, 'player must precede the independent timeline');

console.log('video reader layout keeps the player fixed and scrolls captions independently');

// Mount the production reader to exercise command identities and actual list
// handlers; host adapters replace only native rendering and external queries.
const { createRequire } = await import('node:module');
const { default: Module } = await import('node:module');
const require = createRequire(import.meta.url);
const ts = require('typescript');
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
const runtime = {};
const client = {};
let endScrolls = 0;
const HostList = React.forwardRef((props, ref) => {
  React.useImperativeHandle(ref, () => ({ scrollToEnd: () => { endScrolls += 1; } }), []);
  return React.createElement('FlatList', props);
});
const adapters = {
  '../i18n': localization,
  'react-native': { ActivityIndicator: 'ActivityIndicator', Alert: { alert() {} }, FlatList: HostList, Linking: {}, Pressable: 'Pressable', Share: {}, StyleSheet: { create: value => value }, Text: 'Text', View: 'View' },
  '@expo/vector-icons': { MaterialCommunityIcons: 'Icon' },
  '@tanstack/react-query': { useQueryClient: () => client },
  '../components/DetailHeader': { DetailHeader: 'DetailHeader' },
  '../components/FloatingReaderTools': { FloatingReaderTools: 'FloatingReaderTools' },
  '../components/TranslatableWebView': { TranslatableWebView: 'TranslatableWebView' },
  '../components/TitleTranslationNotice': { TitleTranslationNotice: 'TitleTranslationNotice' },
  '../domain/readerHtml': { hasReadableBody: () => { throw new Error('YouTube must not inspect article bodies'); } },
  '../domain/media': { youtubeVideoId: () => 'testvideo01', youtubeWatchUrl: () => 'https://www.youtube.com/watch?v=testvideo01', youtubeEmbedUrl: () => 'https://www.youtube.com/embed/testvideo01', YOUTUBE_APP_REFERER: 'https://www.youtube.com/' },
  '../hooks/useTitleTranslationConvergence': { useTitleTranslationConvergence: () => [] },
  '../hooks/useTranslationPreference': { useTranslationPreference: () => ({ enabled: true, targetLocale: 'zh-CN' }) },
  '../lib/api': { displayTitle: item => item.title },
  '../lib/connection': { captureRuntimeContext: () => ({ runtime, serverId: 'test' }), isRuntimeContextCurrent: () => true },
  '../lib/connection/react': { useReaderRuntime: () => runtime },
  '../state/invalidation': { invalidateAfterSavedMutation() {} },
  '../state/savedMutation': { beginSavedMutation: () => () => {} },
  '../ui/tokens': { colors: {}, radii: {}, touchTarget: 44 },
};
const originalLoad = Module._load;
Module._load = function (request, parent, isMain) { return adapters[request] ?? originalLoad.call(this, request, parent, isMain); };
let tree;
try {
  const compiled = new Module(screenPath);
  compiled.filename = screenPath;
  compiled.paths = Module._nodeModulePaths(fileURLToPath(new URL('..', import.meta.url)));
  compiled._compile(ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText, screenPath);
  const { ArticleReaderScreen } = compiled.exports;
  await act(async () => { tree = create(React.createElement(ArticleReaderScreen, {
    item: { content_id: 'video', source_kind: 'youtube', external_url: 'https://www.youtube.com/watch?v=testvideo01', is_saved: false },
    session: { user: { id: 'test' }, access_token: 'test' }, onBack() {}, renderReaderHtml() {},
  })); });
  const web = () => tree.root.findByType('TranslatableWebView');
  const list = () => tree.root.findByType('FlatList');
  const caption = id => ({ segmentId: id, startMs: 4000, endMs: 8000, sourceText: id, translatedText: null });
  await act(async () => web().props.onCaptionTimelineChange({ captions: [caption('first')], currentSegmentId: 'first', hasPrevious: false, hasNext: true }));
  const originalVideoSource = web().props.source;
  const originalCaptionData = list().props.data;
  assert.equal(tree.root.findByType('FloatingReaderTools').props.modeLabel, '切换到网页');
  await act(async () => localization.i18n.changeLanguage('en'));
  assert.equal(tree.root.findByType('FloatingReaderTools').props.modeLabel, 'Switch to webpage');
  assert.deepEqual(web().props.source, originalVideoSource, 'changing interface language keeps the same video URL and playback headers');
  assert.strictEqual(list().props.data, originalCaptionData, 'changing interface language preserves the loaded transcript');
  assert.equal(web().props.targetLocale, 'zh-CN', 'interface language cannot change the content translation target');
  await act(async () => localization.i18n.changeLanguage('zh-CN'));
  // Layout-triggered edge notifications cannot automatically churn pages.
  await act(async () => list().props.onEndReached());
  assert.equal(web().props.timelineWindowCommand, null);
  const scroll = (y, height = 2000) => ({ nativeEvent: { contentOffset: { y }, layoutMeasurement: { height: 400 }, contentSize: { height } } });
  await act(async () => {
    list().props.onScrollBeginDrag(scroll(1500));
    list().props.onScroll(scroll(1600));
  });
  assert.equal(web().props.timelineWindowCommand.direction, 'next');
  assert.equal(web().props.timelineWindowCommand.firstId, 'first');
  const nextSequence = web().props.timelineWindowCommand.sequence;
  await act(async () => list().props.onEndReached());
  assert.equal(web().props.timelineWindowCommand.sequence, nextSequence, 'one drag sends one page command');
  await act(async () => web().props.onCaptionTimelineChange({ captions: [caption('later')], currentSegmentId: null, hasPrevious: true, hasNext: true }));
  await act(async () => {
    list().props.onScrollBeginDrag(scroll(100));
    list().props.onScroll(scroll(0));
  });
  assert.equal(web().props.timelineWindowCommand.direction, 'previous');
  await act(async () => web().props.onCaptionTimelineChange({ captions: [caption('first')], currentSegmentId: 'first', hasPrevious: false, hasNext: true }));
  await act(async () => list().props.onContentSizeChange(390, 2000));
  assert.equal(endScrolls, 1, 'previous page is positioned at its trailing edge');
  // A short bounded page cannot scroll: a deliberate swipe must still page.
  await act(async () => {
    list().props.onLayout({ nativeEvent: { layout: { height: 800 } } });
    list().props.onContentSizeChange(390, 300);
    list().props.onTouchStart({ nativeEvent: { pageY: 200 } });
    list().props.onTouchEnd({ nativeEvent: { pageY: 120 } });
  });
  assert.equal(web().props.timelineWindowCommand.direction, 'next');
  const shortSequence = web().props.timelineWindowCommand.sequence;
  await act(async () => {
    list().props.onTouchStart({ nativeEvent: { pageY: 200 } });
    list().props.onTouchEnd({ nativeEvent: { pageY: 199 } });
  });
  assert.equal(web().props.timelineWindowCommand.sequence, shortSequence, 'a tap is not a page gesture');
  for (let sequence = 1; sequence <= 4; sequence += 1) {
    await act(async () => list().props.renderItem({ item: caption('first') }).props.onPress());
    assert.deepEqual(web().props.seekCommand, { positionMs: 4000, sequence });
  }
  console.log('Mounted caption list pages forward/backward and every same-cue click has a fresh seek identity.');
} finally {
  if (tree) await act(async () => tree.unmount());
  Module._load = originalLoad;
}
