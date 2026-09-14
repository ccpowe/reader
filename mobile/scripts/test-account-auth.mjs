import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import Module from 'node:module';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-account-auth-'));
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
const Pressable = host('Pressable');
const Text = host('Text');
const focusedInputs = [];
const TextInput = React.forwardRef(function TextInput({ children, ...props }, ref) {
  React.useImperativeHandle(ref, () => ({ focus: () => focusedInputs.push(props.accessibilityLabel) }), [props.accessibilityLabel]);
  return React.createElement('TextInput', props, children);
});
const View = host('View');
const ScrollView = host('ScrollView');
const KeyboardAvoidingView = host('KeyboardAvoidingView');
const StyleSheet = { create: (styles) => styles };
let generation = 1;
let activeRuntime = null;
let safeAreaTop = 24;

try {
  execFileSync(
    process.execPath,
    [
      join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
      '--ignoreConfig',
      'screens/EmailAuthScreen.tsx',
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
    if (request === 'react-native') {
      return {
        ActivityIndicator,
        Image: host('Image'),
        KeyboardAvoidingView,
        Linking: { addEventListener: () => ({ remove: () => {} }), getInitialURL: async () => null },
        Platform: { OS: 'android' },
        Pressable,
        ScrollView,
        StyleSheet,
        Text,
        TextInput,
        View,
      };
    }
    if (request === 'react-native-safe-area-context') return {
      useSafeAreaInsets: () => ({ top: safeAreaTop, bottom: 16, left: 0, right: 0 }),
    };
    if (request.endsWith('.png')) return 1;
    if (request === '@expo/vector-icons') return { Feather: host('Feather'), MaterialCommunityIcons: host('MaterialCommunityIcons') };
    if (request === '../lib/connection' || request === './connection') return {
      getActiveRuntime: () => activeRuntime,
      getConnectionGeneration: () => generation,
    };
    return originalLoad.call(this, request, parent, isMain);
  };

  const { EmailAuthScreen } = require(join(outputDirectory, 'screens/EmailAuthScreen.js'));
  const input = (tree, label) => tree.root.findAll((node) => node.type === 'TextInput' && node.props.accessibilityLabel === label)[0];
  const button = (tree, label) => tree.root.findAll((node) => node.type === 'Pressable' && node.findAll((child) => child.type === 'Text' && child.children.join('').includes(label)).length > 0)[0];
  const session = { access_token: 'access-token', user: { id: 'user-1', email: 'reader@example.com', user_metadata: {} } };

  let releaseSignup;
  const signupRequests = [];
  const signupClient = { auth: {
    signUp: body => {
      signupRequests.push(body);
      return new Promise(resolve => { releaseSignup = () => resolve({ data: { session }, error: null }); });
    },
  } };
  let tree;
  const texts = () => tree.root.findAll(node => node.type === 'Text').map(node => node.children.join('')).join(' ');
  const mount = async client => {
    if (tree) await act(async () => tree.unmount());
    await act(async () => { tree = create(React.createElement(EmailAuthScreen, { client, generation, serverUrl: 'https://reader.example.com' })); });
  };
  await mount(signupClient);
  assert.equal(tree.root.findByType('KeyboardAvoidingView').props.behavior, 'height');
  assert.equal(tree.root.findByType('KeyboardAvoidingView').props.keyboardVerticalOffset, 24, 'Android keyboard avoidance accounts for the parent top safe area');
  safeAreaTop = 0;
  await act(async () => { tree.update(React.createElement(EmailAuthScreen, { client: signupClient, generation })); });
  assert.equal(tree.root.findByType('KeyboardAvoidingView').props.keyboardVerticalOffset, 0, 'layouts with no top inset do not acquire a phantom keyboard gap');
  safeAreaTop = 24;
  await act(async () => { tree.update(React.createElement(EmailAuthScreen, { client: signupClient, generation })); });
  assert.equal(tree.root.findByType('KeyboardAvoidingView').props.keyboardVerticalOffset, 24, 'safe-area changes update keyboard coordinate correction');
  await act(async () => button(tree, '创建账号').props.onPress());
  assert.equal(input(tree, '邮箱').props.submitBehavior, 'submit', 'email Next keeps the keyboard open while advancing focus');
  await act(async () => input(tree, '邮箱').props.onSubmitEditing());
  assert.deepEqual(focusedInputs, ['密码'], 'signup email Next focuses the password input');
  assert.equal(signupRequests.length, 0, 'advancing to password does not submit signup');
  await act(async () => {
    input(tree, '邮箱').props.onChangeText('reader@example.com');
    input(tree, '密码').props.onChangeText('old123');
  });
  await act(async () => { button(tree, '创建账号').props.onPress(); await Promise.resolve(); });
  assert.equal(signupRequests.length, 0, 'new accounts require at least eight password characters');
  await act(async () => {
    input(tree, '邮箱').props.onChangeText('  reader@example.com  ');
    input(tree, '密码').props.onChangeText('password');
  });
  await act(async () => {
    const submit = button(tree, '创建账号').props.onPress;
    input(tree, '密码').props.onSubmitEditing();
    submit();
    submit();
    await Promise.resolve();
  });
  assert.equal(signupRequests.length, 1, 'same-tick keyboard and button submissions share the duplicate guard');
  assert.deepEqual(signupRequests[0], { email: 'reader@example.com', password: 'password' });
  assert.ok(tree.root.findAll(node => node.props.accessibilityState?.busy === true).length > 0);
  releaseSignup();
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  assert.equal(tree.root.findAll(node => node.props.accessibilityState?.busy === true).length, 0);
  assert.doesNotMatch(texts(), /请检查邮箱|重发验证邮件|验证邮件已/);

  const loginRequests = [];
  const loginClient = { auth: {
    signInWithPassword: async body => { loginRequests.push(body); return { data: { session }, error: null }; },
  } };
  await mount(loginClient);
  focusedInputs.length = 0;
  await act(async () => input(tree, '邮箱').props.onSubmitEditing());
  assert.deepEqual(focusedInputs, ['密码'], 'login email Next focuses the password input');
  assert.equal(loginRequests.length, 0, 'advancing to password does not submit login');
  await act(async () => {
    input(tree, '邮箱').props.onChangeText('reader@example.com');
    input(tree, '密码').props.onChangeText('password');
  });
  await act(async () => { input(tree, '密码').props.onSubmitEditing(); await Promise.resolve(); });
  assert.deepEqual(loginRequests, [{ email: 'reader@example.com', password: 'password' }]);
  await act(async () => { input(tree, '密码').props.onChangeText('old123'); });
  await act(async () => { input(tree, '密码').props.onSubmitEditing(); await Promise.resolve(); });
  assert.equal(loginRequests.at(-1).password, 'old123', 'migrated accounts retain access with a legacy six-character password');
  assert.equal(input(tree, '密码').props.secureTextEntry, true);
  await act(async () => tree.root.find(node => node.type === 'Pressable' && node.props.accessibilityLabel === '显示密码').props.onPress());
  assert.equal(input(tree, '密码').props.secureTextEntry, false);
  await act(async () => button(tree, '忘记密码？').props.onPress());
  assert.match(texts(), /联系服务器管理员/);
  assert.equal(tree.root.findAllByType('TextInput').length, 0, 'password recovery only explains administrator assistance');
  assert.equal(loginRequests.length, 2, 'forgot password sends no network or mail request');
  assert.equal(button(tree, '发送重置链接'), undefined);
  await act(async () => button(tree, '返回登录').props.onPress());
  assert.equal(input(tree, '邮箱').props.value, 'reader@example.com');
  assert.equal(input(tree, '密码').props.secureTextEntry, true, 'navigation hides previously revealed passwords');

  for (const failure of [
    async () => ({ data: { session: null }, error: new Error('Invalid credentials') }),
    async () => { throw new Error('Network unavailable'); },
  ]) {
    await mount({ auth: { signInWithPassword: failure } });
    await act(async () => {
      input(tree, '邮箱').props.onChangeText('reader@example.com');
      input(tree, '密码').props.onChangeText('password');
    });
    await act(async () => { button(tree, '登录').props.onPress(); await Promise.resolve(); });
    assert.ok(tree.root.findAllByProps({ accessibilityRole: 'alert' }).length > 0, 'resolved and thrown failures stay inline and accessible');
    assert.equal(tree.root.findAll(node => node.props.accessibilityState?.busy === true).length, 0, 'failed requests restore submission');
  }

  let finishOldLogin;
  await mount({ auth: { signInWithPassword: () => new Promise(resolve => { finishOldLogin = resolve; }) } });
  await act(async () => {
    input(tree, '邮箱').props.onChangeText('reader@example.com');
    input(tree, '密码').props.onChangeText('password');
  });
  await act(async () => { button(tree, '登录').props.onPress(); await Promise.resolve(); });
  generation += 1;
  await act(async () => { finishOldLogin({ data: { session: null }, error: new Error('old server failure') }); await Promise.resolve(); });
  assert.doesNotMatch(texts(), /old server failure/, 'late auth results cannot alter the new server surface');
  await act(async () => tree.unmount());

} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}

console.log('Mounted Reader registration, login, administrator recovery and stale-generation guards passed.');
