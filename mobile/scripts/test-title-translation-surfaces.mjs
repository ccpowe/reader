import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import Module from 'node:module';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-title-surfaces-'));
const require = createRequire(import.meta.url);
const React = require('react');
const { act, create } = require('react-test-renderer');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();

const timedOut = {
  retryTimedOut: () => { retryCalls += 1; },
  timedOutContentIds: ['content-1'],
  timeoutMessage: '翻译标题超时，请点击重试。',
};
const timedOutTitles = Object.assign([], timedOut);
let retryCalls = 0;

try {
  execFileSync(
    process.execPath,
    [
      join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
      '--ignoreConfig',
      'components/TitleTranslationNotice.tsx',
      'screens/HomeScreen.tsx',
      'screens/SavedScreen.tsx',
      'screens/ArticleReaderScreen.tsx',
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
  const ScrollView = host('ScrollView');
  const TextInput = host('TextInput');
  const ActivityIndicator = host('ActivityIndicator');
  const Image = host('Image');
  const FlatList = ({ data = [], renderItem, ListEmptyComponent, ListFooterComponent, ListHeaderComponent, ...props }) => React.createElement(
    'FlatList',
    props,
    ListHeaderComponent,
    data.length === 0 ? ListEmptyComponent : null,
    ...data.map((item, index) => renderItem?.({ item, index, separators: {} })),
    ListFooterComponent,
  );
  const Pressable = host('Pressable');
  const StyleSheet = { create: (styles) => styles };
  const Reanimated = { FlatList, View };
  const originalLoad = Module._load;
  Module._load = function load(request, parent, isMain) {
    if (request === 'react-native') {
      return {
        ActivityIndicator,
        Alert: { alert: () => {} },
        FlatList,
        Image,
        Modal: host('Modal'),
        PanResponder: { create: () => ({ panHandlers: {} }) },
        Linking: { openURL: async () => {} },
        Pressable,
        ScrollView,
        Share: { share: async () => {} },
        StyleSheet,
        Text,
        Keyboard: { addListener: () => ({ remove() {} }) },
      TextInput,
        View,
      };
    }
    if (request === '@expo/vector-icons') return { Feather: host('Feather'), MaterialCommunityIcons: host('MaterialCommunityIcons') };
    if (request === 'react-native-reanimated') return { default: Reanimated, ...Reanimated, useAnimatedStyle: () => ({}), useSharedValue: value => ({ value }) };
    if (request === '@tanstack/react-query') return { useQueryClient: () => ({}) };
    if (request === 'expo-web-browser') return { openBrowserAsync: async () => {} };
    if (request === '../components/TitleTranslationNotice') return originalLoad.call(this, request, parent, isMain);
    if (request === '../components/ChannelPickerModal') return { ChannelPickerModal: () => null };
    if (request === '../components/Chip' || request === './Chip') return { Chip: ({ label, onPress }) => React.createElement(Pressable, { accessibilityRole: 'button', onPress }, React.createElement(Text, null, label)) };
    if (request === '../components/CategoryPager') return { CategoryPager: ({ renderPage, options }) => renderPage(options[0]) };
    if (request === '../components/FeedCards') return {
      ArticleCard: ({ item }) => React.createElement(Text, null, item.title),
      SavedCard: ({ item }) => React.createElement(Text, null, item.title),
    };
    if (request === '../components/FeedbackState') return {
      EmptyState: ({ message }) => React.createElement(Text, null, message),
      ErrorState: ({ message }) => React.createElement(Text, null, message),
      listFeedbackStyles: { content: {}, state: {} },
      LoadingBlock: () => React.createElement(ActivityIndicator),
    };
    if (request === '../components/PageHeader') return {
      IconButton: () => null,
      PageHeaderContent: ({ title }) => React.createElement(Text, null, title),
      ReaderLogo: () => React.createElement(Text, null, 'logo'),
    };
    if (request === '../components/SourceAvatar') return { SourceAvatar: () => React.createElement(View) };
    if (request === '../components/TranslatableWebView') return {
      TranslatableWebView: () => React.createElement(View),
    };
    if (request === '../hooks/useCollapsingChrome') return {
      useChromeStyle: () => ({}),
      useCollapsingChrome: () => ({ onScroll: () => {}, revealChrome: () => {} }),
    };
    if (request === '../hooks/useDebouncedValue') return { useDebouncedValue: (value) => value };
    if (request === '../hooks/useInboxFeed') return {
      prefetchInboxFeed: async () => {},
      useInboxFeed: () => ({
        fetchNextPage: async () => {},
        hasNextPage: false,
        isError: false,
        isFetchingNextPage: false,
        items: [],
        loading: false,
        message: '',
        refetch: async () => {},
        refreshing: false,
        titleTranslation: timedOut,
      }),
    };
    if (request === '../hooks/useSavedContent') return {
      useSavedContent: () => ({
        items: [],
        loading: false,
        message: '',
        removeSaved: async () => {},
        savedQuery: { fetchNextPage: async () => {}, hasNextPage: false, isError: false, isFetchingNextPage: false, refetch: async () => {} },
        sources: [],
        titleTranslation: timedOut,
      }),
    };
    if (request === '../hooks/useSources') return { useSources: () => ({ error: null, isPending: false, isSuccess: true, items: [], loading: false, message: '' }) };
    if (request === '../hooks/useTranslationPreference') return { useTranslationPreference: () => ({ effectiveEngineId: null, enabled: true, isPending: false, targetLocale: 'zh-CN' }) };
    if (request === '../hooks/useTitleTranslationConvergence') return { useTitleTranslationConvergence: () => timedOutTitles };
    if (request === '../lib/connection/react') return { useReaderRuntime: () => ({ generation: 1, identity: { server_id: 'surface-test' } }) };
    if (request === '../lib/connection') return {
      captureRuntimeContext: (runtime) => ({ generation: runtime.generation, runtime, serverId: runtime.identity.server_id }),
      isRuntimeContextCurrent: () => true,
    };
    if (request === '../state/invalidation') return { invalidateAfterSavedMutation: async () => {} };
    if (request === '../state/cacheUpdates') return { setCachedFeedSavedState: () => {} };
    if (request === '../state/queryClient') return { readerQueryKeys: {
      feed: (...args) => ['feed', ...args],
      feedPrefix: (...args) => ['feed-prefix', ...args],
      saved: (...args) => ['saved', ...args],
      savedPrefix: (...args) => ['saved-prefix', ...args],
    } };
    if (request === '../state/queryLifecycle') return {
      protectsQueryKeys: () => () => false,
      removeObsoleteQueries: async () => {},
      trimInactiveInfiniteQueryWhenIdle: () => {},
    };
    if (request === '../lib/api') return {
      displayTitle: (item) => item.title,
      getArticle: async () => null,
      getSavedContentPage: async () => ({ items: [], next_cursor: null }),
      setSavedContent: async () => {},
    };
    if (request === '../domain/feed') return {
      feedScopeCacheKey: () => 'home',
      folderFeedScope: () => ({ kind: 'folder', folder: null }),
      sourceFeedScope: () => ({ kind: 'source', sourceId: 'source-1' }),
    };
    if (request === '../domain/folders') return {
      UNCATEGORIZED_FOLDER: '__uncategorized__',
      folderLabel: (folder) => folder,
      sourceFolder: () => '__uncategorized__',
      sourceFolders: () => [],
    };
    if (request === '../domain/source') return {
      folderLabel: () => '',
      sourceDisplayName: (source) => source.display_name ?? 'source',
      sourceFolder: () => '__uncategorized__',
      sourceHealthLabel: () => '',
      sourceHost: () => 'example.test',
    };
    if (request === '../domain/media') return {
      YOUTUBE_APP_REFERER: 'https://www.youtube.com/',
      youtubeEmbedUrl: () => 'https://youtube.test/embed',
      youtubeVideoId: () => null,
      youtubeWatchUrl: () => 'https://youtube.test/watch',
    };
    if (request === '../domain/webTranslation') return { webTranslationProfileForUrl: () => 'generic', isUrlAllowedForWebTranslationProfile: () => true };
    if (request === '../ui/tokens') return { colors: { background: '#fff', danger: '#f00', surface: '#fff', textPrimary: '#111', textSecondary: '#444', textStrong: '#111', textTertiary: '#666' }, radii: { lg: 10, md: 10, pill: 20, sm: 8 }, spacing: { md: 8, sm: 8 }, touchTarget: 44 };
    return originalLoad.call(this, request, parent, isMain);
  };

  const { HomeScreen } = require(join(outputDirectory, 'screens/HomeScreen.js'));
  const { SavedScreen } = require(join(outputDirectory, 'screens/SavedScreen.js'));
  const { ArticleReaderScreen } = require(join(outputDirectory, 'screens/ArticleReaderScreen.js'));
  const session = { access_token: 'test-token', user: { email: 'reader@example.com', id: 'user-1' } };
  const shared = { active: true, chromeProgress: { value: 0 }, session };
  const noticeButton = (tree) => tree.root.findAll((node) => node.type === 'Pressable' && node.props.accessibilityLabel === '重试翻译标题')[0];
  const noticeText = (tree) => tree.root.findAll((node) => node.props.accessibilityRole === 'alert').map((node) => node.children.join('')).find((value) => value.includes('翻译标题超时'));

  let tree;
  await act(async () => { tree = create(React.createElement(HomeScreen, { ...shared, onOpenArticle: () => {}, onSelectSourceId: () => {}, selectedSourceId: null })); });
  assert.ok(noticeText(tree), 'Home consumes the production title timeout notice');
  await act(async () => { noticeButton(tree).props.onPress(); await Promise.resolve(); });
  assert.equal(retryCalls, 1, 'Home retry is explicit and actionable');

  await act(async () => { tree = create(React.createElement(SavedScreen, { ...shared, onOpenArticle: () => {} })); });
  assert.ok(noticeText(tree), 'Saved consumes the production title timeout notice');
  await act(async () => { noticeButton(tree).props.onPress(); await Promise.resolve(); });
  assert.equal(retryCalls, 2, 'Saved retry is explicit and actionable');

  const article = {
    content_id: 'content-1',
    external_url: 'https://example.test/article',
    fetched_at: '2026-01-01T00:00:00Z',
    is_saved: false,
    source_kind: 'reddit',
    title: 'Original title',
    translation_status: 'pending',
  };
  await act(async () => {
    tree = create(React.createElement(ArticleReaderScreen, {
      item: article,
      onBack: () => {},
      renderReaderHtml: () => '<html />',
      session,
    }));
  });
  assert.ok(noticeText(tree), 'Article consumes the production title timeout notice');
  await act(async () => { noticeButton(tree).props.onPress(); await Promise.resolve(); });
  assert.equal(retryCalls, 3, 'Article retry is explicit and actionable');
} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}

console.log('Mounted Home/Saved/Article title-timeout surfaces passed.');
