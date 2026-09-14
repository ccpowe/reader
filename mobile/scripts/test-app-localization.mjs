import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { createRequire, Module } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';

const require = createRequire(import.meta.url);
const React = require('react');
const { act, create } = require('react-test-renderer');
const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const directory = mkdtempSync(join(tmpdir(), 'reader-app-localization-'));
const originalLoad = Module._load;
const previousNodePath = process.env.NODE_PATH;
const previousStorage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
const previousWindow = Object.getOwnPropertyDescriptor(globalThis, 'window');
const previousLocale = Object.getOwnPropertyDescriptor(Intl, 'Locale');
const previousPluralRules = Object.getOwnPropertyDescriptor(Intl, 'PluralRules');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();

const host = (name) => ({ children, ...props }) => React.createElement(name, props, children);
const nativeListeners = new Set();
const browserListeners = new Set();
const persisted = new Map();
const storage = {
  getItem: (key) => persisted.get(key) ?? null,
  setItem: (key, value) => { persisted.set(key, value); },
};
Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: storage });
Object.defineProperty(globalThis, 'window', {
  configurable: true,
  value: {
    addEventListener(name, listener) { if (name === 'languagechange') browserListeners.add(listener); },
    removeEventListener(name, listener) { if (name === 'languagechange') browserListeners.delete(listener); },
  },
});
const platform = { OS: 'android' };
let deviceLanguages = ['en-US'];
let chromeLocked = false;
let avatarPickerResult = { canceled: true };
let avatarUploadError;
let onAvatarUpload;
const translationUpdates = [];
const runtime = { generation: 1, identity: { server_id: 'localization-test' } };
const session = { access_token: 'access', user: { id: 'reader', email: 'reader@example.com', user_metadata: {} } };
const profile = { id: 'reader', display_name: 'Reader', avatar_url: null };
const translationPreference = {
  is_enabled: true,
  target_locale: 'zh-CN',
  provider_mode: 'app_default',
  supported_locales: ['zh-CN', 'en'],
  managed_engines: [],
};
let tree;
let localization;

function restoreGlobal(name, descriptor) {
  if (descriptor) Object.defineProperty(globalThis, name, descriptor);
  else delete globalThis[name];
}

