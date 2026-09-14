import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import Module from 'node:module';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-profile-account-'));
const require = createRequire(import.meta.url);
const React = require('react');
const { act, create } = require('react-test-renderer');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();

const host = (name) => function Host({ children, ...props }) {
  return React.createElement(name, props, children);
};
const ActivityIndicator = host('ActivityIndicator');
const Image = host('Image');
const Pressable = host('Pressable');
const ScrollView = host('ScrollView');
const Switch = host('Switch');
const Text = host('Text');
const TextInput = host('TextInput');
const View = host('View');
const StyleSheet = { create: (styles) => styles };
let generation = 1;
let currentRuntime = { generation, identity: { server_id: 'profile-test', api_base_url: 'https://reader.test' } };
let updateProfileCalls = [];
let avatarUploadCalls = [];
let resolveUpdate;
let rejectUpdate;
let imagePickerResult = { canceled: true, assets: null };
globalThis.fetch = async () => ({ arrayBuffer: async () => new ArrayBuffer(128) });

const storedProfile = {
  avatar_url: 'https://cdn.example/avatar.png',
  display_name: 'Reader',
  email: 'reader@example.com',
  id: 'user-1',
};

try {
  execFileSync(
    process.execPath,
    [
      join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
      '--ignoreConfig',
      'screens/ProfileScreen.tsx',
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

  const originalLoad = Module._load;
  Module._load = function load(request, parent, isMain) {
    if (request === 'react-native') return { ActivityIndicator, Alert: { alert: () => {} }, Image, Pressable, ScrollView, StyleSheet, Switch, Text, TextInput, View };
    if (request === 'react-native-reanimated') return { __esModule: true, default: { ScrollView, View }, useAnimatedScrollHandler: (handlers) => handlers, useAnimatedStyle: (factory) => factory(), useSharedValue: (value) => React.useRef({ value }).current, cancelAnimation: () => {}, withTiming: (value) => value, Easing: { out: (value) => value, cubic: 'cubic' } };
    if (request === '@expo/vector-icons') return { Feather: host('Feather') };
    if (request === 'expo-image-picker') return { launchImageLibraryAsync: async () => imagePickerResult };
    if (request === '@tanstack/react-query') {
      return {
        useMutation: () => ({ isPending: false, mutate: () => {} }),
        useQuery: () => ({ data: storedProfile, isPending: false }),
        useQueryClient: () => ({ setQueryData: () => {} }),
      };
    }
    if (request === '../components/PageHeader') return { PageHeaderContent: ({ title, right }) => React.createElement(View, null, React.createElement(Text, null, title), right) };
    if (request === '../components/BottomSheetModal') return { BottomSheetModal: ({ children, visible }) => visible ? React.createElement('BottomSheetModal', null, children) : null };
    if (request === '../hooks/useTranslationPreference') return {
      useTranslationPreference: () => ({ data: { is_enabled: true, provider_mode: 'app_default', supported_locales: ['zh-CN'], managed_engines: [] }, enabled: true, engineAvailable: true, engineLabel: 'DeepSeek', engineSubtitle: 'DeepSeek', isPending: false, targetLocale: 'zh-CN' }),
    };
    if (request === '../lib/connection/react') return { useReaderRuntime: () => currentRuntime };
    if (request === '../lib/connection') return {
      captureRuntimeContext: () => ({ generation, runtime: currentRuntime, serverId: currentRuntime.identity.server_id }),
      getConnectionGeneration: () => generation,
      isRuntimeContextCurrent: () => true,
    };
    if (request === '../state/invalidation') return { invalidateAfterTranslationPreferenceMutation: async () => {} };
    if (request === '../state/queryClient') return { readerQueryKeys: { profile: (...args) => ['profile', ...args], translationPreference: (...args) => ['translation', ...args] } };
    if (request === '../lib/api') return {
      uploadAvatar: async (...args) => {
        avatarUploadCalls.push(args);
        return { ...storedProfile, avatar_url: 'https://reader.test/v1/profiles/user-1/avatar' };
      },
      getProfile: async () => storedProfile,
      updateProfile: async (...args) => {
        updateProfileCalls.push(args);
        return new Promise((resolve, reject) => { resolveUpdate = resolve; rejectUpdate = reject; });
      },
      updateTranslationPreference: async () => ({}),
    };
    return originalLoad.call(this, request, parent, isMain);
  };

  const { ProfileScreen } = require(join(outputDirectory, 'screens/ProfileScreen.js'));
  const session = { access_token: 'access-token', user: { id: 'user-1', email: 'reader@example.com', user_metadata: {} } };
  let authUpdateCalls = [];
  let passwordUpdateCalls = [];
  const client = {
    auth: {
      updateEmail: async (...args) => { authUpdateCalls.push(args); return { data: { session }, error: null }; },
      updatePassword: async (...args) => { passwordUpdateCalls.push(args); return { data: { session }, error: null }; },
    },
  };
  const props = { active: true, chromeProgress: { value: 0 }, onChangeServer: () => {}, onLogout: async () => {}, session, authClient: client };
  let tree;
  await act(async () => { tree = create(React.createElement(ProfileScreen, props)); });
  const scroll = () => tree.root.findByType('ScrollView').props.onScroll;
  const scrollEvent = (y, height = 1600) => ({ contentOffset: { y }, contentSize: { height }, layoutMeasurement: { height: 700 } });
  await act(async () => { scroll().onBeginDrag(scrollEvent(0)); scroll().onScroll(scrollEvent(104)); });
  assert.equal(props.chromeProgress.value, 1, 'profile scroll drives shared navigation progress');
  await act(async () => { scroll().onScroll(scrollEvent(52)); });
  assert.equal(props.chromeProgress.value, 0.5, 'profile reversal follows the finger');
  await act(async () => { scroll().onBeginDrag(scrollEvent(0, 750)); scroll().onScroll(scrollEvent(40, 750)); });
  assert.equal(props.chromeProgress.value, 0, 'short profile content leaves navigation visible');
  const byText = (value) => tree.root.findAll((node) => node.type === 'Pressable' && node.findAll((child) => child.type === 'Text' && child.children.join('').includes(value)).length > 0)[0];
  const input = (label) => tree.root.findAll((node) => node.type === 'TextInput' && node.props.accessibilityLabel === label)[0];

  await act(async () => { tree.root.find((node) => node.type === 'Pressable' && node.props.accessibilityLabel === '个人资料，编辑昵称、头像和邮箱').props.onPress(); });
  assert.equal(props.chromeProgress.value, 0, 'opening a sheet reveals navigation');
  await act(async () => { scroll().onBeginDrag(scrollEvent(0)); scroll().onScroll(scrollEvent(104)); });
  assert.equal(props.chromeProgress.value, 0, 'profile sheet locks background chrome');
  assert.ok(input('昵称'), 'profile editor exposes nickname');
  assert.ok(byText('从相册选择'), 'profile editor exposes device avatar picker');
  assert.ok(input('邮箱'), 'profile editor exposes email');
  imagePickerResult = { canceled: false, assets: [{ fileName: 'avatar.jpg', fileSize: 128, mimeType: 'image/jpeg', uri: 'file:///avatar.jpg' }] };
  await act(async () => { byText('从相册选择').props.onPress(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); });
  assert.equal(avatarUploadCalls.length, 1, 'avatar picker uploads directly through the authenticated Reader API');
  assert.equal(avatarUploadCalls[0][1].contentType, 'image/jpeg');
  assert.equal(avatarUploadCalls[0][1].body.byteLength, 128);
  await act(async () => {
    input('昵称').props.onChangeText('Updated Reader');
    input('邮箱').props.onChangeText('new@example.com');
  });
  await act(async () => { input('当前密码').props.onChangeText('current-password'); });
  await act(async () => { byText('保存资料').props.onPress(); await Promise.resolve(); });
  assert.equal(updateProfileCalls.length, 1, 'profile save calls the production PATCH helper once');
  assert.equal(authUpdateCalls.length, 0, 'email change waits for profile PATCH');
  assert.ok(tree.root.findAll((node) => node.props.accessibilityState?.busy === true).length >= 1, 'profile save exposes busy state');
  resolveUpdate({ ...storedProfile, display_name: 'Updated Reader' });
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  assert.equal(authUpdateCalls.length, 1, 'email change calls the Reader auth API once');
  assert.deepEqual(authUpdateCalls[0], [{ email: 'new@example.com', current_password: 'current-password' }]);
  assert.equal(tree.root.findAllByType('BottomSheetModal').length, 0, 'successful immediate email change closes the editor');

  await act(async () => { tree.update(React.createElement(ProfileScreen, props)); });
  await act(async () => { tree.root.find((node) => node.type === 'Pressable' && node.props.accessibilityLabel === '个人资料，编辑昵称、头像和邮箱').props.onPress(); });
  await act(async () => { input('昵称').props.onChangeText('Failure'); });
  await act(async () => { byText('保存资料').props.onPress(); await Promise.resolve(); });
  rejectUpdate(new Error('profile failed'));
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  assert.ok(tree.root.findAllByProps({ accessibilityRole: 'alert' }).length >= 1, 'profile failure remains inline and accessible');
  assert.equal(tree.root.findAll((node) => node.props.accessibilityState?.busy === true).length, 0, 'profile failure recovers busy state');
  await act(async () => { tree.root.find(node => node.type === 'Pressable' && node.props.accessibilityLabel === '关闭个人资料').props.onPress(); });
  await act(async () => { byText('修改密码').props.onPress(); });
  assert.equal(input('当前密码').props.secureTextEntry, true);
  await act(async () => {
    input('当前密码').props.onChangeText('current-password');
    input('新密码').props.onChangeText('replacement-password');
    input('确认新密码').props.onChangeText('different-password');
  });
  await act(async () => { byText('更新密码').props.onPress(); await Promise.resolve(); });
  assert.equal(passwordUpdateCalls.length, 0, 'mismatched passwords are rejected before mutation');
  await act(async () => { input('确认新密码').props.onChangeText('replacement-password'); });
  await act(async () => { byText('更新密码').props.onPress(); await Promise.resolve(); });
  assert.deepEqual(passwordUpdateCalls, [[{ current_password: 'current-password', password: 'replacement-password' }]]);
  assert.equal(tree.root.findAllByType('BottomSheetModal').length, 0);
  await act(async () => tree.unmount());

} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}

console.log('Mounted ProfileScreen account flow passed.');
