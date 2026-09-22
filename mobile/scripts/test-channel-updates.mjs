import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import Module from 'node:module';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-channel-updates-'));
const require = createRequire(import.meta.url);
const React = require('react');
const { act, create } = require('react-test-renderer');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();

const source = (overrides = {}) => ({
  avatar_url: null,
  backlog_cycles: 0,
  canonical_url: 'https://example.test/feed',
  display_name: 'Example source',
  folder_name: null,
  gap_detected: false,
  include_in_home: true,
  kind: 'rss',
  last_complete_at: null,
  last_error_code: null,
  new_count: 0,
  next_scan_at: null,
  source_id: 'source-1',
  status: 'active',
  subscription_id: 'subscription-1',
  sync_phase: 'idle',
  ...overrides,
});

let currentRuntime = { generation: 1, identity: { server_id: 'server-1' } };
let sourceItems = [];
let feedRefetch = async () => ({ data: { pages: [{ channel_update_token: null, items: [], next_cursor: null }] }, isError: false });
let viewedCalls = [];
let capturedPickerProps = null;
let sourcesRefetchCalls = 0;
let sourceQueryCancelCalls = 0;
let selectedCalls = [];
let toggleCalls = [];

try {
  execFileSync(
    process.execPath,
    [
      join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
      '--ignoreConfig',
      'components/ChannelPickerModal.tsx',
      'screens/HomeScreen.tsx',
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
  const Pressable = host('Pressable');
  const Modal = ({ children, visible, ...props }) => visible ? React.createElement('Modal', props, children) : null;
  const SectionList = ({ sections = [], renderItem, renderSectionHeader, ListHeaderComponent, ...props }) => React.createElement(
    'SectionList',
    props,
    ListHeaderComponent,
    ...sections.flatMap((section) => [renderSectionHeader({ section }), ...section.data.map((item, index) => renderItem({ item, index, section }))]),
  );
  const FlatList = ({ ListEmptyComponent, ListHeaderComponent, data = [], renderItem, ...props }) => React.createElement(
    'FlatList', props, ListHeaderComponent, data.length ? data.map((item, index) => renderItem({ item, index })) : ListEmptyComponent,
  );
  const StyleSheet = { create: (styles) => styles };
  const originalLoad = Module._load;
  Module._load = function load(request, parent, isMain) {
    if (request === 'react-native') return {
      Alert: { alert: () => {} },
      Modal,
      Pressable,
      ScrollView: host('ScrollView'),
      SectionList,
      StyleSheet,
      Text,
      TextInput: host('TextInput'),
      View,
    };
    if (request === '@expo/vector-icons') return { MaterialCommunityIcons: host('MaterialCommunityIcons') };
    if (request === 'react-native-safe-area-context') return { SafeAreaView: host('SafeAreaView') };
    if (request === 'react-native-reanimated') {
      const reanimated = { FlatList, View: host('ReanimatedView') };
      return { default: reanimated, ...reanimated };
    }
    if (request === '@tanstack/react-query') return {
      useQueryClient: () => ({
        cancelQueries: async () => { sourceQueryCancelCalls += 1; },
        setQueryData: (_key, updater) => { sourceItems = updater(sourceItems) ?? sourceItems; },
      }),
    };
    if (request === '../components/ChannelPickerModal') return {
      ChannelPickerModal: (props) => { capturedPickerProps = props; return React.createElement('ChannelPickerModal', props); },
    };
    if (request === '../components/CategoryPager') return { CategoryPager: () => null };
    if (request === '../components/Chip') return { Chip: () => null };
    if (request === '../components/FeedCardRow') return { FeedCardRow: () => null };
    if (request === '../components/FeedbackState') return {
      EmptyState: () => null, ErrorState: () => null, LoadingBlock: () => null,
      listFeedbackStyles: { content: {}, state: {} },
    };
    if (request === '../components/PageHeader') return { PageHeaderContent: () => null };
    if (request === '../components/SourceAvatar' || request === './SourceAvatar') return { SourceAvatar: () => React.createElement('SourceAvatar') };
    if (request === '../components/TitleTranslationNotice') return { TitleTranslationNotice: () => null };
    if (request === '../hooks/useCollapsingChrome') return {
      useChromeStyle: () => ({}), useCollapsingChrome: () => ({ onScroll: () => {}, revealChrome: () => {} }),
    };
    if (request === '../hooks/useInboxFeed') return {
      prefetchInboxFeed: async () => {},
      useInboxFeed: () => ({
        fetchNextPage: async () => {}, hasNextPage: false, isError: false, isFetchingNextPage: false,
        items: [], loading: false, message: '', refetch: (...args) => feedRefetch(...args), refreshing: false,
        titleTranslation: { retryTimedOut: () => {}, timedOutContentIds: [], timeoutMessage: '' },
        xTranslation: { byContentId: new Map(), retry: () => {} },
      }),
    };
    if (request === '../hooks/useSources') return {
      useSources: () => ({
        isSuccess: true,
        items: sourceItems,
        refetch: async () => { sourcesRefetchCalls += 1; return { data: sourceItems }; },
      }),
    };
    if (request === '../hooks/useTranslationPreference') return {
      useTranslationPreference: () => ({
        effectiveEngineFingerprint: null, effectiveEngineId: null, enabled: false,
        isPending: false, targetLocale: 'zh-CN',
      }),
    };
    if (request === '../hooks/useSourceFolders') return { useSourceFolders: () => [] };
    if (request === '../hooks/useListPositionMemory') return { useListPositionMemory: () => ({}) };
    if (request === '../domain/feed') return {
      feedScopeCacheKey: (scope) => scope.kind === 'source' ? `source:${scope.sourceId}` : 'all',
      folderFeedScope: (folder) => ({ folder, kind: 'folder' }),
      sourceFeedScope: (sourceId) => ({ kind: 'source', sourceId }),
    };
    if (request === '../domain/folders') return {
      folderLabel: (folder) => folder ?? 'Uncategorized',
      sourceFolder: (item) => item.folder_name ?? '',
      sourceFolders: () => [''],
    };
    if (request === '../domain/source') return {
      sourceDisplayName: (item) => item.display_name ?? item.canonical_url,
      sourceHealthLabel: () => 'Active',
      sourceLocationLabel: (url) => url,
      sourceSecondaryLabel: () => 'example.test',
    };
    if (request === '../domain/listMemory') return {};
    if (request === '../lib/connection/react') return { useReaderRuntime: () => currentRuntime };
    if (request === '../lib/connection') return {
      captureRuntimeContext: (runtime) => runtime === currentRuntime
        ? { generation: runtime.generation, runtime, serverId: runtime.identity.server_id, sessionEpoch: 1 }
        : null,
      isRuntimeContextCurrent: (context) => context?.runtime === currentRuntime && context.generation === currentRuntime.generation,
    };
    if (request === '../lib/api') return {
      markSourceSubscriptionViewed: async (...args) => {
        viewedCalls.push(args);
        return source({ new_count: 0 });
      },
      setSavedContent: async () => {},
      updateSourceSubscriptionHomeInclusion: async (_session, _id, include) => source({ include_in_home: include }),
    };
    if (request === '../state/invalidation') return {
      invalidateAfterSavedMutation: async () => {}, invalidateAfterSourceMutation: async () => {},
    };
    if (request === '../state/savedMutation') return { beginSavedMutation: () => () => {} };
    if (request === '../state/cacheUpdates') return { setCachedFeedSavedState: () => {} };
    if (request === '../state/queryClient') return {
      readerQueryKeys: {
        feed: (...args) => ['feed', ...args], feedPrefix: (...args) => ['feed', ...args], sources: (...args) => ['sources', ...args],
      },
    };
    if (request === '../state/queryLifecycle') return {
      protectsQueryKeys: () => () => false, removeObsoleteQueries: async () => {}, trimInactiveInfiniteQueryWhenIdle: () => {},
    };
    if (request === '../ui/tokens') return {
      colors: new Proxy({}, { get: () => '#111' }), radii: { md: 12, pill: 999, sheet: 24 }, spacing: { md: 12, sm: 8, xl: 24 }, touchTarget: 44,
    };
    if (request === '../ui/layout') return {
      PAGE_HEADER_HEIGHT: 72, SCREEN_HORIZONTAL_PADDING: 20, SCREEN_LIST_BOTTOM_PADDING: 80,
    };
    if (request === '../i18n') return {
      i18n: { language: 'en', resolvedLanguage: 'en', t: (key) => key },
      useTranslation: () => ({ t: (key, values = {}) => `${key}${values.count === undefined ? '' : `:${values.count}`}` }),
    };
    return originalLoad.call(this, request, parent, isMain);
  };

  const { ChannelPickerModal } = require(join(outputDirectory, 'components/ChannelPickerModal.js'));
  const pickerSource = source({ new_count: 12 });
  let pickerTree;
  await act(async () => {
    pickerTree = create(React.createElement(ChannelPickerModal, {
      accessToken: 'token', onClose: () => {}, onSelect: (item) => selectedCalls.push(item),
      onToggleHome: (item, include) => toggleCalls.push([item, include]), pendingHomeSubscriptionIds: new Set(),
      preferredFolder: null, selectedSourceId: null, sources: [pickerSource], visible: true,
    }));
  });
  const checkbox = pickerTree.root.find((node) => node.type === 'Pressable' && node.props.accessibilityRole === 'checkbox');
  await act(async () => { checkbox.props.onPress(); });
  assert.equal(toggleCalls.length, 1, 'the checkbox toggles home inclusion');
  assert.equal(toggleCalls[0][1], false, 'a checked channel toggles off');
  assert.equal(selectedCalls.length, 0, 'the checkbox does not navigate into the channel');
  assert.equal(pickerTree.root.findAllByType('Modal').length, 1, 'the checkbox keeps the picker open');
  await act(async () => {
    pickerTree.root.find((node) => node.type === 'Pressable' && node.props.accessibilityRole === 'button' && String(node.props.accessibilityLabel).startsWith('channelNameWithUpdates')).props.onPress();
  });
  assert.equal(selectedCalls.length, 1, 'the channel body still navigates');

  const { HomeScreen } = require(join(outputDirectory, 'screens/HomeScreen.js'));
  const session = { access_token: 'token', user: { id: 'user-1' } };
  const baseProps = {
    active: true, channelVisitId: 0, chromeProgress: { value: 0 }, onClearSource: () => {},
    onOpenArticle: () => {}, onProfile: () => {}, onSelectSourceId: () => {}, selectedSourceId: null, session,
  };
  sourceItems = [source({ new_count: 5 }), source({ include_in_home: false, new_count: 100, source_id: 'source-2', subscription_id: 'subscription-2' }), source({ new_count: 2, source_id: 'source-3', subscription_id: 'subscription-3' })];
  let homeTree;
  await act(async () => { homeTree = create(React.createElement(HomeScreen, baseProps)); });
  const filterButton = homeTree.root.find((node) => node.type === 'Pressable' && String(node.props.accessibilityLabel).startsWith('filterChannel'));
  assert.equal(filterButton.props.accessibilityLabel, 'filterChannel, 7', 'the home count sums every included channel and excludes hidden channels');
  await act(async () => { filterButton.props.onPress(); await Promise.resolve(); });
  assert.ok(sourcesRefetchCalls >= 2, 'entering home and opening the picker refresh source counts');
  assert.equal(capturedPickerProps.sources.length, 3, 'search and category state do not narrow the count source set');
  await act(async () => {
    capturedPickerProps.onToggleHome(sourceItems[0], false);
    await Promise.resolve();
    await Promise.resolve();
  });
  assert.equal(sourceItems[0].include_in_home, false, 'the checkbox response updates its authoritative field');
  assert.equal(sourceItems[0].new_count, 5, 'the checkbox response cannot overwrite a newer reminder count');
  assert.equal(sourceQueryCancelCalls, 2, 'the checkbox write fences source GETs before and after the mutation');

  sourceItems = [source({ include_in_home: false, new_count: 4 })];
  let resolveFresh;
  feedRefetch = () => new Promise((resolve) => { resolveFresh = resolve; });
  viewedCalls = [];
  await act(async () => { homeTree.update(React.createElement(HomeScreen, { ...baseProps, channelVisitId: 1, selectedSourceId: 'source-1' })); await Promise.resolve(); });
  assert.equal(viewedCalls.length, 0, 'cached feed data cannot clear a reminder before a fresh visit load succeeds');
  await act(async () => { resolveFresh({ data: { pages: [{ channel_update_token: 'token-1', items: [], next_cursor: null }] }, isError: false }); await Promise.resolve(); await Promise.resolve(); });
  assert.equal(viewedCalls.length, 1, 'a successful active source visit confirms its snapshot');
  assert.equal(viewedCalls[0][2], 'token-1');
  assert.equal(sourceItems[0].include_in_home, false, 'the viewed response cannot overwrite the checkbox field');
  assert.equal(sourceItems[0].new_count, 0, 'the viewed response clears only the reminder count');
  assert.equal(sourceQueryCancelCalls, 4, 'the viewed write fences source GETs before and after the mutation');

  const refetchesAfterSuccess = sourcesRefetchCalls;
  await act(async () => { homeTree.update(React.createElement(HomeScreen, { ...baseProps, active: false, channelVisitId: 1, selectedSourceId: 'source-1' })); });
  await act(async () => { homeTree.update(React.createElement(HomeScreen, { ...baseProps, active: true, channelVisitId: 1, selectedSourceId: 'source-1' })); await Promise.resolve(); });
  assert.equal(viewedCalls.length, 1, 'returning from an article does not reconfirm the same channel visit');
  assert.ok(sourcesRefetchCalls > refetchesAfterSuccess, 'returning home refreshes visible reminder counts');

  feedRefetch = async () => ({ data: undefined, isError: true });
  await act(async () => { homeTree.update(React.createElement(HomeScreen, { ...baseProps, channelVisitId: 2, selectedSourceId: 'source-1' })); await Promise.resolve(); await Promise.resolve(); });
  assert.equal(viewedCalls.length, 1, 'a failed source load does not clear the reminder');

  let resolveStale;
  feedRefetch = () => new Promise((resolve) => { resolveStale = resolve; });
  const staleRuntime = currentRuntime;
  await act(async () => { homeTree.update(React.createElement(HomeScreen, { ...baseProps, channelVisitId: 3, selectedSourceId: 'source-1' })); await Promise.resolve(); });
  currentRuntime = { generation: 2, identity: { server_id: 'server-2' } };
  await act(async () => { homeTree.update(React.createElement(HomeScreen, { ...baseProps, channelVisitId: 3, selectedSourceId: 'source-1' })); });
  await act(async () => { resolveStale({ data: { pages: [{ channel_update_token: 'stale-token', items: [], next_cursor: null }] }, isError: false }); await Promise.resolve(); await Promise.resolve(); });
  assert.equal(viewedCalls.length, 1, 'a response from a previous runtime cannot clear reminders on the next server');
  assert.notEqual(currentRuntime, staleRuntime);

  await act(async () => { pickerTree.unmount(); homeTree.unmount(); });
  console.log('channel update behavior checks passed');
} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}