try {
  buildSync({
    stdin: {
      contents: `
        export * from './i18n/index';
        export * from './i18n/registry';
        export * from './i18n/store';
        export * from './i18n/react';
        export * from './i18n/message';
        export * from './i18n/AppLanguageProvider';
        export { ProfileScreen } from './screens/ProfileScreen';
        export { ConnectionError, asConnectionError } from './lib/connection/errors';
        export { parseReaderBaseUrl } from './lib/connection/url';
        export { ReaderAuthError } from './lib/readerAuth';
      `,
      resolveDir: mobileRoot,
      sourcefile: 'localization-test-entry.ts',
      loader: 'ts',
    },
    outfile: join(directory, 'localization.cjs'),
    bundle: true,
    platform: 'node',
    format: 'cjs',
    jsx: 'automatic',
    packages: 'external',
    external: [
      '../components/PageHeader', '../components/BottomSheetModal',
      '../hooks/useTranslationPreference', '../hooks/useCollapsingChrome',
      '../lib/api', '../lib/connection', '../lib/connection/react',
      './connection',
      '../state/invalidation', '../state/queryClient',
    ],
  });
  Module._load = function load(request, parent, isMain) {
    if (request === 'react-native') return {
      ActivityIndicator: host('ActivityIndicator'), Alert: { alert() {} }, Image: host('Image'),
      Pressable: host('Pressable'), Switch: host('Switch'), Text: host('Text'),
      TextInput: host('TextInput'), View: host('View'), StyleSheet: { create: value => value },
      Platform: platform,
      AppState: { addEventListener(name, listener) {
        assert.equal(name, 'change');
        nativeListeners.add(listener);
        return { remove() { nativeListeners.delete(listener); } };
      } },
    };
    if (request === 'react-native-reanimated') return {
      __esModule: true,
      default: { ScrollView: host('ScrollView'), View: host('AnimatedView') },
    };
    if (request === 'expo-localization') return { getLocales: () => deviceLanguages.map(languageTag => ({ languageTag })) };
    if (request === 'expo-image-picker') return { launchImageLibraryAsync: async () => avatarPickerResult };
    if (request === '@expo/vector-icons') return { Feather: host('Feather') };
    if (request === '@tanstack/react-query') return {
      useQuery: () => ({ data: profile, isPending: false }),
      useQueryClient: () => ({ setQueryData() {} }),
      // Execute the real mutationFn so the content-language control below proves
      // this API spy detects writes, rather than merely observing a no-op mock.
      useMutation: options => ({ isPending: false, mutate: value => { void options.mutationFn(value); } }),
    };
    if (request === '../components/PageHeader') return { PageHeaderContent: host('PageHeader') };
    if (request === '../components/BottomSheetModal') return {
      BottomSheetModal: ({ children, visible }) => visible ? React.createElement('BottomSheet', null, children) : null,
    };
    if (request === '../hooks/useCollapsingChrome') return {
      useChromeStyle: () => ({}),
      useCollapsingChrome: (_active, _progress, _key, options) => { chromeLocked = options.locked; return { onScroll() {} }; },
    };
    if (request === '../hooks/useTranslationPreference') return {
      useTranslationPreference: () => ({
        data: translationPreference, enabled: true, targetLocale: translationPreference.target_locale,
        engineLabel: 'DeepSeek', engineAvailable: true, isPending: false,
      }),
    };
    if (request === '../lib/connection/react') return { useReaderRuntime: () => runtime };
    if (request === '../lib/connection' || request === './connection') return {
      captureRuntimeContext: () => ({ runtime, generation: 1, serverId: runtime.identity.server_id }),
      getActiveRuntime: () => runtime,
      getConnectionGeneration: () => 1,
      isRuntimeContextCurrent: () => true,
    };
    if (request === '../lib/api') return {
      getProfile: async () => profile,
      updateTranslationPreference: async (...args) => { translationUpdates.push(args); return translationPreference; },
      updateProfile: async () => { throw new Error('Unexpected profile mutation'); },
      uploadAvatar: async () => {
        onAvatarUpload?.();
        throw avatarUploadError ?? new Error('Unexpected avatar upload');
      },
    };
    if (request === '../state/invalidation') return { invalidateAfterTranslationPreferenceMutation: async () => {} };
    if (request === '../state/queryClient') return {
      readerQueryKeys: { profile: (...args) => ['profile', ...args], translationPreference: (...args) => ['translation', ...args] },
    };
    return originalLoad.call(this, request, parent, isMain);
  };

  // Model the Intl API surface of the exact Hermes version shipped by RN 0.86.
  delete Intl.Locale;
  delete Intl.PluralRules;
  localization = require(join(directory, 'localization.cjs'));
  assert.equal(typeof Intl.Locale, 'function');
  assert.equal(typeof Intl.PluralRules, 'function');
  const {
    appLanguages, resolveSystemLanguage, normalizeLanguagePreference,
    createAppLanguageStore, appLanguageStore, languagePreferenceKey,
    resources, i18n, AppLanguageProvider, useAppLanguage, useTranslation, ProfileScreen,
    ConnectionError, asConnectionError, parseReaderBaseUrl, ReaderAuthError,
    localizedMessage, resolveMessage, bindLocalizedErrorMessage,
  } = localization;

  assert.equal(resolveSystemLanguage(['fr-FR', 'en-GB', 'zh-CN']), 'en', 'unsupported preferences are skipped in OS order');
  assert.equal(resolveSystemLanguage(['zh-TW', 'en-US']), 'zh-CN', 'the first supported language wins even when the pack uses another region');
  assert.equal(resolveSystemLanguage(['en_AU']), 'en', 'underscore region variants are accepted');
  assert.equal(resolveSystemLanguage(['en-US-u-hc-h12']), 'en', 'Unicode extensions do not affect matching');
  assert.equal(resolveSystemLanguage(['not a tag', 'en-US']), 'en', 'malformed tags do not conceal the next valid language');
  assert.equal(resolveSystemLanguage(['fr-FR', 'ja-JP']), 'zh-CN', 'unsupported language lists fall back to Chinese');
  assert.equal(resolveSystemLanguage([]), 'zh-CN', 'empty language lists fall back to Chinese');
  const chinesePacks = [
    { id: 'zh-CN', language: 'zh', script: 'Hans' },
    { id: 'zh-TW', language: 'zh', script: 'Hant' },
  ];
  assert.equal(resolveSystemLanguage(['zh-HK'], chinesePacks), 'zh-TW', 'traditional regional variants prefer a matching bundled script');
  assert.equal(resolveSystemLanguage(['zh-Hans-HK'], chinesePacks), 'zh-CN', 'an explicit script takes priority over a region');
  assert.equal(resolveSystemLanguage(['en-US'], chinesePacks), 'zh-CN', 'a removed pack is no longer selected');
  for (const invalid of [undefined, null, '', 'ja', 'en-US', 'removed-language', {}]) {
    assert.equal(normalizeLanguagePreference(invalid), 'system', 'invalid or removed saved language returns to System');
  }
  for (const pack of appLanguages) assert.equal(normalizeLanguagePreference(pack.id), pack.id);

  const saved = new Map();
  const testStorage = { getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value) };
  let orderedLanguages = ['en-GB'];
  const applied = [];
  const store = createAppLanguageStore({ storage: () => testStorage, applyLanguage: value => applied.push(value) });
  let notifications = 0;
  const unsubscribe = store.subscribe(() => { notifications += 1; });
  store.initialize(() => orderedLanguages);
  assert.deepEqual(store.getSnapshot(), { preference: 'system', language: 'en' });
  const stableSnapshot = store.getSnapshot();
  store.refresh();
  assert.equal(store.getSnapshot(), stableSnapshot, 'unchanged locale reads keep the external-store snapshot stable');
  assert.equal(notifications, 1, 'unchanged locale reads do not notify subscribers');
  store.setPreference('zh-CN');
  assert.equal(saved.get(languagePreferenceKey), 'zh-CN', 'manual choice persists under the device preference key');
  orderedLanguages = ['en-US'];
  store.refresh();
  assert.deepEqual(store.getSnapshot(), { preference: 'zh-CN', language: 'zh-CN' }, 'OS changes cannot override a manual choice');
  const restarted = createAppLanguageStore({ storage: () => testStorage, applyLanguage() {} });
  restarted.initialize(() => ['en-US']);
  assert.deepEqual(restarted.getSnapshot(), { preference: 'zh-CN', language: 'zh-CN' }, 'manual language survives a new store instance');
  store.setPreference('system');
  assert.deepEqual(store.getSnapshot(), { preference: 'system', language: 'en' }, 'returning to System reads the current OS language immediately');
  assert.equal(saved.get(languagePreferenceKey), 'system');
  orderedLanguages = ['zh-CN'];
  store.refresh();
  assert.equal(store.getSnapshot().language, 'zh-CN');
  unsubscribe();
  const beforeUnsubscribedChange = notifications;
  store.setPreference('en');
  assert.equal(notifications, beforeUnsubscribedChange, 'unsubscribed components receive no later updates');
  assert.deepEqual(applied, ['en', 'zh-CN', 'en', 'zh-CN', 'en']);
  for (const removedPreference of ['ja', 'bogus']) {
    saved.set(languagePreferenceKey, removedPreference);
    const restored = createAppLanguageStore({ storage: () => testStorage, applyLanguage() {} });
    restored.initialize(() => ['en-US']);
    assert.deepEqual(restored.getSnapshot(), { preference: 'system', language: 'en' });
  }
  for (const storageProvider of [
    () => { throw new Error('Storage access denied'); },
    () => ({ getItem() { throw new Error('Read denied'); }, setItem() { throw new Error('Write denied'); } }),
  ]) {
    const unavailableStorage = createAppLanguageStore({ storage: storageProvider, applyLanguage() {} });
    unavailableStorage.initialize(() => ['en-US']);
    assert.equal(unavailableStorage.getSnapshot().language, 'en');
    assert.doesNotThrow(() => unavailableStorage.setPreference('zh-CN'));
    assert.equal(unavailableStorage.getSnapshot().language, 'zh-CN', 'storage failure still permits an in-session language choice');
  }
  const unavailableDevice = createAppLanguageStore({ storage: () => undefined, applyLanguage() {} });
  unavailableDevice.initialize(() => { throw new Error('Locale service unavailable'); });
  assert.deepEqual(unavailableDevice.getSnapshot(), { preference: 'system', language: 'zh-CN' });
  console.log('PASS localization registry and persisted device preference behavior');

  const baseKey = key => key.replace(/_(?:zero|one|two|few|many|other)$/, '');
  const normalizedKeys = messages => [...new Set(Object.keys(messages).map(baseKey))].sort();
  const placeholders = value => [...value.matchAll(/{{\s*-?\s*([\w.]+)(?:,[^}]*)?\s*}}/g)].map(match => match[1]).sort();
  const chineseNamespaces = resources['zh-CN'];
  for (const [language, namespaces] of Object.entries(resources)) {
    assert.deepEqual(Object.keys(namespaces).sort(), Object.keys(chineseNamespaces).sort(), `${language} has every application namespace`);
    for (const [namespace, messages] of Object.entries(namespaces)) {
      const reference = chineseNamespaces[namespace];
      assert.deepEqual(normalizedKeys(messages), normalizedKeys(reference), `${language}:${namespace} has matching semantic keys`);
      for (const [key, message] of Object.entries(messages)) {
        assert.equal(typeof message, 'string', `${language}:${namespace}:${key} uses a flat string value`);
        assert.ok(message.trim(), `${language}:${namespace}:${key} must not be empty`);
        const counterpart = reference[key] ?? reference[baseKey(key)] ?? reference[`${baseKey(key)}_other`];
        assert.equal(typeof counterpart, 'string', `${namespace}:${key} has a Chinese equivalent`);
        assert.deepEqual(placeholders(message), placeholders(counterpart), `${language}:${namespace}:${key} preserves interpolation arguments`);
      }
    }
  }
  i18n.addResource('zh-CN', 'localizationTest', 'onlyChinese', '中文后备：{{name}}');
  assert.equal(i18n.t('localizationTest:onlyChinese', { lng: 'en', name: 'Reader' }), '中文后备：Reader', 'missing English keys use the Chinese fallback and still interpolate');
  i18n.removeResourceBundle('zh-CN', 'localizationTest');
  assert.equal(i18n.t('profile:contentLanguage', { lng: 'en', language: 'English' }), 'Content language: English');
  assert.equal(i18n.t('common:threadCollected', { lng: 'en', count: 1 }), 'Thread · 1 post collected');
  assert.equal(i18n.t('common:threadCollected', { lng: 'en', count: 2 }), 'Thread · 2 posts collected');
  assert.equal(i18n.t('common:threadCollected', { lng: 'en', count: 0 }), 'Thread · 0 posts collected');
  console.log('PASS all bundled message namespaces, fallback, interpolation and English plurals');

  let heldConnectionError;
  try { parseReaderBaseUrl(''); } catch (error) { heldConnectionError = error; }
  assert.ok(heldConnectionError instanceof ConnectionError, 'exercise a production app-owned URL validation error');
  const originalConnectionText = heldConnectionError.message;
  assert.equal(originalConnectionText, '请输入 Reader 服务器地址。');
  // Even an external message identical to a dictionary value remains verbatim.
  const externalError = asConnectionError(new Error(originalConnectionText));
  const redactedError = new ConnectionError('discovery_invalid_response', localizedMessage('errors:discoveryFieldInvalid', { field: 'token=private-value' }));
  assert.doesNotMatch(redactedError.message, /private-value/);
  const heldAuthError = new ReaderAuthError('invalid_server_token', 401);
  const originalAuthText = heldAuthError.message;
  assert.equal(originalAuthText, i18n.t('errors:serverTokenInvalid'));
  await i18n.changeLanguage('en');
  assert.equal(heldConnectionError.message, i18n.t('errors:serverAddressRequired'), 'the same held Error.message updates without retrying or replacing the error');
  assert.notEqual(heldConnectionError.message, originalConnectionText);
  assert.equal(externalError.message, originalConnectionText, 'provider/external errors must never be translated by matching their text');
  assert.match(redactedError.message, /\[redacted\]/);
  assert.doesNotMatch(redactedError.message, /private-value/, 'live descriptor interpolation keeps existing redaction');
  assert.equal(heldAuthError.message, i18n.t('errors:serverTokenInvalid'), 'retained invalid-token errors follow the current language');
  assert.notEqual(heldAuthError.message, originalAuthText);
  await i18n.changeLanguage('zh-CN');
  assert.equal(heldConnectionError.message, originalConnectionText, 'the held error can switch back repeatedly');
  console.log('PASS retained app-owned connection errors/auth notices and verbatim external messages');

  let mounts = 0;
  const firstRenders = [];
  function ReaderDraft() {
    const { t } = useTranslation('profile');
    const { language } = useAppLanguage();
    const [draft, setDraft] = React.useState('');
    firstRenders.push({ language, title: t('title') });
    React.useEffect(() => { mounts += 1; }, []);
    return React.createElement('Draft', { language, title: t('title'), value: draft, onChangeText: setDraft });
  }
  const renderDraft = () => React.createElement(AppLanguageProvider, null, React.createElement(ReaderDraft));
  const emitNative = state => { for (const listener of [...nativeListeners]) listener(state); };
  const emitBrowser = () => { for (const listener of [...browserListeners]) listener(); };
  await act(async () => { tree = create(renderDraft()); });
  assert.deepEqual(firstRenders[0], { language: 'en', title: 'Profile' }, 'provider resolves the OS language before the first descendant render');
  assert.equal(nativeListeners.size, 1);
  assert.equal(browserListeners.size, 0, 'native screens do not attach browser listeners');
  await act(async () => tree.root.findByType('Draft').props.onChangeText('unsaved draft'));
  deviceLanguages = ['zh-CN'];
  await act(async () => emitNative('background'));
  assert.equal(tree.root.findByType('Draft').props.language, 'en', 'background state alone does not refresh locale');
  await act(async () => emitNative('active'));
  assert.equal(tree.root.findByType('Draft').props.title, '我的', 'foregrounding refreshes system language and React messages');
  assert.equal(tree.root.findByType('Draft').props.value, 'unsaved draft');
  assert.equal(mounts, 1, 'switching language does not remount descendants');
  await act(async () => appLanguageStore.setPreference('en'));
  await act(async () => emitNative('active'));
  assert.equal(tree.root.findByType('Draft').props.language, 'en', 'foreground refresh preserves manual preference');
  await act(async () => appLanguageStore.setPreference('system'));
  assert.equal(tree.root.findByType('Draft').props.language, 'zh-CN');
  await act(async () => tree.unmount());
  tree = undefined;
  assert.equal(nativeListeners.size, 0, 'unmount removes the AppState listener');

  platform.OS = 'web';
  await act(async () => { tree = create(renderDraft()); });
  assert.equal(browserListeners.size, 1);
  await act(async () => tree.root.findByType('Draft').props.onChangeText('web draft'));
  deviceLanguages = ['en-NZ'];
  await act(async () => emitBrowser());
  assert.equal(tree.root.findByType('Draft').props.title, 'Profile', 'browser languagechange refreshes system language');
  assert.equal(tree.root.findByType('Draft').props.value, 'web draft');
  assert.equal(mounts, 2, 'browser languagechange also preserves the existing component');
  await act(async () => appLanguageStore.setPreference('zh-CN'));
  await act(async () => emitBrowser());
  assert.equal(tree.root.findByType('Draft').props.language, 'zh-CN', 'browser events do not override manual selection');
  await act(async () => tree.unmount());
  tree = undefined;
  assert.equal(nativeListeners.size, 0);
  assert.equal(browserListeners.size, 0, 'unmount removes the browser language listener');
  console.log('PASS mounted foreground/browser updates and preserved component state');

  deviceLanguages = ['zh-CN'];
  appLanguageStore.setPreference('system');
  await act(async () => {
    tree = create(React.createElement(AppLanguageProvider, null, React.createElement(ProfileScreen, {
      active: true, chromeProgress: { value: 0 }, onChangeServer() {}, onLogout: async () => {},
      session, authClient: { auth: { updateEmail: async () => { throw new Error('Unexpected auth mutation'); } } },
    })));
  });
  const pressable = label => tree.root.find(node => node.type === 'Pressable' && node.props.accessibilityLabel === label);
  const setting = title => tree.root.find(node => node.type === 'Pressable' && node.props.accessibilityLabel?.startsWith(`${title}，`));
  const input = label => tree.root.find(node => node.type === 'TextInput' && node.props.accessibilityLabel === label);
  const buttonText = label => tree.root.find(node => node.type === 'Pressable' && node.findAll(child => child.type === 'Text' && child.children.join('') === label).length > 0);
  await act(async () => setting('界面语言').props.onPress());
  assert.equal(chromeLocked, true, 'the app-language sheet locks collapsing chrome');
  assert.equal(pressable('跟随系统').props.accessibilityState.checked, true);
  await act(async () => pressable('English').props.onPress());
  assert.deepEqual(appLanguageStore.getSnapshot(), { preference: 'en', language: 'en' });
  assert.equal(persisted.get(languagePreferenceKey), 'en');
  assert.equal(tree.root.findByType('PageHeader').props.title, 'Profile', 'the mounted Profile immediately updates after selection');
  assert.equal(chromeLocked, false);
  assert.equal(translationUpdates.length, 0, 'app-language selection must not mutate the content translation preference');
  assert.equal(translationPreference.target_locale, 'zh-CN');

  await act(async () => pressable('Profile, edit name, avatar and email').props.onPress());
  await act(async () => {
    input('Name').props.onChangeText('Unsubmitted name');
    input('Email').props.onChangeText('');
  });
  await act(async () => buttonText('Save profile').props.onPress());
  assert.ok(tree.root.findAll(node => node.type === 'Text' && node.children.join('') === 'Enter a valid email address.').length);
  await act(async () => appLanguageStore.setPreference('zh-CN'));
  assert.equal(input('昵称').props.value, 'Unsubmitted name', 'a language switch preserves the actual Profile editor draft');
  assert.equal(input('邮箱').props.value, '');
  assert.ok(tree.root.findAll(node => node.type === 'Text' && node.children.join('') === '请输入有效邮箱地址。').length, 'existing inline validation changes language without resetting the form');

  avatarPickerResult = { canceled: false, assets: [{ uri: 'data:image/png;base64,AQID', mimeType: 'image/png', fileName: 'avatar.png' }] };
  const avatarLimit = localizedMessage('profile:avatarTooLarge');
  avatarUploadError = new Error(resolveMessage(avatarLimit));
  bindLocalizedErrorMessage(avatarUploadError, avatarLimit);
  async function failAvatarUpload(pickerLabel) {
    const requested = new Promise(resolve => { onAvatarUpload = resolve; });
    await act(async () => {
      pressable(pickerLabel).props.onPress();
      await requested;
    });
  }
  await failAvatarUpload('从相册选择头像');
  assert.ok(tree.root.findAll(node => node.type === 'Text' && node.children.join('') === '头像图片不能超过 5 MB。').length);
  await act(async () => appLanguageStore.setPreference('en'));
  assert.ok(tree.root.findAll(node => node.type === 'Text' && node.children.join('') === 'Your avatar image must be 5 MB or smaller.').length, 'Profile retains the Error object so held app-owned upload errors change language');
  assert.equal(input('Name').props.value, 'Unsubmitted name');
  avatarUploadError = new Error('Provider image limit: 5 MB');
  await failAvatarUpload('Choose an avatar from your photo library');
  await act(async () => appLanguageStore.setPreference('zh-CN'));
  assert.ok(tree.root.findAll(node => node.type === 'Text' && node.children.join('') === 'Provider image limit: 5 MB').length, 'Profile preserves held arbitrary provider error text');
  await act(async () => pressable('关闭个人资料').props.onPress());
  await act(async () => setting('翻译语言').props.onPress());
  await act(async () => pressable('English').props.onPress());
  assert.equal(translationUpdates.length, 1, 'the independent content-language control really reaches the API spy');
  assert.deepEqual(translationUpdates[0][1], { targetLocale: 'en' });
  assert.deepEqual(appLanguageStore.getSnapshot(), { preference: 'zh-CN', language: 'zh-CN' }, 'content-language selection leaves interface language intact');
  console.log('PASS mounted Profile language controls, independent translation preference and retained drafts/feedback');
} finally {
  if (tree) await act(async () => tree.unmount());
  if (localization) await localization.i18n.changeLanguage('zh-CN');
  Module._load = originalLoad;
  restoreGlobal('localStorage', previousStorage);
  restoreGlobal('window', previousWindow);
  if (previousLocale) Object.defineProperty(Intl, 'Locale', previousLocale);
  else delete Intl.Locale;
  if (previousPluralRules) Object.defineProperty(Intl, 'PluralRules', previousPluralRules);
  else delete Intl.PluralRules;
  if (previousNodePath === undefined) delete process.env.NODE_PATH;
  else process.env.NODE_PATH = previousNodePath;
  Module._initPaths();
  rmSync(directory, { recursive: true, force: true });
}
