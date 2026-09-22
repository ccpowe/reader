const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const ts = require(path.join(root, 'node_modules/typescript'));
const React = require(path.join(root, 'node_modules/react'));
const { act, create } = require(path.join(root, 'node_modules/react-test-renderer'));
const { QueryClient, QueryClientProvider } = Module.createRequire(path.join(root, 'package.json'))('@tanstack/react-query');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const host = name => ({ children, ...props }) => React.createElement(name, props, children);
const requests = [];
let sourceItems = [];
let pickImage = async () => ({ canceled: true });
let avatarUploads = 0;
globalThis.fetch = async () => ({ arrayBuffer: async () => new ArrayBuffer(128) });
let failInvalidation = false;
const scrollCalls = [];
const ScrollView = React.forwardRef(({ children, ...props }, ref) => {
  React.useImperativeHandle(ref, () => ({ scrollTo: value => scrollCalls.push(value) }));
  return React.createElement('ScrollView', props, children);
});
const runtime = { generation: 1, identity: { server_id: 'test' } };
const originalLoad = Module._load;
Module._extensions['.ts'] = Module._extensions['.tsx'] = (mod, filename) => {
  let source = fs.readFileSync(filename, 'utf8');
  // Only alter the in-memory audit copy to expose the actual private component.
  if (filename.endsWith('/screens/ProfileScreen.tsx')) source += '\nexport { ProfileEditorSheet };\n';
  mod._compile(ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText, filename);
};
Module._load = function(request, parent, isMain) {
  if (request === 'react-native') return { ScrollView, ActivityIndicator: host('ActivityIndicator'), Alert: { alert() {} }, Image: host('Image'), Pressable: host('Pressable'), StyleSheet: { create: x => x }, Switch: host('Switch'), Text: host('Text'), TextInput: host('TextInput'), View: host('View') };
  if (request === 'react-native-reanimated') return {
    __esModule: true,
    default: { FlatList: host('FlatList'), ScrollView: host('ScrollView'), View: host('AnimatedView') },
    useAnimatedScrollHandler: handlers => handlers,
    useComposedEventHandler: handlers => event => handlers.filter(Boolean).forEach(handler => {
      if (typeof handler === 'function') handler(event);
      else handler.onScroll?.(event);
    }),
    useSharedValue: value => ({ value }),
  };
  if (request === '@expo/vector-icons') return { Feather: host('Feather'), MaterialCommunityIcons: host('Icon') };
  if (request === 'expo-image-picker') return { launchImageLibraryAsync: () => pickImage() };
  if (request === '../components/PageHeader') return { PageHeaderContent: host('PageHeaderContent') };
  if (request === '../components/FolderFilterControls') return { FOLDER_FILTER_CONTENT_GAP: 0, FOLDER_FILTER_CONTROLS_HEIGHT: 0, FolderFilterControls: host('FolderFilterControls') };
  if (request === '../components/BottomSheetModal') return { BottomSheetModal: host('BottomSheetModal') };
  if (request === '../lib/connection/runtime') return { getActiveRuntime: () => runtime };
  if (request === '../lib/connection/guard') return { isRuntimeContextCurrent: () => true };
  if (request === '../lib/connection') return { getConnectionGeneration: () => 1, isRuntimeContextCurrent: () => true, captureRuntimeContext: () => ({ runtime, serverId: 'test', generation: 1 }) };
  if (request === '../lib/connection/react') return { useReaderRuntime: () => runtime };
  if (request === '../lib/api') return {
    uploadAvatar: async () => { avatarUploads += 1; throw new Error('unexpected stale upload'); },
    setSavedContent: (...args) => new Promise((resolve, reject) => requests.push({ args, resolve, reject })),
  };
  if (request === '../state/invalidation') return { invalidateAfterSavedMutation: async () => { if (failInvalidation) throw new Error('refresh failed'); } };
  if (request.endsWith('/useSources')) return { useSources: () => ({ items: sourceItems, isSuccess: true }) };
  if (request.endsWith('/useTitleTranslationConvergence')) return { useTitleTranslationConvergence: () => ({}) };
  if (request.endsWith('/useInboxFeed')) return { useInboxFeed: () => ({ items: [], titleTranslation: {} }), prefetchInboxFeed() {} };
  if (request === '../components/CategoryPager') return { CategoryPager: ({ options, renderPage }) => React.createElement(React.Fragment, null, options.map((option, i) => React.createElement(React.Fragment, { key: i }, renderPage(option)))) };
  for (const name of ['ChannelPickerModal', 'Chip', 'SourceAvatar', 'TitleTranslationNotice']) {
    if (request === '../components/' + name) return { [name]: host(name) };
  }
  if (request === '../components/FeedCards') return { ArticleCard: host('ArticleCard') };
  if (request === '../components/FeedbackState') return { EmptyState: host('Empty'), ErrorState: host('Error'), listFeedbackStyles: { content: {}, state: {} }, LoadingBlock: host('Loading') };
  if (request.endsWith('/useTranslationPreference')) return { useTranslationPreference: () => ({ targetLocale: 'zh-CN', isSuccess: true }) };
  if (request === '../hooks/useCollapsingChrome') return { useChromeStyle: () => ({}), useCollapsingChrome: () => ({ onScroll() {}, revealChrome() {} }) };
  if (request === '../ui/layout') return { PAGE_HEADER_HEIGHT: 80, SCREEN_LIST_BOTTOM_PADDING: 80 };
  return originalLoad.apply(this, arguments);
};
(async () => {
  const { removeCachedSavedItem, restoreSavedCache } = require(path.join(root, 'state/cacheUpdates.ts'));
  const { readerQueryKeys } = require(path.join(root, 'state/queryClient.ts'));
  const client = new QueryClient({ defaultOptions: { queries: { gcTime: Infinity, retry: false } } });
  const key = readerQueryKeys.saved('user', 'zh-CN', '', 'test');
  const feedKey = readerQueryKeys.feed('user', 'all', 'zh-CN', 'test');
  const page = ids => ({ pageParams: [null], pages: [{ items: ids.map(content_id => ({ content_id, is_saved: true })), next_cursor: null }] });
  const ids = () => client.getQueryData(key).pages.flatMap(p => p.items.map(i => i.content_id));
  client.setQueryData(key, page(['A', 'B']));
  const previousA = removeCachedSavedItem(client, 'user', 'test', 'A');
  removeCachedSavedItem(client, 'user', 'test', 'B');
  client.setQueryData(key, page(['A', 'C']));
  restoreSavedCache(client, 'user', 'test', previousA);
  assert.deepEqual(ids(), ['A', 'C'], 'rollback must neither resurrect B nor remove a new C');
  client.setQueryData(key, page(['C']));
  restoreSavedCache(client, 'user', 'test', previousA);
  assert.deepEqual(ids(), ['A', 'C'], 'rollback restores only its missing item');

  const { useSavedContent } = require(path.join(root, 'hooks/useSavedContent.ts'));
  const { HomeScreen } = require(path.join(root, 'screens/HomeScreen.tsx'));
  const session = { access_token: 'a', user: { id: 'user' } };
  let saved;
  function SavedHarness() { saved = useSavedContent(session, false); return null; }
  const homeProps = { active: true, chromeProgress: { value: 0 }, onClearSource() {}, onOpenArticle() {}, onSelectSourceId() {}, selectedSourceId: null, session };
  let mutationTree;
  await act(async () => { mutationTree = create(React.createElement(QueryClientProvider, { client }, React.createElement(SavedHarness), React.createElement(HomeScreen, homeProps))); });
  const toggle = item => mutationTree.root.find(node => typeof node.type === 'function' && node.type.name === 'InboxFeedPage').props.onToggleSave(item);
  const A = { content_id: 'A', is_saved: true };
  const B = { content_id: 'B', is_saved: true };
  client.setQueryData(key, page(['A', 'B']));
  client.setQueryData(feedKey, page(['A', 'B']));
  let removeA, removeB;
  await act(async () => {
    removeA = saved.removeSaved(A);
    removeB = saved.removeSaved(B);
    await toggle(A); // Same item from another mounted surface cannot start a second write.
  });
  assert.equal(requests.length, 2);
  assert.deepEqual(ids(), []);
  await act(async () => { requests[1].resolve(); await removeB; });
  client.setQueryData(key, page(['A', 'C'])); // B's authoritative refresh, while A remains pending.
  await act(async () => { requests[0].reject(new Error('delete failed')); await removeA; });
  assert.deepEqual(ids(), ['A', 'C']);
  assert.equal(client.getQueryData(feedKey).pages[0].items[0].is_saved, true);
  assert.equal(client.getQueryData(feedKey).pages[0].items[1].is_saved, false);

  failInvalidation = true;
  let committedDelete;
  await act(async () => { committedDelete = saved.removeSaved(A); });
  await act(async () => { requests[2].resolve(); await committedDelete; });
  assert.deepEqual(ids(), ['C'], 'refetch failure does not undo committed delete');
  assert.equal(client.getQueryData(feedKey).pages[0].items[0].is_saved, false);
  let addA;
  await act(async () => { addA = toggle({ ...A, is_saved: false }); await toggle(A); });
  assert.equal(requests.length, 4, 'same Home item pending tap is suppressed');
  await act(async () => { requests[3].resolve(); await addA; });
  assert.equal(client.getQueryData(feedKey).pages[0].items[0].is_saved, true, 'Home committed add survives refetch failure');
  failInvalidation = false;
  let failedToggle;
  await act(async () => { failedToggle = toggle(A); });
  await act(async () => { requests[4].reject(new Error('write failed')); await failedToggle; });
  assert.equal(client.getQueryData(feedKey).pages[0].items[0].is_saved, true, 'Home failed write releases lock and restores prior flag');
  await act(async () => mutationTree.unmount());
  client.clear();
  console.log('PASS F5: real mounted Home/Saved shared pending boundary, independent rollback and committed-write refetch failures');

  const { SavedScreen } = require(path.join(root, 'screens/SavedScreen.tsx'));
  const { i18n } = require(path.join(root, 'i18n/index.ts'));
  sourceItems = [{ source_id: 'a', folder_name: '阿尔法' }, { source_id: 'z', folder_name: 'Z Notes' }];
  let savedTree;
  const originalCompare = String.prototype.localeCompare;
  try {
    await act(async () => { savedTree = create(React.createElement(QueryClientProvider, { client }, React.createElement(SavedScreen, { active: true, chromeProgress: { value: 0 }, onOpenArticle() {}, session }))); });
    const categoryPager = () => savedTree.root.find(node => typeof node.type === 'function' && node.type.name === 'CategoryPager');
    const originalOptions = [...categoryPager().props.options];
    await act(async () => categoryPager().props.onSelect('阿尔法'));
    // Reproduce Android/Hermes changing its collation when OS preferences change.
    // This must not reorder existing index-keyed category pages on a translation render.
    String.prototype.localeCompare = function(other, ...args) { return -originalCompare.call(this, other, ...args); };
    await act(async () => { await i18n.changeLanguage('en'); });
    assert.deepEqual(categoryPager().props.options, originalOptions, 'OS-language changes retain mounted Saved category indices');
    assert.equal(categoryPager().props.selected, '阿尔法', 'the selected category survives the language change');
    sourceItems = sourceItems.map(item => ({ ...item, sync_phase: 'scanning' }));
    await act(async () => savedTree.update(React.createElement(QueryClientProvider, { client }, React.createElement(SavedScreen, { active: true, chromeProgress: { value: 0 }, onOpenArticle() {}, session }))));
    assert.deepEqual(categoryPager().props.options, originalOptions, 'source-status refreshes cannot undo the stable category order');
    sourceItems = [...sourceItems, { source_id: 'new', folder_name: 'New category' }];
    await act(async () => savedTree.update(React.createElement(QueryClientProvider, { client }, React.createElement(SavedScreen, { active: true, chromeProgress: { value: 0 }, onOpenArticle() {}, session }))));
    assert.ok(categoryPager().props.options.includes('New category'), 'actual category additions still update the pager');
  } finally {
    String.prototype.localeCompare = originalCompare;
    await act(async () => { savedTree?.unmount(); await i18n.changeLanguage('zh-CN'); });
    sourceItems = [];
    client.clear();
  }
  console.log('PASS: mounted Saved categories survive an OS collation change');

  const { ProfileEditorSheet } = require(path.join(root, 'screens/ProfileScreen.tsx'));
  let props = { client: {}, email: 'reader@example.com', generation: 1, onClose() {}, onSaved() {}, profile: null, runtime, session, visible: true };
  let tree;
  await act(async () => { tree = create(React.createElement(ProfileEditorSheet, props)); });
  const input = label => tree.root.findAll(node => node.type === 'TextInput' && node.props.accessibilityLabel === label)[0];
  const update = async patch => { props = { ...props, ...patch }; await act(async () => tree.update(React.createElement(ProfileEditorSheet, props))); };
  await act(async () => { input('昵称').props.onChangeText('My unsaved nickname'); input('邮箱').props.onChangeText('new@example.com'); });
  await update({ profile: { id: 'user', display_name: 'Server name', avatar_url: 'https://cdn.test/late.png' } });
  assert.equal(input('昵称').props.value, 'My unsaved nickname');
  assert.equal(input('邮箱').props.value, 'new@example.com');
  assert.equal(tree.root.findByType('Image').props.source.uri, 'https://cdn.test/late.png', 'untouched avatar still loads');
  await act(async () => tree.root.findAll(node => node.type === 'Pressable' && node.props.accessibilityLabel === '移除头像')[0].props.onPress());
  await update({ profile: { ...props.profile, avatar_url: 'https://cdn.test/new.png' } });
  assert.equal(tree.root.findAllByType('Image').length, 0, 'removed avatar is a dirty field');
  await update({ visible: false });
  await update({ visible: true });
  assert.equal(input('昵称').props.value, 'Server name', 'reopen resets abandoned draft');
  assert.equal(input('邮箱').props.value, 'reader@example.com');
  await act(async () => input('昵称').props.onChangeText('User A draft'));
  await update({ session: { ...session, user: { id: 'other' } }, email: 'other@example.com', profile: { id: 'other', display_name: 'Other', avatar_url: null } });
  assert.equal(input('昵称').props.value, 'Other', 'account change resets draft');
  assert.equal(input('邮箱').props.value, 'other@example.com');
  await update({ visible: false, profile: null });
  await update({ visible: true });
  await act(async () => input('邮箱').props.onChangeText('draft@example.com'));
  await update({ profile: { id: 'other', display_name: 'Late other', avatar_url: null } });
  assert.equal(input('昵称').props.value, 'Late other', 'untouched name loads despite edited email');
  assert.equal(input('邮箱').props.value, 'draft@example.com');
  let resolvePicker;
  pickImage = () => new Promise(resolve => { resolvePicker = resolve; });
  await act(async () => tree.root.findAll(node => node.type === 'Pressable' && node.props.accessibilityLabel === '从相册选择头像')[0].props.onPress());
  await update({ visible: false });
  await update({ visible: true });
  await act(async () => resolvePicker({ canceled: false, assets: [{ uri: 'file:///old.png', mimeType: 'image/png', fileName: 'old.png' }] }));
  assert.equal(avatarUploads, 0, 'picker from closed editor cannot start uploading into reopened editor');
  assert.equal(tree.root.findAllByType('Image').length, 0);
  await act(async () => tree.unmount());
  console.log('PASS F6: mounted editor preserves dirty fields, fills untouched fields, and resets reopened/account-scoped drafts');

  const { CategoryPager } = require(path.join(root, 'components/CategoryPager.web.tsx'));
  let selected = 'second';
  const selections = [];
  const pagerProps = () => ({ options: ['first', 'second'], selected, onSelect: value => { selected = value; selections.push(value); }, renderPage: value => React.createElement('Text', null, value) });
  await act(async () => { tree = create(React.createElement(CategoryPager, pagerProps())); });
  const layout = async width => act(async () => tree.root.findByType('ScrollView').props.onLayout({ nativeEvent: { layout: { width } } }));
  const scrollEnd = async x => act(async () => tree.root.findByType('ScrollView').props.onMomentumScrollEnd({ nativeEvent: { contentOffset: { x } } }));
  await layout(390);
  assert.deepEqual(scrollCalls.at(-1), { animated: false, x: 390 }, 'first nonzero page is positioned after measurement');
  await scrollEnd(390);
  assert.equal(selections.length, 0, 'programmatic scroll does not echo selection');
  await layout(780);
  assert.deepEqual(scrollCalls.at(-1), { animated: false, x: 780 }, 'resize repositions selected page without animation');
  await scrollEnd(780);
  assert.equal(selections.length, 0);
  await scrollEnd(0);
  assert.deepEqual(selections, ['first']);
  const beforeSwipeUpdate = scrollCalls.length;
  await act(async () => tree.update(React.createElement(CategoryPager, pagerProps())));
  assert.equal(scrollCalls.length, beforeSwipeUpdate, 'selection update after swipe does not issue another scroll');
  selected = 'second';
  await act(async () => tree.update(React.createElement(CategoryPager, pagerProps())));
  assert.deepEqual(scrollCalls.at(-1), { animated: true, x: 780 }, 'tab change still animates');
  await act(async () => tree.unmount());
  console.log('PASS F7: mounted Web pager first measurement, resize, swipe and selection-loop boundaries');
})().catch(error => { console.error(error); process.exitCode = 1; });
