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
const reactQuery = require('@tanstack/react-query');
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
const productHuntItem = { ...item, title: 'Useful Product', description: 'A practical tagline',
  translation_key: 'ph-1', image_urls: ['https://example.test/icon.png'], score: 120,
  comments: 9, url: 'https://www.producthunt.com/posts/useful-product', website_url: 'https://example.test/product' };
let rankingRetryCalls = 0;
let previewRetryCalls = 0;
const clearedPreviewQueueFailures = [];
const runtime = { generation: 1, identity: { server_id: 'ranking-surface-test' } };
const savedState = { content_id: null, is_saved: false };
let saveCalls = 0;
let savedStateReads = 0;
let saveFailure = false;
let resolveSave;
let blockSave = false;
const alerts = [];
const retryingIds = new Set();
const rankingRetrying = new Set();

let productHuntError = false;
let productHuntCurrentDate;
let productHuntArchiveDate;
let tree;
let xCard;
try {
  execFileSync(
    process.execPath,
    [
      join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
      '--ignoreConfig',
      'components/RankingTitle.tsx',
      'screens/RankingsScreen.tsx',
      'screens/RankingPreviewScreen.tsx',
      'domain/mainNavigation.ts',
      'domain/rankingTranslationProjection.ts',
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
  const ScrollView = host('ScrollView');
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
      AppState: { currentState: 'active', addEventListener: () => ({ remove() {} }) },
      ActivityIndicator,
      Alert: { alert: (...args) => alerts.push(args) },
      Linking: { openURL: async () => {} },
      Share: { share: async () => {} },
      PanResponder: { create: (handlers) => ({ panHandlers: handlers }) },
      FlatList,
      Image,
      ScrollView,
      Modal,
      Pressable,
      StyleSheet,
      Text,
      View,
    };
    if (request === '@expo/vector-icons') return { MaterialCommunityIcons: host('MaterialCommunityIcons'), Feather: host('Feather') };
    if (request === '@expo/vector-icons/Feather') return { default: host('Feather') };
    if (request === 'react-native-reanimated') return {
      default: Reanimated,
      ...Reanimated,
      useAnimatedScrollHandler: handlers => handlers,
      useComposedEventHandler: handlers => event => handlers.filter(Boolean).forEach(handler => {
        if (typeof handler === 'function') handler(event);
        else handler.onScroll?.(event);
      }),
      useSharedValue: value => ({ value }),
    };
    if (request === 'expo-web-browser') return { openBrowserAsync: async () => {} };
    if (request === '@tanstack/react-query') return { ...reactQuery, useQueryClient: () => ({ fetchQuery: async ({ queryFn }) => queryFn(), setQueryData: () => {} }) };
    if (request === '../components/BottomSheetModal' || request === './BottomSheetModal') return { BottomSheetModal: ({ visible, children }) => visible ? React.createElement(View, null, children) : null };
    if (request === '../components/RankingTitle') return originalLoad.call(this, request, parent, isMain);
    if (request === '../components/CategoryPager') return { CategoryPager: ({ renderPage, selected }) => renderPage(selected) };
    if (request === '../components/PageHeader') return {
      IconButton: () => null,
      PageHeaderContent: ({ title }) => React.createElement(Text, null, title),
    };
    if (request === '../components/TranslatableWebView') return { TranslatableWebView: host('WebView') };
    if (request === '../hooks/useCollapsingChrome') return { useChromeStyle: () => ({}), useCollapsingChrome: () => ({ onScroll: () => {}, revealChrome: () => {} }) };
    if (request === '../hooks/useSources') return { useSources: () => ({ error: null, isPending: false, isSuccess: true, items: [], message: '' }) };
    if (request === '../hooks/useTranslationPreference') return { useTranslationPreference: () => ({ effectiveEngineId: 'test', effectiveEngineFingerprint: 'test-fingerprint', enabled: true, isPending: false, targetLocale: 'zh-CN' }) };
    if (request === '../hooks/useSegmentTranslationQueue') return { useSegmentTranslationQueue: () => ({
      clearFailedSegments: (segmentIds) => clearedPreviewQueueFailures.push(...segmentIds),
      clearTimedOutSegments: () => {},
      enqueueSegments: () => {},
      isTranslating: false,
      failedSegmentIds: new Set(),
      retrySegments: () => {},
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
            effective_engine_fingerprint: 'test-fingerprint',
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
        rankingVariantKey: (kind, options) => kind === 'github' ? `github:${options?.period ?? 'weekly'}` : kind === 'reddit' ? `reddit:${options?.subreddit ?? ''}:${options?.sort ?? ''}` : kind === 'product_hunt' ? `product_hunt:${options?.date ?? productHuntCurrentDate}` : kind,
        useRanking: (_session, kind, options) => {
          const [status, setStatus] = React.useState('failed');
          return {
            failedSegmentIds: new Set(),
            isFetching: false,
            isTitleRetrying: () => false,
            loading: false,
            message: kind === 'product_hunt' && productHuntError ? 'Product Hunt is not configured' : '',
            ranking: { items: kind === 'product_hunt' && productHuntError ? [] : [{
              ...(kind === 'product_hunt' ? { ...productHuntItem, title: options?.date === productHuntCurrentDate ? 'Latest launch' : options?.date === shiftProductHuntDate(productHuntCurrentDate, -1) ? 'Yesterday launch' : options?.date === productHuntArchiveDate ? 'Archive launch' : 'Other launch' } : kind === 'github' ? { ...item, title: ({ daily: 'Daily repository', weekly: 'Weekly repository', monthly: 'Monthly repository' })[options?.period] } : item),
              title_translation_status: kind === 'hacker_news' ? status : 'succeeded',
              translated_title: kind === 'hacker_news' && status === 'succeeded' ? 'Translated ranking title' : null,
            }], fetched_at: '2026-10-22T18:35:00Z', kind, subtitle: 'Today', title: kind },
            refresh: async () => {},
            refreshing: false,
            retryBodyTranslation: () => {},
            retryTitleTranslation: () => {
              if (status === 'succeeded' || rankingRetrying.has(item.translation_key)) return;
              rankingRetrying.add(item.translation_key);
              rankingRetryCalls += 1;
              setStatus('succeeded');
            },
            timedOutSegmentIds: new Set(),
            translating: false,
            translationEnabled: true,
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
      getRankingSavedState: async () => { savedStateReads++; return { ...savedState }; },
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
    if (request === '../domain/formatting') return { formatCount: (value) => String(value ?? 0), relativeTime: () => 'now' };
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
  const { rankingFromSaved } = require(join(outputDirectory, 'domain/mainNavigation.js'));
  const { rankingBodyText, mergeRankingTranslationProjection, missingRankingTranslationSegments } = require(join(outputDirectory, 'domain/rankingTranslationProjection.js'));
  const xItem = { ...item, title: 'Summary heading', description: 'Not the body', body_text: 'First line\nSecond line',
    body_segments: [
      { segment_id: 'x:body:0', text: 'First line\n', translated_text: '第一行\n', translation_status: 'succeeded' },
      { segment_id: 'x:body:1', text: 'Second line', translated_text: '第二行', translation_status: 'succeeded' },
    ],
    author_handle: 'dev', retweet_count: 8, bookmark_count: 3, video_thumbnail_url: 'https://example.test/thumb.jpg' };
  assert.equal(rankingBodyText(xItem, false), 'First line\nSecond line');
  assert.equal(rankingBodyText(xItem, true), '第一行\n第二行');
  assert.equal(rankingBodyText({ ...xItem, body_segments: [{ ...xItem.body_segments[0], translated_text: '第一行' }, xItem.body_segments[1]] }, true), '第一行\n第二行', 'translated lines preserve paragraph boundaries');
  const failedX = { ...xItem, body_segments: [xItem.body_segments[0], { ...xItem.body_segments[1], translated_text: null, translation_status: 'failed' }] };
  assert.equal(rankingBodyText(failedX, true), xItem.body_text, 'partial failure leaves original body readable');
  const xRanking = { kind: 'techtwitter', effective_engine_fingerprint: 'test-fingerprint', items: [xItem] };
  assert.deepEqual(missingRankingTranslationSegments(xRanking, 20, 'zh-CN', true, 'test-fingerprint', new Set([xItem.translation_key])), [], 'translated body skips title and summary');
  assert.deepEqual(missingRankingTranslationSegments({
    kind: 'product_hunt', effective_engine_fingerprint: 'test-fingerprint', items: [{ ...productHuntItem, title_translation_status: null, description_translation_status: null }],
  }, 20, 'zh-CN', true, 'test-fingerprint').map(segment => [segment.purpose, segment.text]), [
    ['ranking_title', 'Useful Product'], ['ranking_description', 'A practical tagline'],
  ], 'Product Hunt name and tagline use the existing ranking translation purposes');
  const { productHuntToday, shiftProductHuntDate, shiftProductHuntMonth } = require(join(outputDirectory, 'domain/productHuntDate.js'));
  productHuntCurrentDate = productHuntToday();
  productHuntArchiveDate = `${shiftProductHuntMonth(productHuntCurrentDate.slice(0, 7), -1)}-15`;
  const { rankingQueryOptions, rankingVariantKey } = require(join(outputDirectory, 'hooks/useRanking.js'));
  assert.deepEqual(['daily', 'weekly', 'monthly'].map(period => rankingVariantKey('github', { period })),
    ['github:daily', 'github:weekly', 'github:monthly'], 'each GitHub period has an independent ranking cache and list-memory key');
  assert.equal(rankingVariantKey('github'), 'github:weekly');
  assert.equal(rankingVariantKey('product_hunt'), `product_hunt:${productHuntCurrentDate}`);
  assert.equal(rankingVariantKey('product_hunt', { date: '2025-06-20' }), 'product_hunt:2025-06-20');
  assert.notEqual(rankingVariantKey('product_hunt', { date: '2025-06-20' }), rankingVariantKey('product_hunt', { date: '2025-06-19' }));
  const githubCacheKeys = ['daily', 'weekly', 'monthly'].map(period => rankingQueryOptions(
    { user: { id: 'ranking-cache-user' } }, 'github', { period }, runtime.identity.server_id,
    runtime, 'zh-CN', true, 'test-fingerprint',
  ).queryKey);
  assert.equal(new Set(githubCacheKeys.map(key => JSON.stringify(key))).size, 3, 'React Query keeps separate GitHub period data');
  const phCacheKeys = ['2025-06-20', '2025-06-19'].map(date => rankingQueryOptions(
    { user: { id: 'ranking-cache-user' } }, 'product_hunt', { date }, runtime.identity.server_id,
    runtime, 'zh-CN', true, 'test-fingerprint',
  ).queryKey);
  assert.notDeepEqual(phCacheKeys[0], phCacheKeys[1], 'separate Product Hunt dates retain independent React Query identities');
  const cache = new reactQuery.QueryClient();
  const options = rankingQueryOptions({ user: { id: 'ranking-cache-user' } }, 'techtwitter', undefined, runtime.identity.server_id, runtime, 'zh-CN', true, 'test-fingerprint');
  cache.setQueryDefaults(options.queryKey, options);
  const cacheRanking = { ...xRanking, items: [xItem, {
    ...xItem,
    rank: 2,
    translation_key: 'ranking-2',
    body_segments: xItem.body_segments.map((segment, index) => ({ ...segment, segment_id: `ranking-2:body:${index}` })),
  }] };
  cache.setQueryData(options.queryKey, cacheRanking);
  const beforeRefresh = cache.getQueryData(options.queryKey);
  cache.setQueryData(options.queryKey, structuredClone(cacheRanking));
  assert.equal(cache.getQueryData(options.queryKey), beforeRefresh, 'identical refresh preserves cached rows instead of repainting every card');
  const updatedRanking = structuredClone(cacheRanking);
  updatedRanking.items[1].body_segments[0].translated_text = '更新后的第一行\n';
  updatedRanking.items[1].score = 99;
  cache.setQueryData(options.queryKey, updatedRanking);
  const afterRefresh = cache.getQueryData(options.queryKey);
  assert.equal(afterRefresh.items[0], beforeRefresh.items[0], 'refresh keeps an unaffected card stable');
  assert.notEqual(afterRefresh.items[1], beforeRefresh.items[1], 'changed card receives fresh data');
  assert.equal(afterRefresh.items[1].score, 99);
  assert.equal(rankingBodyText(afterRefresh.items[1], true), '更新后的第一行\n第二行');
  cache.clear();
  const missingX = { ...xItem, body_segments: xItem.body_segments.map((segment) => ({ ...segment, translated_text: null, translation_status: null })) };
  assert.deepEqual(missingRankingTranslationSegments({ ...xRanking, items: [missingX] }, 20, 'zh-CN', true, 'test-fingerprint', new Set()), [], 'offscreen posts do not enter queue');
  assert.deepEqual(missingRankingTranslationSegments({ ...xRanking, items: [missingX] }, 20, 'zh-CN', true, 'test-fingerprint', new Set([xItem.translation_key])).map((segment) => segment.text), ['First line\n', 'Second line'], 'body segments are submitted without trimming or title translation');
  const changedEngine = mergeRankingTranslationProjection(xRanking, { ...xRanking, effective_engine_fingerprint: 'new-engine', items: [missingX] }, 'zh-CN', true, 'new-engine');
  assert.equal(rankingBodyText(changedEngine.items[0], true), missingX.body_text, 'engine change cannot carry old translations');
  const changedLocale = mergeRankingTranslationProjection(xRanking, { ...xRanking, items: [missingX] }, 'ja-JP', true, 'test-fingerprint');
  assert.equal(rankingBodyText(changedLocale.items[0], true), missingX.body_text, 'locale change cannot reuse body translations');
  const restored = rankingFromSaved({ ...item, content_id: 'saved-x', ranking_kind: 'techtwitter', ranking_item: { ...missingX, translation_locale: null }, is_saved: true, translated_title: 'Stale saved title' });
  assert.equal(restored.body_text, missingX.body_text, 'saved preview recovers the full original post');
  assert.equal(restored.body_segments[1].text, 'Second line');
  assert.equal(restored.description, 'Not the body', 'summary remains separate from body');
  assert.equal(restored.translation_locale, null, 'saved title translation locale does not contaminate body');
  assert.equal(restored.saved_content_id, 'saved-x');
  const { XRankingContent } = require(join(outputDirectory, 'components/XRankingContent.js'));
  await act(async () => { xCard = create(React.createElement(XRankingContent, { item: missingX, translated: false })); });
  const xBody = () => xCard.root.findAll((node) => node.type === 'Text' && node.props.children === missingX.body_text)[0];
  assert.equal(xBody().props.numberOfLines, 5, 'X card initially shows five lines of original body');
  assert.ok(xCard.root.findAll((node) => node.type === 'Image' && node.props.source?.uri === xItem.video_thumbnail_url).length, 'media thumbnail is visible');
  await act(async () => { xCard.root.findAll((node) => node.type === 'Pressable' && node.props.accessibilityRole === 'button')[0].props.onPress({ stopPropagation() {} }); });
  assert.equal(xBody().props.numberOfLines, undefined, 'body expands without losing original text');
  await act(async () => { xCard.unmount(); });
  xCard = undefined;
  const { RankingsScreen } = require(join(outputDirectory, 'screens/RankingsScreen.js'));
  const { RankingPreviewScreen } = require(join(outputDirectory, 'screens/RankingPreviewScreen.js'));
  const session = { access_token: 'test-token', user: { id: 'user-1' } };
  let openedRankingItem;
  const treeProps = { active: true, chromeProgress: { value: 0 }, onOpenItem: (opened) => { openCalls += 1; openedRankingItem = opened; }, session };
  let openCalls = 0;
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
  const rankingTab = label => tree.root.findAll(node => node.type === 'Pressable' && node.props.accessibilityRole === 'tab' && node.props.accessibilityLabel === label)[0];
  const control = label => tree.root.findAll(node => node.type === 'Pressable' && node.props.accessibilityLabel === label)[0];
  const text = value => tree.root.findAll(node => node.type === 'Text' && node.props.children === value);
  await act(async () => { rankingTab('GitHub').props.onPress(); });
  assert.ok(text('Weekly repository').length, 'GitHub starts on weekly results');
  await act(async () => { control('选择时间范围').props.onPress(); });
  const period = label => tree.root.findAll(node => node.type === 'Pressable' && node.props.accessibilityRole === 'radio' && node.props.accessibilityLabel === label)[0];
  assert.equal(period('本周').props.accessibilityState.checked, true, 'period sheet reflects current selection');
  await act(async () => { period('今日').props.onPress(); });
  assert.equal(period('今日'), undefined, 'selecting a period closes the sheet');
  assert.ok(text('Daily repository').length, 'GitHub shows the selected period’s results');
  await act(async () => { control('1 Daily repository').props.onPress(); });
  assert.equal(openedRankingItem.ranking_context.period, 'daily', 'opening a repository keeps its selected period');
  await act(async () => { rankingTab('Product Hunt').props.onPress(); });
  assert.ok(text('Latest launch').length, 'Product Hunt starts with the Los Angeles today ranking');
  assert.ok(text(productHuntItem.description).length, 'Product Hunt shows its tagline');
  assert.ok(tree.root.findAll(node => node.type === 'Image' && node.props.source?.uri === productHuntItem.image_urls[0]).length, 'Product Hunt shows thumbnail');
  assert.ok(text('Product Hunt 原帖 ↗').length, 'Product Hunt original link attribution is visible');
  assert.ok(text('120 票').length);
  await act(async () => { control('1 Latest launch · Product Hunt').props.onPress(); });
  assert.equal(openedRankingItem.url, productHuntItem.url);
  assert.equal(openedRankingItem.ranking_context, undefined, 'Product Hunt never receives a save context');
  assert.equal(control('后一天').props.disabled, true, 'future day navigation is unavailable today');
  assert.ok(tree.root.findAll(node => node.type === 'Text' && Array.isArray(node.props.children) && node.props.children.join('').includes('查询时间：')).length, 'compact metadata shows fetched time');
  assert.equal(text('按所选日期查询 Product Hunt 官方 API；票数及排序为查询时所见，并非当日最终榜单。').length, 0, 'caveat stays hidden behind info');
  await act(async () => { control('榜单说明').props.onPress(); });
  assert.ok(text('按所选日期查询 Product Hunt 官方 API；票数及排序为查询时所见，并非当日最终榜单。').length, 'info sheet reveals official-data caveat');
  await act(async () => { control('关闭榜单说明').props.onPress(); });
  assert.equal(text('按所选日期查询 Product Hunt 官方 API；票数及排序为查询时所见，并非当日最终榜单。').length, 0);
  await act(async () => { control('前一天').props.onPress(); });
  assert.ok(text('Yesterday launch').length, 'previous day changes the displayed ranking');
  assert.equal(control('后一天').props.disabled, false);
  await act(async () => { control('后一天').props.onPress(); });
  assert.ok(text('Latest launch').length, 'next day restores today’s ranking');
  await act(async () => { control('选择日期').props.onPress(); });
  assert.ok(control('关闭日期选择'), 'date toolbar opens the calendar');
  assert.equal(control('下个月').props.disabled, true, 'future months are unavailable');
  const futureDate = shiftProductHuntDate(productHuntCurrentDate, 1);
  if (futureDate.slice(0, 7) === productHuntCurrentDate.slice(0, 7)) {
    const futureDayLabel = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long', timeZone: 'UTC' }).format(new Date(`${futureDate}T12:00:00Z`));
    assert.equal(control(futureDayLabel).props.disabled, true, 'calendar future days are disabled');
  }
  await act(async () => { control('上个月').props.onPress(); });
  assert.equal(control('下个月').props.disabled, false, 'past month can navigate back toward today');
  const archiveDayLabel = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long', timeZone: 'UTC' }).format(new Date(`${productHuntArchiveDate}T12:00:00Z`));
  await act(async () => { control(archiveDayLabel).props.onPress(); });
  assert.equal(control('关闭日期选择'), undefined, 'calendar day selection closes the sheet');
  assert.ok(text('Archive launch').length, 'calendar date changes the displayed historical ranking');
  await act(async () => { rankingTab('GitHub').props.onPress(); });
  assert.ok(text('Daily repository').length, 'GitHub selection survives switching tabs');
  await act(async () => { rankingTab('Product Hunt').props.onPress(); });
  assert.ok(text('Archive launch').length, 'Product Hunt date survives switching tabs');
  await act(async () => { control('今天').props.onPress(); });
  assert.ok(text('Latest launch').length, 'Today returns to the live Los Angeles date');
  assert.equal(control('后一天').props.disabled, true);
  productHuntError = true;
  await act(async () => { tree.update(React.createElement(RankingsScreen, treeProps)); });
  assert.ok(text('Product Hunt is not configured').length, 'backend-disabled error is visible');
  productHuntError = false;
  await act(async () => { rankingTab('GitHub').props.onPress(); });
  await act(async () => { control('选择时间范围').props.onPress(); });
  assert.equal(period('今日').props.accessibilityState.checked, true, 'GitHub period persists when switching ranking types');
  await act(async () => { period('本月').props.onPress(); });
  assert.ok(text('Monthly repository').length, 'monthly selection updates the ranking');

  await act(async () => { tree.unmount(); });
  tree = undefined;
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
  tree = undefined;

  const readsBeforeProductHunt = savedStateReads;
  const savesBeforeProductHunt = saveCalls;
  await act(async () => { tree = create(React.createElement(RankingPreviewScreen, { item: openedRankingItem, onBack: () => {}, session })); });
  assert.equal(savedStateReads, readsBeforeProductHunt, 'Product Hunt preview never requests saved state');
  assert.equal(tree.root.findAll(node => node.type === 'Pressable' && node.props.accessibilityLabel === '收藏').length, 0, 'Product Hunt preview has no save action');
  assert.equal(tree.root.findAll(node => node.type === 'WebView' && node.props.source?.uri === productHuntItem.url).length, 1, 'Product Hunt preview opens the original post');
  assert.equal(saveCalls, savesBeforeProductHunt);
  await act(async () => { tree.unmount(); });
  tree = undefined;

  await act(async () => {
    tree = create(React.createElement(RankingPreviewScreen, {
      item: { ...xItem, ranking_context: { kind: 'techtwitter' } },
      onBack: () => {},
      session,
    }));
  });
  const visible = node => {
    for (let current = node; current; current = current.parent) {
      if (current.props.accessibilityElementsHidden) return false;
    }
    return true;
  };
  const visibleBody = () => tree.root.findAll(node => node.type === 'Text' && node.props.children === xItem.body_text && visible(node));
  const visibleWeb = () => tree.root.findAll(node => node.type === 'WebView' && visible(node));
  assert.equal(visibleBody().length, 1, 'X opens a readable native body without loading the original website');
  assert.equal(tree.root.findAllByType('WebView').length, 0);
  await act(async () => { button('切换到网页').props.onPress(); });
  assert.equal(visibleBody().length, 0, 'native body cannot share the visible page with the website');
  assert.equal(visibleWeb().length, 1);
  await act(async () => {
    button('切换网页双语翻译').props.onPress();
    visibleWeb()[0].props.onTranslationStateChange({ activeCount: 0, bridgeState: 'ready', discoveredCount: 1, error: 'Website translation failed' });
  });
  assert.equal(tree.root.findAll(node => node.type === 'Text' && node.props.children === 'Website translation failed').length, 1);
  await act(async () => { button('切换到阅读').props.onPress(); });
  assert.equal(visibleWeb().length, 0, 'switching back hides the website from sight and accessibility');
  assert.equal(tree.root.findAll(node => node.type === 'Text' && node.props.children === 'Website translation failed').length, 0, 'web translation errors do not leak into native reading');
  await act(async () => { button('翻译正文').props.onPress(); });
  assert.equal(visibleBody().length, 1, 'original text remains available after switching views');
  await act(async () => { tree.unmount(); });
  tree = undefined;
} finally {
  if (tree) await act(async () => { tree.unmount(); });
  if (xCard) await act(async () => { xCard.unmount(); });
  rmSync(outputDirectory, { force: true, recursive: true });
}

console.log('Ranking tabs, period selection, Product Hunt cards, preview saving, and title retry passed.');
