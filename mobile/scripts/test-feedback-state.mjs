import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { createRequire, Module } from 'node:module';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';

const require = createRequire(import.meta.url);
const React = require('react');
const jsxRuntime = require('react/jsx-runtime');
const { act, create } = require('react-test-renderer');
const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const directory = mkdtempSync(join(tmpdir(), 'reader-feedback-state-'));
const originalLoad = Module._load;
const previousActEnvironment = Object.getOwnPropertyDescriptor(globalThis, 'IS_REACT_ACT_ENVIRONMENT');
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
let tree;

try {
  buildSync({
    entryPoints: [join(mobileRoot, 'components/FeedbackState.tsx')],
    outfile: join(directory, 'feedback-state.cjs'),
    bundle: true,
    platform: 'node',
    format: 'cjs',
    jsx: 'automatic',
    packages: 'external',
    external: ['../i18n'],
  });
  Module._load = function load(request, parent, isMain) {
    if (request === 'react') return React;
    if (request === 'react/jsx-runtime') return jsxRuntime;
    if (request === 'react-native') return {
      ActivityIndicator: 'ActivityIndicator',
      Pressable: 'Pressable',
      StyleSheet: { create: styles => styles },
      Text: 'Text',
      View: 'View',
    };
    if (request === '@expo/vector-icons') return { MaterialCommunityIcons: 'Icon' };
    if (request === '../i18n') return { useTranslation: () => ({ t: key => key }) };
    return originalLoad.call(this, request, parent, isMain);
  };

  const { ErrorState } = require(join(directory, 'feedback-state.cjs'));
  const requests = [];
  const onRetry = () => new Promise((resolve, reject) => { requests.push({ resolve, reject }); });
  const button = () => tree.root.findByType('Pressable');
  const assertIdle = () => {
    assert.equal(button().props.disabled, false, 'settled retries leave the action enabled');
    assert.deepEqual(button().props.accessibilityState, { busy: false, disabled: false });
    assert.equal(tree.root.findAllByType('ActivityIndicator').length, 0);
  };

  await act(async () => {
    tree = create(React.createElement(ErrorState, { message: 'Network unavailable', onRetry }));
  });
  assertIdle();
  await act(async () => {
    const press = button().props.onPress;
    press();
    press();
  });
  assert.equal(requests.length, 1, 'two taps in the same render start one request');
  assert.equal(button().props.disabled, true);
  assert.deepEqual(button().props.accessibilityState, { busy: true, disabled: true });
  assert.equal(button().props.accessibilityLabel, 'retrying');
  assert.equal(tree.root.findAllByType('ActivityIndicator').length, 1, 'the real pending request has visible progress');
  await act(async () => { button().props.onPress(); });
  assert.equal(requests.length, 1, 'the pending action also guards programmatic duplicate presses');
  await act(async () => { requests[0].resolve(); });
  assertIdle();

  await act(async () => { button().props.onPress(); });
  assert.equal(requests.length, 2, 'success releases the retry lock');
  await act(async () => { requests[1].reject(new Error('Still offline')); });
  assertIdle();
  assert.ok(tree.root.findAllByType('Text').some(node => node.children.includes('Network unavailable')),
    'a failed retry preserves the caller-owned error feedback');
  await act(async () => { button().props.onPress(); });
  assert.equal(requests.length, 3, 'failure releases the retry lock');
  await act(async () => { requests[2].resolve(); });
  assertIdle();

  let synchronousCalls = 0;
  await act(async () => {
    tree.update(React.createElement(ErrorState, {
      message: 'Network unavailable',
      onRetry: () => { synchronousCalls += 1; },
    }));
  });
  for (let attempt = 0; attempt < 2; attempt += 1) {
    await act(async () => { button().props.onPress(); });
    assertIdle();
  }
  assert.equal(synchronousCalls, 2, 'synchronous callbacks do not leave progress or a stuck lock');

  let thrownCalls = 0;
  await act(async () => {
    tree.update(React.createElement(ErrorState, {
      message: 'Network unavailable',
      onRetry: () => { thrownCalls += 1; throw new Error('Retry failed immediately'); },
    }));
  });
  for (let attempt = 0; attempt < 2; attempt += 1) {
    await act(async () => { button().props.onPress(); });
    assertIdle();
  }
  assert.equal(thrownCalls, 2, 'synchronously thrown failures also leave retry available');
} finally {
  if (tree) await act(async () => { tree.unmount(); });
  Module._load = originalLoad;
  if (previousActEnvironment) Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', previousActEnvironment);
  else delete globalThis.IS_REACT_ACT_ENVIRONMENT;
  rmSync(directory, { force: true, recursive: true });
}

console.log('Mounted feedback retry pending, duplicate-tap, failure and synchronous behavior passed.');
