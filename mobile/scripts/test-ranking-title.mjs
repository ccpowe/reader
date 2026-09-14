import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import Module from 'node:module';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-ranking-title-'));
const require = createRequire(import.meta.url);
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();

try {
  execFileSync(
    process.execPath,
    [
      join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
      '--ignoreConfig',
      'components/RankingTitle.tsx',
      'hooks/useRankingTitleRetry.ts',
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
  let currentRuntime = {
    generation: 1,
    identity: { server_id: 'ranking-test', api_base_url: 'https://reader.test' },
  };
  let generation = 1;
  const requests = [];
  const originalLoad = Module._load;
  Module._load = function load(request, parent, isMain) {
    if (request === 'react-native') {
      return {
        Pressable: 'Pressable',
        StyleSheet: { create: (styles) => styles },
        Text: 'Text',
        View: 'View',
      };
    }
    if (request === '../lib/api') {
      return {
        resolveTranslationSegments: async (...args) => {
          const deferred = { args };
          requests.push(deferred);
          return await new Promise((resolve, reject) => {
            deferred.resolve = resolve;
            deferred.reject = reject;
          });
        },
      };
    }
    if (request === '../lib/connection/react') {
      return {
        useReaderRuntime: () => currentRuntime,
        useReaderRuntimeGeneration: () => generation,
      };
    }
    if (request === '../lib/connection') {
      return {
        captureRuntimeContext: (runtime) => runtime === currentRuntime
          ? { generation, runtime, serverId: runtime.identity.server_id }
          : null,
        isRuntimeContextCurrent: (context) => context?.runtime === currentRuntime && context.generation === generation,
      };
    }
    return originalLoad.call(this, request, parent, isMain);
  };

  const { RankingTitle } = require(join(outputDirectory, 'components/RankingTitle.js'));
  const { useRankingTitleRetry } = require(join(outputDirectory, 'hooks/useRankingTitleRetry.js'));
  const item = { title: 'Original ranking title', translation_key: 'ranking-1' };

  function Harness() {
    const [state, setState] = React.useState({ status: 'failed', translatedTitle: '不应显示' });
    const onResults = React.useCallback((results) => {
      const result = results[0];
      setState({ status: result.translation_status, translatedTitle: result.translated_text });
    }, []);
    const onFailure = React.useCallback(() => {
      setState({ status: 'failed', translatedTitle: null });
    }, []);
    const retry = useRankingTitleRetry({ onFailure, onResults, session: { access_token: 'test-token', user: { id: 'user-1' } } });
    return React.createElement(RankingTitle, {
      onRetry: () => { void retry.retryTitleTranslation(item); },
      retrying: retry.isTitleRetrying(item),
      status: state.status,
      title: item.title,
      translatedTitle: state.translatedTitle,
    });
  }

  let tree;
  await act(async () => { tree = create(React.createElement(Harness)); });
  assert.equal(tree.root.findAllByProps({ accessibilityRole: 'button' }).length, 1);
  const textValues = () => tree.root.findAll((node) => node.type === 'Text').map((node) => node.children.join(''));
  assert.ok(textValues().includes('Original ranking title'));
  assert.ok(textValues().includes('翻译失败'));

  const retryButton = tree.root.findAllByProps({ accessibilityRole: 'button' })[0];
  await act(async () => {
    retryButton.props.onPress({ stopPropagation: () => {} });
    retryButton.props.onPress({ stopPropagation: () => {} });
    await Promise.resolve();
  });
  assert.equal(requests.length, 1, 'duplicate taps must issue exactly one interactive retry');
  assert.equal(
    tree.root.findAll((node) => node.props.accessibilityState?.busy === true).length,
    1,
    'retry exposes an accessible busy state',
  );
  requests[0].resolve([{
    error_code: null,
    error_retryable: null,
    engine_id: 'test',
    engine_label: 'test',
    purpose: 'ranking_title',
    retry_after_ms: null,
    segment_id: 'ranking-1:title',
    translated_text: 'Translated ranking title',
    translation_locale: 'zh-CN',
    translation_status: 'succeeded',
  }]);
  await act(async () => { await Promise.resolve(); });
  assert.equal(tree.root.findAllByProps({ accessibilityRole: 'button' }).length, 0, 'success removes the failed affordance');
  assert.ok(textValues().includes('Translated ranking title'));

  // A runtime switch makes the in-flight result stale; no UI callback may run.
  let staleResults = 0;
  function StaleHarness() {
    const retry = useRankingTitleRetry({
      onResults: () => { staleResults += 1; },
      session: { access_token: 'test-token', user: { id: 'user-1' } },
    });
    return React.createElement(RankingTitle, { onRetry: () => { void retry.retryTitleTranslation(item); }, status: 'failed', title: item.title });
  }
  await act(async () => { tree = create(React.createElement(StaleHarness)); });
  await act(async () => {
    tree.root.findAllByProps({ accessibilityRole: 'button' })[0].props.onPress({ stopPropagation: () => {} });
    await Promise.resolve();
  });
  assert.equal(requests.length, 2);
  currentRuntime = { ...currentRuntime, generation: 2, identity: { ...currentRuntime.identity, server_id: 'ranking-next' } };
  generation = 2;
  requests[1].resolve([{
    error_code: null,
    error_retryable: null,
    engine_id: 'test',
    engine_label: 'test',
    purpose: 'ranking_title',
    retry_after_ms: null,
    segment_id: 'ranking-1:title',
    translated_text: 'stale result',
    translation_locale: 'zh-CN',
    translation_status: 'succeeded',
  }]);
  await act(async () => { await Promise.resolve(); });
  assert.equal(staleResults, 0, 'runtime-stale retry results must be ignored');
} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}

console.log('Mounted RankingTitle and title-retry behavior passed.');
