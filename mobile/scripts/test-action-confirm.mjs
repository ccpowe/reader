import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import Module from 'node:module';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-action-confirm-'));
const require = createRequire(import.meta.url);

try {
  execFileSync(
    process.execPath,
    [
      join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
      '--ignoreConfig',
      'lib/connection/errors.ts',
      'components/ActionConfirm.tsx',
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

  const React = require('react');
  const { act, create } = require('react-test-renderer');
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  process.env.NODE_PATH = join(mobileRoot, 'node_modules');
  Module._initPaths();
  const originalLoad = Module._load;
  Module._load = function load(request, parent, isMain) {
    if (request === 'react-native') {
      return {
        ActivityIndicator: 'ActivityIndicator',
        Pressable: 'Pressable',
        StyleSheet: { create: (styles) => styles },
        Text: 'Text',
        View: 'View',
      };
    }
    return originalLoad.call(this, request, parent, isMain);
  };

  const { ActionConfirm } = require(join(outputDirectory, 'components/ActionConfirm.js'));
  const element = (props) => React.createElement(ActionConfirm, {
    cancelLabel: '取消',
    confirmLabel: '执行操作',
    message: '请确认此操作。',
    onCancel: props.onCancel ?? (() => {}),
    onConfirm: props.onConfirm,
    isCurrent: props.isCurrent,
    title: '确认操作',
  });

  let cancelled = false;
  let tree;
  await act(async () => {
    tree = create(element({ onConfirm: () => {}, onCancel: () => { cancelled = true; } }));
  });
  const buttons = tree.root.findAllByProps({ accessibilityRole: 'button' });
  assert.equal(buttons.length, 2);
  buttons[0].props.onPress();
  assert.equal(cancelled, true, 'cancel must be operable on Web/native host elements');

  let release;
  await act(async () => {
    tree = create(element({ onConfirm: () => new Promise((resolve) => { release = resolve; }) }));
  });
  await act(async () => {
    tree.root.findAllByProps({ accessibilityRole: 'button' })[1].props.onPress();
    await Promise.resolve();
  });
  assert.equal(
    tree.root.findAll((node) => node.props.accessibilityState?.busy === true).length,
    1,
    'confirm must expose busy state while the mutation is pending',
  );
  release();
  await act(async () => { await Promise.resolve(); });
  assert.equal(
    tree.root.findAll((node) => node.props.accessibilityState?.busy === false).length,
    1,
    'busy state must recover after the mutation resolves',
  );

  await act(async () => {
    tree = create(element({ onConfirm: async () => { throw new Error('server mutation failed'); } }));
  });
  await act(async () => {
    tree.root.findAllByProps({ accessibilityRole: 'button' })[1].props.onPress();
    await Promise.resolve();
  });
  const errors = tree.root.findAllByProps({ accessibilityRole: 'alert' });
  assert.equal(errors.length, 1, 'mutation failure must remain inline and accessible');
  assert.equal(errors[0].props.accessibilityLiveRegion, 'assertive');
  assert.match(errors[0].children.join(''), /server mutation failed/);

  let current = true;
  await act(async () => {
    tree = create(element({
      isCurrent: () => current,
      onConfirm: async () => {
        current = false;
        throw new Error('stale mutation failed');
      },
    }));
  });
  await act(async () => {
    tree.root.findAllByProps({ accessibilityRole: 'button' })[1].props.onPress();
    await Promise.resolve();
  });
  assert.equal(tree.root.findAllByProps({ accessibilityRole: 'alert' }).length, 0, 'stale failure must be ignored');
} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}

console.log('Mounted ActionConfirm Web/native behavior passed.');
