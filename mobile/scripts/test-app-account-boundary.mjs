import assert from 'node:assert/strict';
import { buildSync } from 'esbuild';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import Module from 'node:module';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';

const root = fileURLToPath(new URL('..', import.meta.url));
const require = createRequire(import.meta.url);
const React = require('react');
const { act, create } = require('react-test-renderer');
const ts = require('typescript');
// Share the production message runtime across the compiled App.
// Keep packages external so its hooks use the same React as the test renderer.
const localizationModule = new Module(join(root, 'i18n/account-boundary-runtime.cjs'));
localizationModule.filename = join(root, 'i18n/account-boundary-runtime.cjs');
localizationModule.paths = Module._nodeModulePaths(root);
localizationModule._compile(buildSync({
  stdin: { contents: "export * from './i18n/index'; export * from './i18n/message';", resolveDir: root, loader: 'ts' },
  bundle: true, packages: 'external',
  format: 'cjs', platform: 'node', write: false,
}).outputFiles[0].text, localizationModule.filename);
const localization = localizationModule.exports;
await localization.i18n.changeLanguage('zh-CN');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
globalThis.__DEV__ = false;
const host = (name) => (props) => React.createElement(name, props, props.children);
function compile(file, mocks) {
  const filename = join(root, file);
  const mod = new Module(filename);
  mod.filename = filename;
  mod.require = (name) => Object.hasOwn(mocks, name) ? mocks[name] : require(name);
  mod._compile(ts.transpileModule(readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, filename);
  return mod.exports;
}
const snapshot = compile('lib/connection/snapshot.ts', {});
const guard = compile('lib/connection/guard.ts', { './snapshot': snapshot });
let authListener;
let tokenInvalidListener;
let currentSession = { user: { id: 'A' }, access_token: 'A' };
const runtime = {
  generation: 1, base_url: 'https://reader.example',
  identity: { server_id: 'server-a', api_base_url: 'https://reader.example' },
  authClient: { auth: {
    getSession: async () => ({ data: { session: currentSession }, error: null }),
    onAuthStateChange: (listener) => {
      authListener = listener;
      return { data: { subscription: { unsubscribe() { if (authListener === listener) authListener = null; } } } };
    },

  } },
};
const native = {
  ActivityIndicator: host('ActivityIndicator'), Text: host('Text'), View: host('View'),
  StyleSheet: { create: (v) => v }, Platform: { OS: 'android' },
  BackHandler: { addEventListener: () => ({ remove() {} }) },

};
let clearCalls = 0;
const connection = {
  getActiveRuntime: snapshot.getActiveRuntimeSnapshot,
  getConnectionGeneration: snapshot.getConnectionGenerationSnapshot,
  subscribeConnection: snapshot.subscribeConnectionSnapshot,
  updateReaderSessionIdentity: snapshot.updateReaderSessionIdentity,
  readPersistedConnection: () => ({ base_url: runtime.base_url, identity: runtime.identity, discovery: { protocol_version: 2, server_id: 'server-a', api_base_url: runtime.base_url } }),
  readLegacyServerAddress: () => null,
  restorePersistedReaderRuntime: async () => ({ runtime, session: currentSession }),
  discoverReaderServer: async () => ({ protocol_version: 2, server_id: 'server-a', api_base_url: runtime.base_url, base_url: runtime.base_url }),
  readerIdentitiesMatch: () => true,
  writePersistedConnection: () => {},
  notifyServerTokenInvalid: failedRuntime => tokenInvalidListener?.(failedRuntime),
  subscribeServerTokenInvalid: listener => { tokenInvalidListener = listener; return () => { if (tokenInvalidListener === listener) tokenInvalidListener = null; }; },
  ConnectionError: class ConnectionError extends Error { constructor(code, message) { super(message); this.code = code; } },
  logoutReaderSession: async (options) => {
    options.clearSession();
    options.stopAuthListener();
    currentSession = null;
    snapshot.commitRuntimeSnapshot({ ...runtime, generation: 2 }, 2);
  },
};
const shellMocks = {
  'react-native': native,
  'react-native-reanimated': { useSharedValue: (v) => React.useRef({ value: v }).current },
  '../domain/mainNavigation': compile('domain/mainNavigation.ts', {}),
  '../domain/readerHtml': { buildReaderHtml: () => '' },
  '../ui/tokens': { colors: {} },
  './ReaderTabBar': { MotionReaderTabBar: host('ReaderTabBar') },
};
for (const name of ['ArticleReaderScreen', 'HomeScreen', 'ProfileScreen', 'RankingPreviewScreen', 'RankingsScreen', 'SavedScreen', 'SourcesScreen']) {
  shellMocks[`../screens/${name}`] = { [name]: host(name) };
}
const { MainAppShell } = compile('components/MainAppShell.tsx', shellMocks);
const mocks = {
  'expo-sqlite/localStorage/install': {}, 'react-native': native,
  'expo-status-bar': { StatusBar: host('StatusBar') },
  'react-native-gesture-handler': { GestureHandlerRootView: host('GestureHandlerRootView') },
  '@tanstack/react-query': { QueryClientProvider: host('QueryClientProvider') },
  'react-native-safe-area-context': { SafeAreaProvider: host('SafeAreaProvider'), SafeAreaView: host('SafeAreaView') },
  './state/queryClient': { queryClient: { cancelQueries: async () => {}, clear() { clearCalls += 1; } } },
  './lib/connection': connection,
  './components/MainAppShell': { MainAppShell },
  './ui/tokens': { colors: {} },
  './domain/translationDiagnostics': { clearTranslationDiagnostics() {}, recordTranslationDiagnostic() {} },
  './domain/xFeedTranslationMemory': { clearXFeedTranslationMemory() {} },
  './app.json': { expo: { version: 'test' } },
  './i18n': localization,
  './i18n/message': localization,
  // Device locale subscription is covered by the localization gate; keep this
  // harness focused on production account boundaries.
  './i18n/AppLanguageProvider': { AppLanguageProvider: ({ children }) => children },
};
for (const [dir, name] of [['components', 'ActionConfirm'], ['components', 'RedirectConfirm'], ['components', 'ServerConnectionForm'], ['screens', 'EmailAuthScreen'], ['screens', 'HomeUiDraftPreview']]) {
  mocks[`./${dir}/${name}`] = { [name]: host(name) };
}
const App = compile('App.tsx', mocks).default;
snapshot.commitRuntimeSnapshot(runtime, 1);
let tree;
try {
  await act(async () => { tree = create(React.createElement(App)); });
  await act(async () => tree.root.findByType('HomeScreen').props.onOpenArticle({ content_id: 'A-private', title: 'private' }));
  const contextA = guard.captureRuntimeContext();
  const beforeRefresh = clearCalls;
  await act(async () => { await localization.i18n.changeLanguage('en'); });
  assert.equal(guard.isRuntimeContextCurrent(contextA), true, 'interface language changes preserve the account epoch');
  assert.equal(clearCalls, beforeRefresh, 'interface language changes must not discard query state');
  assert.equal(tree.root.findByType('ArticleReaderScreen').props.item.content_id, 'A-private',
    'interface language changes preserve the reader selection');
  await act(async () => { await localization.i18n.changeLanguage('zh-CN'); });
  await act(async () => authListener('TOKEN_REFRESHED', { ...currentSession, access_token: 'refreshed-A' }));
  assert.equal(guard.isRuntimeContextCurrent(contextA), true);
  assert.equal(clearCalls, beforeRefresh, 'token refresh must not discard query state');
  assert.equal(tree.root.findByType('ArticleReaderScreen').props.item.content_id, 'A-private');
  await act(async () => {
    currentSession = { user: { id: 'B' }, access_token: 'B' };
    authListener('SIGNED_IN', currentSession);
    assert.equal(guard.isRuntimeContextCurrent(contextA), false, 'old operations invalidate before React commits');
  });
  assert.equal(tree.root.findAllByType('ArticleReaderScreen').length, 0, 'B cannot inherit A reader selection');
  assert.equal(tree.root.findByType('HomeScreen').props.session.user.id, 'B');
  await act(async () => tree.root.findByType(MainAppShell).props.onLogout());
  assert.equal(tree.root.findAllByType('EmailAuthScreen').length, 1);
  assert.equal(tree.root.findAllByType('ArticleReaderScreen').length, 0, 'logout discards private reader selection');
  await act(async () => tokenInvalidListener(runtime));
  assert.equal(tree.root.findAllByType('ServerConnectionForm').length, 0, 'invalid-token notifications from old runtimes cannot interrupt the current connection');
  await act(async () => tokenInvalidListener(snapshot.getActiveRuntimeSnapshot()));
  assert.equal(tree.root.findAllByType('ServerConnectionForm').length, 1, 'rotated entry token returns the app to token input');
  assert.equal(tree.root.findByType('ServerConnectionForm').props.error.code, 'server_token_invalid');
  assert.equal(tree.root.findByType('ServerConnectionForm').props.initialValue, runtime.base_url, 'entry-token recovery retains the server address');
} finally {
  if (tree) await act(async () => tree.unmount());
}
console.log('Production App account epochs, shell reset, logout and invalid server-token boundary passed.');
