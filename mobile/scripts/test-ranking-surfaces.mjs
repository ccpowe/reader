import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import Module from 'node:module';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-ranking-surfaces-'));
const require = createRequire(import.meta.url);
const React = require('react');
const { act, create } = require('react-test-renderer');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();

const item = {
  author: 'reader',
  comments: 2,
  description: 'A ranking item.',
  description_translation_status: null,
  external_url: 'https://example.test/item',
  image_urls: [],
  language: 'TypeScript',
  rank: 1,
  score: 42,
  source_label: 'Hacker News',
  stars: null,
  stars_this_period: null,
  forks: null,
  title: 'Original ranking title',
  title_translation_status: 'failed',
  translated_description: null,
  translated_title: null,
  translation_key: 'ranking-1',
  translation_locale: 'zh-CN',
  url: 'https://example.test/item',
};
let rankingRetryCalls = 0;
let previewRetryCalls = 0;
const clearedPreviewQueueFailures = [];
const runtime = { generation: 1, identity: { server_id: 'ranking-surface-test' } };
const savedState = { content_id: null, is_saved: false };
let saveCalls = 0;
let saveFailure = false;
let resolveSave;
let blockSave = false;
const alerts = [];
const retryingIds = new Set();
const rankingRetrying = new Set();

try {
  execFileSync(
    process.execPath,
    [
      join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
      '--ignoreConfig',
      'components/RankingTitle.tsx',
      'screens/RankingsScreen.tsx',
      'screens/RankingPreviewScreen.tsx',
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

  const host = (name) => function Host({ children, ...props }) {
    return React.createElement(name, props, children);
  };
  const View = host('View');
  const Text = host('Text');
  const Image = host('Image');
  const ActivityIndicator = host('ActivityIndicator');
  const Pressable = host('Pressable');
  const FlatList = ({ data = [], renderItem, ListEmptyComponent, ListFooterComponent, ListHeaderComponent, ...props }) => React.createElement(
    'FlatList',
    props,
    ListHeaderComponent,
    data.length === 0 ? ListEmptyComponent : null,
    ...data.map((value, index) => renderItem?.({ item: value, index, separators: {} })),
    ListFooterComponent,
  );
  const Modal = ({ children, visible, ...props }) => visible ? React.createElement('Modal', props, children) : null;
  const StyleSheet = { create: (styles) => styles };
  const Reanimated = { FlatList, View };
  const originalLoad = Module._load;
  Module._load = function load(request, parent, isMain) {
    if (request === '../assets/icons/refresh-cw.png') return 1;
    if (request === 'react-native') return {
      ActivityIndicator,
      Alert: { alert: (...args) => alerts.push(args) },
      Linking: { openURL: async () => {} },
      Share: { share: async () => {} },
      PanResponder: { create: (handlers) => ({ panHandlers: handlers }) },
      FlatList,
      Image,
      Modal,
      Pressable,
      StyleSheet,
      Text,
      View,
    };
    if (request === '@expo/vector-icons') return { MaterialCommunityIcons: host('MaterialCommunityIcons'), Feather: host('Feather') };
    if (request === 'react-native-reanimated') return { default: Reanimated, ...Reanimated, useSharedValue: value => ({ value }) };
    if (request === 'expo-web-browser') return { openBrowserAsync: async () => {} };
    if (request === '@tanstack/react-query') return { useQueryClient: () => ({ fetchQuery: async ({ queryFn }) => queryFn(), setQueryData: () => {} }) };
    if (request === '../components/BottomSheetModal') return { BottomSheetModal: ({ visible, children }) => visible ? React.createElement(View, null, children) : null };
    if (request === '../components/RankingTitle') return originalLoad.call(this, request, parent, isMain);
    if (request === '../components/CategoryPager') return { CategoryPager: ({ renderPage, options }) => renderPage(options[0]) };
    if (request === '../components/PageHeader') return {
      IconButton: () => null,
      PageHeaderContent: ({ title }) => React.createElement(Text, null, title),
    };
    if (request === '../components/TranslatableWebView') return { TranslatableWebView: () => React.createElement(View) };
    if (request === '../hooks/useCollapsingChrome') return { useChromeStyle: () => ({}), useCollapsingChrome: () => ({ onScroll: () => {}, revealChrome: () => {} }) };
    if (request === '../hooks/useSources') return { useSources: () => ({ error: null, isPending: false, isSuccess: true, items: [], message: '' }) };
    if (request === '../hooks/useTranslationPreference') return { useTranslationPreference: () => ({ effectiveEngineId: null, enabled: true, isPending: false, targetLocale: 'zh-CN' }) };
    if (request === '../hooks/useSegmentTranslationQueue') return { useSegmentTranslationQueue: () => ({
      clearFailedSegments: (segmentIds) => clearedPreviewQueueFailures.push(...segmentIds),
      clearTimedOutSegments: () => {},
      enqueueSegments: () => {},
      isTranslating: false,
      timedOutSegmentIds: new Set(),
    }) };
    if (request === '../hooks/useRankingTitleRetry') return {
      useRankingTitleRetry: ({ onResults }) => ({
        isTitleRetrying: (retryItem) => retryingIds.has(`${retryItem.translation_key}:title`),
        retryTitleTranslation: async (retryItem) => {
          const id = `${retryItem.translation_key}:title`;
          if (retryingIds.has(id)) return;
          retryingIds.add(id);
          previewRetryCalls += 1;
          onResults([{
            error_code: null,
            error_retryable: null,
            engine_id: 'test',
            engine_label: 'test',
            purpose: 'ranking_title',
            retry_after_ms: null,
            segment_id: id,
            translated_text: 'Translated ranking title',
            translation_locale: 'zh-CN',
            translation_status: 'succeeded',
          }]);
        },
      }),
    };
    if (request === '../hooks/useRanking') {
      return {
        prefetchRanking: async () => {},
        rankingVariantKey: (kind, options) => kind === 'reddit' ? `reddit:${options?.subreddit ?? ''}:${options?.sort ?? ''}` : kind,
        useRanking: () => {
          const [status, setStatus] = React.useState('failed');
          return {
            isFetching: false,
            isTitleRetrying: () => false,
            loading: false,
            message: '',
            ranking: { items: [{ ...item, title_translation_status: status, translated_title: status === 'succeeded' ? 'Translated ranking title' : null }], kind: 'hacker_news', subtitle: 'Today', title: 'Hacker News' },
            refresh: async () => {},
            refreshing: false,
            retryTitleTranslation: () => {
              if (status === 'succeeded' || rankingRetrying.has(item.translation_key)) return;
              rankingRetrying.add(item.translation_key);
              rankingRetryCalls += 1;
              setStatus('succeeded');
            },
            timedOutSegmentIds: new Set(),
          };
        },
      };
    }
    if (request === '../lib/connection/react') return { useReaderRuntime: () => runtime };
    if (request === '../lib/connection') return { captureRuntimeContext: () => ({ runtime, serverId: 'ranking-surface-test' }), isRuntimeContextCurrent: () => true };
    if (request === '../state/invalidation') return { invalidateAfterSavedMutation: async () => {} };
    if (request === '../state/queryClient') return { readerQueryKeys: {
      rankings: (...parts) => ['reader', 'ranking-surface-test', ...parts],
      rankingsPrefix: (userId) => ['reader', 'ranking-surface-test', userId, 'rankings'],
    } };
    if (request === '../state/queryLifecycle') return { protectsQueryKeys: () => () => false, removeObsoleteQueries: async () => {} };
    if (request === '../lib/api') return {
      getRankingSavedState: async () => ({ ...savedState }),
      saveRankingContent: async (_session, body) => {
        assert.equal(body.kind, 'hacker_news'); assert.equal(body.url, item.url);
        saveCalls++;
        if (blockSave) await new Promise(resolve => { resolveSave = resolve; });
        if (saveFailure) throw new Error('save failed');
        savedState.content_id = 'saved-id'; savedState.is_saved = true;
        return { ...savedState };
      },
      setSavedContent: async (_session, id, next) => {
        assert.equal(id, 'saved-id');
        if (saveFailure) throw new Error('save failed');
        savedState.is_saved = next;
      },
    };
    if (request === '../domain/source') return { redditCommunitiesFromSources: () => [], sourceHost: () => 'example.test' };
    if (request === '../domain/webTranslation') return { webTranslationProfileForUrl: () => 'generic' };
    if (request === '../domain/formatting') return { formatCount: (value) => String(value ?? 0) };
    if (request === '../domain/media') return {};
    if (request === '../ui/tokens') return { colors: { background: '#fff', surface: '#fff', surfaceMuted: '#eee', surfaceSubtle: '#eee', textPrimary: '#111', textSecondary: '#444', textStrong: '#111', textTertiary: '#666', textMuted: '#777' }, radii: { md: 10, pill: 20, sm: 8 }, spacing: { md: 8, sm: 8 }, touchTarget: 44 };
    return originalLoad.call(this, request, parent, isMain);
  };

  const toolPosition = require(join(outputDirectory, 'domain/readerToolPosition.js'));
  const originalStorage = globalThis.localStorage;
  const positionStorage = new Map();
  globalThis.localStorage = { getItem: key => positionStorage.get(key) ?? null, setItem: (key, value) => positionStorage.set(key, value) };
  toolPosition.saveToolPosition(0.3);
  assert.equal(toolPosition.readToolPosition(), 0.3, 'position persists across component instances');
  assert.equal(toolPosition.draggedToolPosition(0.3, -900, 600), 0);
  assert.equal(toolPosition.draggedToolPosition(0.3, 900, 600), 1);
  assert.equal(toolPosition.draggedToolPosition(0.3, 900, 0), 0.3, 'unmeasured layout stays finite');
  assert.equal(toolPosition.clampToolPosition(Number.NaN), 0.62);
  globalThis.localStorage = originalStorage;
  const { RankingsScreen } = require(join(outputDirectory, 'screens/RankingsScreen.js'));
  const { RankingPreviewScreen } = require(join(outputDirectory, 'screens/RankingPreviewScreen.js'));
  const session = { access_token: 'test-token', user: { id: 'user-1' } };
  const treeProps = { active: true, chromeProgress: { value: 0 }, onOpenItem: () => { openCalls += 1; }, session };
  let openCalls = 0;
  let tree;
  await act(async () => { tree = create(React.createElement(RankingsScreen, treeProps)); });
  const retryButton = () => tree.root.findAll((node) => node.type === 'Pressable' && node.props.accessibilityLabel === '重试翻译标题')[0];
  assert.ok(retryButton(), 'RankingsScreen mounts the failed title affordance');
  let stopped = false;
  await act(async () => {
    retryButton().props.onPress({ stopPropagation: () => { stopped = true; } });
    retryButton().props.onPress({ stopPropagation: () => { stopped = true; } });
  });
  assert.equal(stopped, true, 'failed-title retry stops card press propagation');
  assert.equal(rankingRetryCalls, 1, 'RankingsScreen invokes one explicit retry');
  assert.equal(openCalls, 0, 'retry never opens the ranking card');
  assert.equal(retryButton(), undefined, 'successful retry removes the failed affordance');

  await act(async () => { tree = create(React.createElement(RankingPreviewScreen, { item: { ...item, ranking_context: { kind: 'hacker_news' } }, onBack: () => {}, session })); });
  const previewRetry = () => tree.root.findAll((node) => node.type === 'Pressable' && node.props.accessibilityLabel === '重试翻译标题')[0];
  assert.ok(previewRetry(), 'RankingPreviewScreen mounts the failed title affordance');
  await act(async () => {
    previewRetry().props.onPress({ stopPropagation: () => {} });
    previewRetry().props.onPress({ stopPropagation: () => {} });
  });
  assert.equal(previewRetryCalls, 1, 'RankingPreviewScreen suppresses duplicate explicit retries');
  assert.deepEqual(clearedPreviewQueueFailures, ['ranking-1:title'], 'explicit retry releases the background queue terminal lock');
  assert.equal(previewRetry(), undefined, 'preview successful retry removes the failed affordance');
  const button = label => tree.root.findAll(node => node.type === 'Pressable' && node.props.accessibilityLabel === label)[0];
  assert.ok(button('收藏'));
  blockSave = true;
  await act(async () => { button('收藏').props.onPress(); button('收藏').props.onPress(); });
  assert.equal(saveCalls, 1, 'rapid double press persists once');
  assert.equal(button('收藏').props.disabled, true);
  await act(async () => { resolveSave(); });
  assert.ok(button('取消收藏'));
  saveFailure = true;
  await act(async () => { button('取消收藏').props.onPress(); });
  assert.ok(button('取消收藏'), 'failed unsave retains saved state');
  assert.equal(alerts.at(-1)[0], '无法更新收藏');
  saveFailure = false;
  await act(async () => { button('取消收藏').props.onPress(); });
  assert.ok(button('收藏'));
  const translateButton = () => button('切换网页双语翻译');
  await act(async () => { translateButton().props.onPress(); });
  assert.equal(translateButton().props.accessibilityState.selected, true);
  await act(async () => { translateButton().props.onPress(); });
  assert.equal(translateButton().props.accessibilityState.selected, false);
  const tools = tree.root.findAll(node => node.type === 'View' && node.props.onPanResponderMove)[0];
  assert.equal(tools.props.onMoveShouldSetPanResponderCapture(null, {dy: 3, dx: 0}), false);
  assert.equal(tools.props.onMoveShouldSetPanResponderCapture(null, {dy: 20, dx: 0}), true);
  await act(async () => { tools.props.onPanResponderGrant(); tools.props.onPanResponderMove(null, {dy: 150}); tools.props.onPanResponderRelease(); });
  await act(async () => { translateButton().props.onPress(); });
  assert.equal(translateButton().props.accessibilityState.selected, false, 'drag release never translates');
  await act(async () => { tree.unmount(); });
} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}

console.log('Mounted RankingsScreen and RankingPreviewScreen failed-title retry passed.');
