const assert = require('node:assert/strict');
const fs = require('node:fs');
const Module = require('node:module');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const ts = require(path.join(root, 'node_modules/typescript'));
const React = require(path.join(root, 'node_modules/react'));
const { act, create } = require(path.join(root, 'node_modules/react-test-renderer'));
const { QueryClient, QueryObserver } = Module.createRequire(path.join(root, 'package.json'))('@tanstack/react-query');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.requestAnimationFrame = (callback) => { callback(); return 1; };
globalThis.cancelAnimationFrame = () => {};

const originalLoad = Module._load;
Module._extensions['.ts'] = Module._extensions['.tsx'] = (mod, filename) => {
  const source = fs.readFileSync(filename, 'utf8');
  mod._compile(ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, filename);
};

const scrollCalls = [];
const FlatList = React.forwardRef((props, ref) => {
  React.useImperativeHandle(ref, () => ({
    scrollToIndex: (value) => scrollCalls.push(['index', value]),
    scrollToOffset: (value) => scrollCalls.push(['offset', value]),
  }));
  return React.createElement('FlatList', props);
});
const View = ({ children, ...props }) => React.createElement('View', props, children);
const ScrollView = React.forwardRef(({ children, ...props }, ref) => {
  React.useImperativeHandle(ref, () => ({ scrollTo() {} }));
  return React.createElement('ScrollView', props, children);
});
const PagerView = React.forwardRef(({ children, ...props }, ref) => {
  React.useImperativeHandle(ref, () => ({ setPage() {} }));
  return React.createElement('PagerView', props, children);
});
const screenMocks = {};
for (const name of ['HomeScreen', 'RankingsScreen', 'SavedScreen', 'SourcesScreen', 'ProfileScreen']) {
  screenMocks[name] = function ScreenMock(props) {
    return props.active ? React.createElement('HeavyScreen', { name }) : null;
  };
}
function ArticleReaderScreen(props) { return React.createElement('ArticleReaderScreen', props); }
function RankingPreviewScreen(props) { return React.createElement('RankingPreviewScreen', props); }
function MotionReaderTabBar(props) { return React.createElement('MotionReaderTabBar', props); }

Module._load = function load(request, parent, isMain) {
  if (request === 'react-native') return {
    BackHandler: { addEventListener: () => ({ remove() {} }) },
    FlatList,
    ScrollView,
    StyleSheet: { create: (styles) => styles },
    View,
  };
  if (request === 'react-native-pager-view') return { __esModule: true, default: PagerView };
  if (request === 'react-native-reanimated') return {
    useAnimatedScrollHandler: (handlers) => handlers,
    useComposedEventHandler: (handlers) => (event) => handlers.filter(Boolean).forEach((handler) => {
      if (typeof handler === 'function') handler(event);
      else handler.onScroll?.(event);
    }),
    useSharedValue: (value) => React.useRef({ value }).current,
  };
  for (const [name, component] of Object.entries(screenMocks)) {
    if (request === `../screens/${name}`) return { [name]: component };
  }
  if (request === '../screens/ArticleReaderScreen') return { ArticleReaderScreen };
  if (request === '../screens/RankingPreviewScreen') return { RankingPreviewScreen };
  if (request === './ReaderTabBar') return { MotionReaderTabBar };
  if (request === '../domain/readerHtml') return { buildReaderHtml: () => '' };
  if (request === '../ui/tokens') return { colors: { background: '#fff' } };
  return originalLoad.call(this, request, parent, isMain);
};

