import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import Module from 'node:module';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-sources-action-'));
const require = createRequire(import.meta.url);
const React = require('react');
const { act, create } = require('react-test-renderer');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();

let currentRuntime = {
  generation: 1,
  identity: { server_id: 'sources-test', api_base_url: 'https://reader.test' },
};
let removeCalls = 0;
const removeRequests = [];
let addCalls = 0;
const addRequests = [];
let deferNextAdd = false;
const deferredAdds = [];
let nextAddError = null;
let focusCalls = 0;

try {
  execFileSync(
    process.execPath,
    [
      join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
      '--ignoreConfig',
      'lib/connection/errors.ts',
      'components/ActionConfirm.tsx',
      'components/BottomSheetModal.tsx',
      'screens/SourcesScreen.tsx',
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
  const KeyboardAvoidingView = host('KeyboardAvoidingView');
  const ActivityIndicator = host('ActivityIndicator');
  const FlatList = ({ data = [], renderItem, ListEmptyComponent, ListFooterComponent, ListHeaderComponent, ...props }) => React.createElement(
    'FlatList',
    props,
    ListHeaderComponent,
    data.length === 0 ? ListEmptyComponent : null,
    ...data.map((item, index) => renderItem({ item, index, separators: {} })),
    ListFooterComponent,
  );
  const Modal = ({ children, visible, ...props }) => visible
    ? React.createElement('Modal', props, children)
    : null;
  const Pressable = React.forwardRef((props, ref) => {
    React.useImperativeHandle(ref, () => ({ focus: () => { focusCalls += 1; } }), []);
    return React.createElement('Pressable', props, props.children);
  });
  const StyleSheet = { create: (styles) => styles };
  const originalLoad = Module._load;
  Module._load = function load(request, parent, isMain) {
    if (request === 'react-native') {
      return {
        ActivityIndicator,
        KeyboardAvoidingView,
        Modal,
        Platform: { OS: 'web' },
        Pressable,
        ScrollView,
        StyleSheet,
        Text,
        Keyboard: { addListener: () => ({ remove() {} }) },
      TextInput,
        View,
      };
    }
    if (request === '@expo/vector-icons') {
      return { Feather: host('Feather'), MaterialCommunityIcons: host('MaterialCommunityIcons') };
    }
    if (request === 'react-native-safe-area-context') {
      return { useSafeAreaInsets: () => ({ bottom: 0, left: 0, right: 0, top: 0 }) };
    }
    if (request === 'react-native-reanimated') {
      const reanimated = { FlatList, View: host('ReanimatedView') };
      return {
        ...reanimated,
        default: reanimated,
        useAnimatedScrollHandler: handlers => handlers,
        useAnimatedStyle: () => ({}),
        useComposedEventHandler: handlers => event => handlers.filter(Boolean).forEach(handler => {
          if (typeof handler === 'function') handler(event);
          else handler.onScroll?.(event);
        }),
        useSharedValue: value => ({ value }),
      };
    }
    if (request === '@tanstack/react-query') {
      return { useQueryClient: () => ({ setQueryData: () => {} }) };
    }
    if (request === '../components/CategoryPager') {
      return {
        CategoryPager: ({ renderPage, options }) => renderPage(options[0]),
      };
    }
    if (request === '../components/Chip' || request === './Chip') {
      return { Chip: ({ active, label, onPress }) => React.createElement(Pressable, { accessibilityLabel: label, accessibilityRole: 'button', accessibilityState: { selected: active }, onPress }, React.createElement(Text, null, label)) };
    }
    if (request === '../components/FeedbackState') {
      return {
        EmptyState: ({ message }) => React.createElement(Text, null, message),
        ErrorState: ({ message, onRetry }) => React.createElement(View, null, React.createElement(Text, null, message), React.createElement(Pressable, { accessibilityRole: 'button', onPress: onRetry }, React.createElement(Text, null, '重试'))),
        LoadingBlock: () => React.createElement(ActivityIndicator),
        listFeedbackStyles: { content: {}, state: {} },
      };
    }
    if (request === '../components/PageHeader') {
      return { PageHeaderContent: ({ title }) => React.createElement(Text, null, title) };
    }
    if (request === '../components/SourceAvatar') {
      return { SourceAvatar: () => React.createElement(View) };
    }
    if (request === '../hooks/useCollapsingChrome') {
      return {
        useChromeStyle: () => ({}),
        useCollapsingChrome: () => ({ onScroll: () => {}, revealChrome: () => {} }),
      };
    }
    if (request === '../hooks/useSources') {
      return {
        useSources: () => ({
          error: null,
          isPending: false,
          isSuccess: true,
          items: [{
            canonical_url: 'https://example.test/feed',
            display_name: 'Example source',
            folder_name: null,
            source_id: 'source-1',
            source_kind: 'rss',
            status: 'active',
            subscription_id: 'subscription-1',
          }],
          loading: false,
          message: '',
          refetch: async () => {},
        }),
      };
    }
    if (request === '../lib/connection/react') {
      return { useReaderRuntime: () => currentRuntime };
    }
    if (request === '../lib/connection') {
      return {
        captureRuntimeContext: (runtime) => runtime === currentRuntime
          ? { generation: runtime.generation, runtime, serverId: runtime.identity.server_id }
          : null,
        isRuntimeContextCurrent: (context) => context?.runtime === currentRuntime && context.generation === currentRuntime.generation,
      };
    }
    if (request === '../lib/api') {
      const addSource = (kind, args) => {
        addCalls += 1;
        addRequests.push({ args, kind });
        if (nextAddError) {
          const error = nextAddError;
          nextAddError = null;
          return Promise.reject(error);
        }
        if (!deferNextAdd) return Promise.resolve();
        deferNextAdd = false;
        return new Promise((resolve) => deferredAdds.push(resolve));
      };
      return {
        addRedditSource: (...args) => addSource('reddit', args),
        addRssSource: (...args) => addSource('rss', args),
        addWebSource: (...args) => addSource('web', args),
        addXSource: (...args) => addSource('x', args),
        addYouTubeSource: (...args) => addSource('youtube', args),
        removeSourceSubscription: (...args) => {
          removeCalls += 1;
          return new Promise((resolve, reject) => removeRequests.push({ args, reject, resolve }));
        },
        updateSourceSubscriptionFolder: async () => ({}),
      };
    }
    if (request === '../state/invalidation') return { invalidateAfterSourceMutation: async () => {} };
    if (request === '../state/queryClient') return { readerQueryKeys: { sources: (...args) => ['sources', ...args] } };
    return originalLoad.call(this, request, parent, isMain);
  };

  const { AddSourceSheet } = require(join(outputDirectory, 'components/AddSourceSheet.js'));
  const { SourcesScreen } = require(join(outputDirectory, 'screens/SourcesScreen.js'));
  const { UNCATEGORIZED_FOLDER } = require(join(outputDirectory, 'domain/folders.js'));
  const session = { access_token: 'test-token', user: { email: 'test@example.com', id: 'user-1' } };
  const props = { active: true, chromeProgress: { value: 0 }, onOpenSource: () => {}, session };
  let staleAddedCalls = 0;
  let staleCloseCalls = 0;
  const addSheetProps = {
    folders: ['AI'],
    initialFolder: 'AI',
    onAdded: () => { staleAddedCalls += 1; },
    onClose: () => { staleCloseCalls += 1; },
    session,
    visible: true,
  };
  const pressableByText = (root, value) => root.findAll((node) => node.type === 'Pressable' && node.findAll((child) => child.type === 'Text' && child.children.join('') === value).length > 0)[0];
  const inputByLabel = (root, label) => root.findAll((node) => node.type === 'TextInput' && node.props.accessibilityLabel === label)[0];
  let addSheetTree;
  await act(async () => { addSheetTree = create(React.createElement(AddSourceSheet, addSheetProps)); });
  let preservedInput = addSheetTree.root.findAll((node) => node.type === 'TextInput' && node.props.accessibilityLabel === 'https://example.com/feed.xml')[0];
  await act(async () => { preservedInput.props.onChangeText('https://example.test/draft.xml'); });
  await act(async () => { addSheetTree.update(React.createElement(AddSourceSheet, { ...addSheetProps, initialFolder: '未分类', key: 1 })); });
  preservedInput = addSheetTree.root.findAll((node) => node.type === 'TextInput' && node.props.accessibilityLabel === 'https://example.com/feed.xml')[0];
  assert.equal(preservedInput.props.value, '', 'a new form instance opens without one frame of stale input');
  await act(async () => { preservedInput.props.onChangeText('https://example.test/draft.xml'); });
  await act(async () => { addSheetTree.update(React.createElement(AddSourceSheet, { ...addSheetProps, initialFolder: 'AI', key: 1 })); });
  preservedInput = addSheetTree.root.findAll((node) => node.type === 'TextInput' && node.props.accessibilityLabel === 'https://example.com/feed.xml')[0];
  assert.equal(preservedInput.props.value, 'https://example.test/draft.xml', 'an external folder change cannot clear an open add form');
  deferNextAdd = true;
  const deferredSubmit = pressableByText(addSheetTree.root, '添加并同步');
  await act(async () => {
    deferredSubmit.props.onPress();
    deferredSubmit.props.onPress();
    await Promise.resolve();
  });
  assert.equal(deferredAdds.length, 1);
  assert.equal(addCalls, 1, 'a same-frame double press issues only one add request');
  await act(async () => { addSheetTree.update(React.createElement(AddSourceSheet, { ...addSheetProps, key: 2 })); });
  deferredAdds[0]();
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  assert.equal(staleAddedCalls, 0, 'an unmounted form cannot publish a stale success message');
  assert.equal(staleCloseCalls, 0, 'an unmounted form cannot close a newly opened sheet');
  await act(async () => { addSheetTree.unmount(); });
  addCalls = 0;
  addRequests.length = 0;

  const initialRuntime = currentRuntime;
  let runtimeRaceAddedCalls = 0;
  let runtimeRaceCloseCalls = 0;
  let runtimeRaceTree;
  await act(async () => {
    runtimeRaceTree = create(React.createElement(AddSourceSheet, {
      ...addSheetProps,
      onAdded: () => { runtimeRaceAddedCalls += 1; },
      onClose: () => { runtimeRaceCloseCalls += 1; },
    }));
  });
  await act(async () => { inputByLabel(runtimeRaceTree.root, 'https://example.com/feed.xml').props.onChangeText('https://example.test/runtime.xml'); });
  const runtimeDeferredIndex = deferredAdds.length;
  deferNextAdd = true;
  await act(async () => { pressableByText(runtimeRaceTree.root, '添加并同步').props.onPress(); await Promise.resolve(); });
  assert.equal(addRequests.at(-1).args[3], initialRuntime, 'add requests use the runtime captured at submission');
  currentRuntime = { generation: 2, identity: { ...initialRuntime.identity, server_id: 'sources-next' } };
  await act(async () => { runtimeRaceTree.update(React.createElement(AddSourceSheet, {
    ...addSheetProps,
    onAdded: () => { runtimeRaceAddedCalls += 1; },
    onClose: () => { runtimeRaceCloseCalls += 1; },
  })); });
  deferredAdds[runtimeDeferredIndex]();
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  assert.equal(runtimeRaceAddedCalls, 0, 'a mounted form ignores success from a previous runtime generation');
  assert.equal(runtimeRaceCloseCalls, 0, 'a mounted form is not closed by a previous runtime generation');
  await act(async () => { runtimeRaceTree.unmount(); });
  currentRuntime = initialRuntime;
  addCalls = 0;
  addRequests.length = 0;

  const addCases = [
    { folder: UNCATEGORIZED_FOLDER, input: '  https://example.test/feed.xml  ', kind: 'rss', label: 'RSS', placeholder: 'https://example.com/feed.xml', sentFolder: null },
    { folder: 'AI', input: '  https://example.test/blog  ', kind: 'web', label: '网页', newFolder: '   ', placeholder: 'https://example.com/blog', sentFolder: 'AI' },
    { folder: 'AI', input: '  r/LocalLLaMA  ', kind: 'reddit', label: 'Reddit', newFolder: '  Research  ', placeholder: 'r/LocalLLaMA', sentFolder: 'Research' },
    { folder: UNCATEGORIZED_FOLDER, input: '  @aiDotEngineer  ', kind: 'youtube', label: 'YouTube', placeholder: '频道名，例如 @aiDotEngineer', sentFolder: null },
    { folder: 'AI', input: '  @OpenAI  ', kind: 'x', label: 'X', placeholder: '@OpenAI', sentFolder: 'AI' },
  ];
  for (const testCase of addCases) {
    let caseTree;
    await act(async () => {
      caseTree = create(React.createElement(AddSourceSheet, { ...addSheetProps, initialFolder: testCase.folder }));
    });
    if (testCase.kind !== 'rss') {
      await act(async () => { caseTree.root.findByProps({ accessibilityLabel: testCase.label }).props.onPress(); });
    }
    await act(async () => { inputByLabel(caseTree.root, testCase.placeholder).props.onChangeText(testCase.input); });
    if (testCase.newFolder) {
      await act(async () => { inputByLabel(caseTree.root, '创建新分类').props.onChangeText(testCase.newFolder); });
    }
    if (testCase.newFolder?.trim() === '') {
      assert.equal(caseTree.root.findByProps({ accessibilityLabel: testCase.folder }).props.accessibilityState.selected, true, 'whitespace-only new folder input keeps the submitted existing folder visibly selected');
    }
    await act(async () => { pressableByText(caseTree.root, '添加并同步').props.onPress(); await Promise.resolve(); });
    const request = addRequests.at(-1);
    assert.equal(request.kind, testCase.kind, `${testCase.kind} dispatches to its matching API`);
    assert.equal(request.args[1], testCase.input.trim(), `${testCase.kind} trims the submitted source value`);
    assert.equal(request.args[2], testCase.sentFolder, `${testCase.kind} applies the expected folder precedence`);
    assert.equal(request.args[3], currentRuntime, `${testCase.kind} receives the current runtime override`);
    await act(async () => { caseTree.unmount(); });
  }
  assert.equal(addCalls, addCases.length, 'each supported source kind issues exactly one request');

  let failedTree;
  nextAddError = new Error('source rejected');
  await act(async () => { failedTree = create(React.createElement(AddSourceSheet, addSheetProps)); });
  await act(async () => { inputByLabel(failedTree.root, 'https://example.com/feed.xml').props.onChangeText('https://example.test/bad.xml'); });
  await act(async () => { pressableByText(failedTree.root, '添加并同步').props.onPress(); await Promise.resolve(); });
  assert.ok(failedTree.root.findAll((node) => node.props.accessibilityLiveRegion === 'assertive' && node.children.join('') === 'source rejected').length === 1, 'add failure remains visible and accessible');
  assert.equal(inputByLabel(failedTree.root, 'https://example.com/feed.xml').props.value, 'https://example.test/bad.xml', 'add failure preserves user input for correction');
  await act(async () => { inputByLabel(failedTree.root, 'https://example.com/feed.xml').props.onChangeText('https://example.test/fixed.xml'); });
  assert.equal(failedTree.root.findAll((node) => node.props.accessibilityLiveRegion === 'assertive').length, 0, 'editing after failure clears stale feedback');
  await act(async () => { failedTree.unmount(); });
  addCalls = 0;
  addRequests.length = 0;

  let tree;
  await act(async () => { tree = create(React.createElement(SourcesScreen, props)); });

  const byLabel = (label) => tree.root.findAll((node) => node.type === 'Pressable' && node.props.accessibilityLabel === label)[0];
  const byText = (value, exact = false) => tree.root.findAll((node) => node.type === 'Pressable' && node.findAll((child) => child.type === 'Text' && (exact ? child.children.join('') === value : child.children.join('').includes(value))).length > 0)[0];
  const openMenu = async () => {
    await act(async () => { byLabel('Example source，更多操作').props.onPress(); });
  };
  const chooseRemove = async () => {
    await act(async () => { byText('取消订阅').props.onPress(); });
  };
  const flushFocus = async () => new Promise((resolve) => setTimeout(resolve, 0));

  await act(async () => { byLabel('添加订阅').props.onPress(); });
  const rssInput = tree.root.findAll((node) => node.type === 'TextInput' && node.props.accessibilityLabel === 'https://example.com/feed.xml')[0];
  await act(async () => { rssInput.props.onChangeText('https://example.test/feed.xml'); });
  await act(async () => { byText('添加并同步', true).props.onPress(); await Promise.resolve(); });
  assert.equal(addCalls, 1, 'the extracted add sheet issues exactly one RSS request');
  assert.equal(addRequests[0].kind, 'rss');
  assert.equal(addRequests[0].args[1], 'https://example.test/feed.xml');
  assert.equal(addRequests[0].args[2], null, 'the uncategorized choice remains an API null folder');
  assert.equal(tree.root.findAllByType('Modal').length, 0, 'a successful add closes the sheet');
  assert.equal(tree.root.findAll((node) => node.type === 'Text' && node.children.join('').includes('已添加，正在同步订阅内容。')).length, 0, 'a successful add leaves no stale global sync message');

  assert.equal(byLabel('Example source，更多操作'), undefined, 'category overview does not flatten channels into the top-level list');
  assert.ok(byLabel('未分类，1 个订阅源'), 'overview exposes the actual category and source count');
  await act(async () => { byLabel('未分类，1 个订阅源').props.onPress(); });
  assert.ok(byLabel('Example source，更多操作'), 'opening a category exposes source actions');
  assert.ok(tree.root.findAll((node) => node.type === 'Text' && node.children.join('') === '同步正常').length, 'each source keeps its own health below the address');
  await act(async () => { byLabel('返回我的分类').props.onPress(); });
  assert.equal(byLabel('Example source，更多操作'), undefined, 'returning to categories restores the overview');
  await act(async () => { inputByLabel(tree.root, '搜索订阅源').props.onChangeText('Example'); });
  assert.ok(byLabel('Example source，更多操作'), 'global search exposes matching channels directly');
  await act(async () => { byLabel('清除搜索').props.onPress(); });
  await act(async () => { byLabel('未分类，1 个订阅源').props.onPress(); });

  await openMenu();
  assert.equal(removeCalls, 0, 'opening a row menu cannot mutate');
  await act(async () => { byText('取消', true).props.onPress(); });
  await act(async () => { await flushFocus(); });
  assert.equal(removeCalls, 0, 'menu cancellation cannot mutate');
  assert.ok(focusCalls >= 1, 'menu cancellation returns focus to the row trigger');

  await openMenu();
  await chooseRemove();
  assert.equal(removeCalls, 0, 'confirmation opening cannot mutate');
  const cancel = byText('取消', true);
  await act(async () => { cancel.props.onPress(); });
  await act(async () => { await flushFocus(); });
  assert.equal(removeCalls, 0, 'confirmation cancellation cannot mutate');
  assert.ok(focusCalls >= 2, 'confirmation cancellation returns focus to the row trigger');

  await openMenu();
  await chooseRemove();
  let confirmButton = byText('取消订阅', true);
  await act(async () => { confirmButton.props.onPress(); await Promise.resolve(); });
  assert.equal(removeCalls, 1, 'confirmation issues exactly one controlled DELETE');
  const busyButton = tree.root.findAll((node) => node.type === 'Pressable' && node.props.accessibilityState?.busy === true)[0];
  assert.equal(busyButton.props.accessibilityState.busy, true, 'confirmation exposes busy state');
  await act(async () => { busyButton.props.onPress(); });
  assert.equal(removeCalls, 1, 'busy confirmation suppresses duplicate DELETE');
  removeRequests[0].reject(new Error('delete failed'));
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  assert.equal(removeCalls, 1);
  assert.ok(tree.root.findAllByProps({ accessibilityRole: 'alert' }).length >= 1, 'failure remains inline and accessible');
  assert.ok(byLabel('Example source，更多操作'), 'failed mutation keeps the source row mounted');

  confirmButton = byText('取消订阅', true);
  await act(async () => { confirmButton.props.onPress(); await Promise.resolve(); });
  assert.equal(removeCalls, 2, 'failure can be retried by the same explicit action');
  removeRequests[1].resolve();
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  assert.equal(tree.root.findAllByProps({ accessibilityRole: 'alert' }).length, 0);
  assert.equal(tree.root.findAll((node) => node.type === 'Text' && node.children.join('') === '已取消订阅。').length, 0, 'unsubscribe leaves no persistent global status');

  await openMenu();
  await chooseRemove();
  const modal = tree.root.findAllByType('Modal').at(-1);
  await act(async () => { modal.props.onRequestClose(); });
  assert.equal(removeCalls, 2, 'Escape/back cancellation cannot mutate');

  await openMenu();
  await chooseRemove();
  confirmButton = byText('取消订阅', true);
  await act(async () => { confirmButton.props.onPress(); await Promise.resolve(); });
  assert.equal(removeCalls, 3);
  currentRuntime = { generation: 2, identity: { ...currentRuntime.identity, server_id: 'sources-next' } };
  await act(async () => { tree.update(React.createElement(SourcesScreen, props)); });
  removeRequests[2].resolve();
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  assert.equal(tree.root.findAllByProps({ accessibilityRole: 'alert' }).length, 0, 'stale runtime result has no UI error');
} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}

console.log('Mounted SourcesScreen ActionConfirm flow passed.');