(async () => {
  const {
    clearMissingListPosition,
    rememberListPosition,
  } = require(path.join(root, 'domain/listMemory.ts'));
  const registry = new Map();
  for (let index = 0; index < 15; index += 1) {
    rememberListPosition(registry, `scope-${index}`, { firstVisibleId: `${index}`, offset: index }, 12);
  }
  assert.equal(registry.size, 12, 'position registry is bounded');
  assert.equal(registry.has('scope-0'), false, 'oldest scope position is evicted');
  registry.set('missing', { firstVisibleId: 'gone', offset: 400 });
  assert.equal(clearMissingListPosition(registry, 'missing', ['current']), null);
  assert.equal(registry.has('missing'), false, 'missing anchor clears stale position');

  const {
    protectsQueryKeys,
    removeObsoleteQueries,
    trimInactiveInfiniteQuery,
    trimInactiveInfiniteQueryWhenIdle,
  } = require(path.join(root, 'state/queryLifecycle.ts'));
  const client = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity, retry: false } } });
  const feedPrefix = ['reader', 'server', 'user', 'feed'];
  const currentKey = [...feedPrefix, 'current'];
  const oldKey = [...feedPrefix, 'old'];
  const activeKey = [...feedPrefix, 'active'];
  const pages = Array.from({ length: 6 }, (_, page) => ({
    items: Array.from({ length: 2 }, (_, item) => ({ id: `${page}-${item}` })),
    next_cursor: page === 5 ? null : `cursor-${page + 1}`,
  }));
  client.setQueryData(currentKey, { pages, pageParams: [null, 'p1', 'p2', 'p3', 'p4', 'p5'] }, { updatedAt: 12345 });
  assert.equal(trimInactiveInfiniteQuery({
    anchorId: '2-0',
    getItemId: (item) => item.id,
    queryClient: client,
    queryKey: currentKey,
  }), true);
  const trimmed = client.getQueryData(currentKey);
  assert.equal(trimmed.pages.length, 4, 'anchor page plus one tail buffer is retained');
  assert.deepEqual(trimmed.pageParams, [null, 'p1', 'p2', 'p3'], 'page params stay aligned');
  assert.equal(client.getQueryState(currentKey).dataUpdatedAt, 12345, 'trimming preserves freshness timestamp');

  let delayedTrimListener;
  let delayedTrimUnsubscribed = 0;
  const delayedQuery = { getObserversCount: () => 0, state: { fetchStatus: 'fetching' } };
  const delayedCache = {
    find: () => delayedQuery,
    subscribe: (listener) => {
      delayedTrimListener = listener;
      return () => { delayedTrimUnsubscribed += 1; };
    },
  };
  trimInactiveInfiniteQueryWhenIdle({
    anchorId: 'anchor',
    getItemId: (item) => item.id,
    queryClient: { getQueryCache: () => delayedCache },
    queryKey: ['delayed'],
  });
  delayedQuery.getObserversCount = () => 1;
  delayedTrimListener();
  assert.equal(delayedTrimUnsubscribed, 1, 'a returning observer cancels and unsubscribes delayed trimming');

  client.setQueryData(oldKey, { value: 'old' });
  client.setQueryData(activeKey, { value: 'active' });
  const observer = new QueryObserver(client, { queryKey: activeKey, queryFn: async () => ({ value: 'active' }) });
  const unsubscribe = observer.subscribe(() => {});
  await removeObsoleteQueries({
    isProtected: protectsQueryKeys([currentKey]),
    prefix: feedPrefix,
    queryClient: client,
  });
  assert.equal(client.getQueryData(oldKey), undefined, 'obsolete inactive query is removed');
  assert.ok(client.getQueryData(currentKey), 'protected query survives cleanup');
  assert.ok(client.getQueryData(activeKey), 'observed query survives cleanup');
  unsubscribe();
  await removeObsoleteQueries({ isProtected: protectsQueryKeys([currentKey]), prefix: feedPrefix, queryClient: client });
  assert.equal(client.getQueryData(activeKey), undefined, 'formerly observed obsolete query is removed after unmount');

  const {
    activateXFeedTranslationMemory,
    clearXFeedTranslationMemory,
    readXFeedTranslationMemory,
    writeXFeedTranslationMemory,
  } = require(path.join(root, 'domain/xFeedTranslationMemory.ts'));
  const route = { engineFingerprint: 'fp-a', serverId: 'server', targetLocale: 'zh-CN', userId: 'user' };
  const result = (id, fingerprint = 'fp-a') => ({
    effective_engine_fingerprint: fingerprint,
    segment_id: id,
    translated_text: `译文-${id}`,
    translation_status: 'succeeded',
  });
  clearXFeedTranslationMemory();
  for (let index = 0; index < 201; index += 1) writeXFeedTranslationMemory(route, `source-${index}`, result(`id-${index}`));
  assert.equal(readXFeedTranslationMemory(route, 'id-0', 'source-0'), null, 'X translation LRU evicts the oldest segment');
  assert.ok(readXFeedTranslationMemory(route, 'id-200', 'source-200'), 'recent X translation remains cached');
  writeXFeedTranslationMemory(route, 'wrong', result('wrong', 'fp-b'));
  assert.equal(readXFeedTranslationMemory(route, 'wrong', 'wrong'), null, 'mismatched engine fingerprint is rejected');
  activateXFeedTranslationMemory({ ...route, userId: 'other' });
  assert.equal(readXFeedTranslationMemory(route, 'id-200', 'source-200'), null, 'account transition clears process-memory translations');

  const { useListPositionMemory } = require(path.join(root, 'hooks/useListPositionMemory.ts'));
  const positionRegistry = new Map([['feed', { firstVisibleId: 'B', offset: 120 }]]);
  let hook;
  function Harness({ active, items }) {
    hook = useListPositionMemory({
      active,
      itemId: (item) => item.id,
      items,
      memoryKey: 'feed',
      registry: positionRegistry,
    });
    return React.createElement(FlatList, { ...hook, ref: hook.listRef });
  }
  let tree;
  await act(async () => { tree = create(React.createElement(Harness, { active: true, items: [{ id: 'A' }, { id: 'B' }, { id: 'C' }] })); });
  assert.deepEqual(scrollCalls[0], ['offset', { animated: false, offset: 120 }], 'restoration starts from saved offset');
  await act(async () => hook.onViewableItemsChanged({ viewableItems: [{ isViewable: true, item: { id: 'A' } }] }));
  assert.deepEqual(scrollCalls[1], ['index', { animated: false, index: 1, viewPosition: 0 }], 'anchor mismatch falls back to item index');
  await act(async () => hook.onScrollBeginDrag());
  const callCountAfterDrag = scrollCalls.length;
  await act(async () => tree.update(React.createElement(Harness, { active: true, items: [{ id: 'A' }, { id: 'B' }, { id: 'C' }, { id: 'D' }] })));
  assert.equal(scrollCalls.length, callCountAfterDrag, 'pagination changes never restart restoration after user drag');
  await act(async () => hook.onScroll({
    contentOffset: { y: 333 },
    contentSize: { height: 1000 },
    layoutMeasurement: { height: 100 },
  }));
  await act(async () => tree.unmount());
  assert.equal(positionRegistry.get('feed').offset, 333, 'unmount captures the UI-thread offset even before momentum ends');
  await act(async () => { tree = create(React.createElement(Harness, { active: false, items: [{ id: 'A' }, { id: 'B' }, { id: 'C' }] })); });
  await act(async () => hook.onViewableItemsChanged({ viewableItems: [{ isViewable: true, item: { id: 'A' } }] }));
  await act(async () => tree.unmount());
  assert.deepEqual(positionRegistry.get('feed'), { firstVisibleId: 'B', offset: 333 }, 'an inactive adjacent page cannot overwrite saved position');
  const callsBeforeReturn = scrollCalls.length;
  await act(async () => { tree = create(React.createElement(Harness, { active: true, items: [{ id: 'A' }, { id: 'B' }, { id: 'C' }] })); });
  assert.deepEqual(scrollCalls[callsBeforeReturn], ['offset', { animated: false, offset: 333 }], 'returning to the page restores the position kept through inactive neighbor mounting');
  await act(async () => tree.unmount());

  const twoPageRegistry = new Map([
    ['A', { firstVisibleId: 'A-0', offset: 0 }],
    ['B', { firstVisibleId: 'B-0', offset: 0 }],
  ]);
  const pageHooks = {};
  function PositionPage({ active, memoryKey }) {
    const pageHook = useListPositionMemory({
      active,
      itemId: (item) => item.id,
      items: [{ id: `${memoryKey}-0` }],
      memoryKey,
      registry: twoPageRegistry,
    });
    pageHooks[memoryKey] = pageHook;
    return React.createElement(FlatList, { ...pageHook, ref: pageHook.listRef });
  }
  function TwoPages({ selected }) {
    return React.createElement(React.Fragment, null,
      React.createElement(PositionPage, { active: selected === 'A', memoryKey: 'A' }),
      React.createElement(PositionPage, { active: selected === 'B', memoryKey: 'B' }),
    );
  }
  const scrollEvent = (y) => ({
    contentOffset: { y },
    contentSize: { height: 1000 },
    layoutMeasurement: { height: 100 },
  });
  await act(async () => { tree = create(React.createElement(TwoPages, { selected: 'A' })); });
  await act(async () => pageHooks.A.onScroll(scrollEvent(100)));
  await act(async () => tree.update(React.createElement(TwoPages, { selected: 'B' })));
  await act(async () => pageHooks.B.onScroll(scrollEvent(222)));
  await act(async () => tree.update(React.createElement(TwoPages, { selected: 'A' })));
  assert.equal(twoPageRegistry.get('A').offset, 100, 'switching back keeps page A own UI-thread offset');
  assert.equal(twoPageRegistry.get('B').offset, 222, 'switching back saves page B own UI-thread offset');
  await act(async () => tree.update(React.createElement(TwoPages, { selected: 'B' })));
  assert.equal(twoPageRegistry.get('A').offset, 100, 'bidirectional switching does not copy page B offset into page A');
  assert.equal(twoPageRegistry.get('B').offset, 222, 'bidirectional switching restores page B without page A contamination');
  await act(async () => tree.unmount());

  const { MainAppShell } = require(path.join(root, 'components/MainAppShell.tsx'));
  const session = { access_token: 'token', user: { id: 'user' } };
  await act(async () => { tree = create(React.createElement(MainAppShell, { authClient: {}, onChangeServer() {}, onLogout: async () => {}, session })); });
  assert.equal(tree.root.findAllByType('HeavyScreen').length, 1, 'only the current tab mounts a heavy screen');
  await act(async () => tree.root.findByType('MotionReaderTabBar').props.onChange('explore'));
  await act(async () => tree.root.findByType('MotionReaderTabBar').props.onChange('saved'));
  await act(async () => tree.root.findByType('MotionReaderTabBar').props.onChange('sources'));
  assert.equal(tree.root.findAllByType('HeavyScreen').length, 1, 'visited tabs retain controllers without retaining heavy content');
  await act(async () => tree.root.findByType(screenMocks.SourcesScreen).props.onOpenSource({ source_id: 'source' }));
  const home = tree.root.findByType(screenMocks.HomeScreen);
  await act(async () => home.props.onOpenArticle({ content_id: 'item', external_url: 'https://example.test', ranking_kind: null }));
  assert.equal(tree.root.findAllByType('HeavyScreen').length, 0, 'reader overlay unloads the main tab heavy surface');
  assert.equal(tree.root.findAllByType('ArticleReaderScreen').length, 1);
  await act(async () => tree.root.findByType('ArticleReaderScreen').props.onBack());
  assert.equal(tree.root.findAllByType('HeavyScreen').length, 1, 'closing the reader restores the selected tab controller');
  await act(async () => tree.unmount());

  const { CategoryPager } = require(path.join(root, 'components/CategoryPager.tsx'));
  const rendered = [];
  await act(async () => { tree = create(React.createElement(CategoryPager, {
    onSelect() {}, options: ['A', 'B', 'C', 'D', 'E'], pageStyle: {}, selected: 'C', style: {},
    renderPage: (value) => { rendered.push(value); return React.createElement('Page', { value }); },
  })); });
  assert.deepEqual(rendered, ['B', 'C', 'D'], 'native pager mounts only center and adjacent heavy pages');
  rendered.length = 0;
  await act(async () => tree.root.findByType('PagerView').props.onPageSelected({ nativeEvent: { position: 3 } }));
  assert.deepEqual(rendered, ['C', 'D', 'E'], 'native swipe advances the render window before parent selection catches up');
  await act(async () => tree.unmount());

  const { CategoryPager: WebCategoryPager } = require(path.join(root, 'components/CategoryPager.web.tsx'));
  const webRendered = [];
  await act(async () => { tree = create(React.createElement(WebCategoryPager, {
    onSelect() {}, options: ['A', 'B', 'C', 'D', 'E'], pageStyle: {}, selected: 'C', style: {},
    renderPage: (value) => { webRendered.push(value); return React.createElement('Page', { value }); },
  })); });
  await act(async () => tree.root.findByType('ScrollView').props.onLayout({ nativeEvent: { layout: { width: 100 } } }));
  webRendered.length = 0;
  await act(async () => tree.root.findByType('ScrollView').props.onScroll({ nativeEvent: { contentOffset: { x: 400 } } }));
  assert.deepEqual(webRendered, ['D', 'E'], 'web pager moves its render window while a swipe crosses pages');
  await act(async () => tree.unmount());

  client.clear();
  console.log('PASS: page lifecycle memory, query cleanup, X LRU, restoration and lazy pager');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
}).finally(() => {
  Module._load = originalLoad;
});
