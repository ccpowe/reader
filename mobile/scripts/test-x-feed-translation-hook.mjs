import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import Module from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-x-feed-translation-hook-'));
const require = createRequire(import.meta.url);
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();

try {
  execFileSync(process.execPath, [
    join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
    '--ignoreConfig', 'hooks/useXFeedTranslation.ts',
    '--target', 'es2022', '--module', 'commonjs', '--jsx', 'react-jsx',
    '--outDir', outputDirectory, '--lib', 'dom,es2022', '--types', 'node', '--skipLibCheck',
  ], { cwd: mobileRoot, stdio: 'inherit' });

  const React = require('react');
  const { act, create } = require('react-test-renderer');
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const requests = [];
  let runtimeGeneration = 1;
  let runtime = { generation: runtimeGeneration, identity: { server_id: 'x-feed-test' } };
  const originalLoad = Module._load;
  Module._load = function load(request, parent, isMain) {
    if (request === '../lib/api') return {
      resolveTranslationSegments: async (_session, segments, signal) => {
        const deferred = { segments, signal };
        requests.push(deferred);
        return await new Promise((resolve, reject) => { deferred.resolve = resolve; deferred.reject = reject; });
      },
    };
    if (request === '../lib/connection/react') return {
      useReaderRuntime: () => runtime,
      useReaderRuntimeGeneration: () => runtimeGeneration,
    };
    if (request === '../lib/connection') return {
      captureRuntimeContext: () => ({ generation: runtimeGeneration, runtime, serverId: runtime.identity.server_id }),
      isRuntimeContextCurrent: (context) => context.generation === runtimeGeneration && context.runtime === runtime,
    };
    if (request === '../domain/translationRetry') return {
      translationTransportRetryDecision: (cause, previousFailureCount) => cause?.stop
        ? { delayMs: null, failureCount: previousFailureCount + 1, retry: false }
        : { delayMs: 1, failureCount: previousFailureCount + 1, retry: true },
    };
    if (request === '../domain/translationConvergence') return {
      nextTranslationConvergencePollAt: (_startedAt, now) => now + 1,
      segmentConvergenceTimeoutMessage: () => 'timed out',
      translationConvergenceDecision: (status) => status === 'pending' || status === 'running' ? 'retry' : 'settled',
    };
    if (request === '../i18n') return {
      i18n: { t: (key) => key },
      useTranslation: () => ({ t: (key) => key }),
    };
    return originalLoad.call(this, request, parent, isMain);
  };

  const { useXFeedTranslation } = require(join(outputDirectory, 'hooks/useXFeedTranslation.js'));
  const item = (contentId, text) => ({
    content_id: contentId,
    excerpt: text,
    source_kind: 'x',
    title: text,
    x_preview: {
      author: { avatar_url: null, handle: contentId, name: contentId },
      completeness: 'parsed',
      external_url: `https://x.com/${contentId}`,
      is_repost: false,
      quote: null,
      repost: null,
      text,
      tweet_id: contentId,
    },
  });
  const succeeded = (segmentId, translatedText) => ({
    error_code: null,
    engine_label: 'test',
    purpose: 'paragraph',
    segment_id: segmentId,
    translated_text: translatedText,
    translation_status: 'succeeded',
  });
  const failed = (segmentId) => ({
    ...succeeded(segmentId, null),
    error_code: 'provider_error',
    translation_status: 'failed',
  });
  const waitForPump = () => new Promise((resolve) => setTimeout(resolve, 20));
  let props;
  let result;
  function Harness() {
    result = useXFeedTranslation(
      props.session,
      props.items,
      true,
      true,
      'zh-CN',
      props.engineId,
      props.scopeKey,
    );
    return null;
  }
  const mount = async (nextProps) => {
    props = nextProps;
    let tree;
    await act(async () => { tree = create(React.createElement(Harness)); });
    await act(waitForPump);
    return tree;
  };
  const update = async (tree, nextProps) => {
    props = nextProps;
    await act(async () => { tree.update(React.createElement(Harness)); });
    await act(waitForPump);
  };
  const sessionA = { access_token: 'token-a', user: { id: 'user-a' } };
  const segmentId = (contentId) => `x-feed:${contentId}:body`;

  let tree = await mount({ engineId: 'engine-a', items: [item('a', 'service failure')], scopeKey: 'all', session: sessionA });
  await act(async () => { requests[0].resolve([failed(segmentId('a'))]); await waitForPump(); });
  assert.equal(result.byContentId.get('a').retryable, true, 'a terminal service failure stays retryable');

  const longB = `transport ${'b'.repeat(3_000)}`;
  const longC = `success ${'c'.repeat(3_000)}`;
  await update(tree, { engineId: 'engine-a', items: [item('a', 'service failure'), item('b', longB), item('c', longC)], scopeKey: 'all', session: sessionA });
  const requestB = requests.find((request) => request.segments[0].segment_id === segmentId('b'));
  const requestC = requests.find((request) => request.segments[0].segment_id === segmentId('c'));
  assert.ok(requestB && requestC, 'large independent segments occupy concurrent requests');
  await act(async () => {
    requestB.reject({ stop: true });
    requestC.resolve([succeeded(segmentId('c'), '成功译文')]);
    await waitForPump();
  });
  assert.equal(result.byContentId.get('a').retryable, true, 'an earlier service failure survives later queue outcomes');
  assert.equal(result.byContentId.get('b').retryable, true, 'a stopped transport failure is assigned to its segment');
  assert.equal(result.byContentId.get('c').bodyText, '成功译文', 'a concurrent success still renders');
  assert.equal(result.byContentId.get('c').retryable, false, 'a concurrent success does not inherit another segment failure');
  assert.equal(requests.filter((request) => request.segments[0].segment_id === segmentId('b')).length, 1,
    'an unrelated successful render cannot automatically reopen a stopped transport failure');
  await act(async () => { tree.unmount(); });

  requests.splice(0);
  tree = await mount({ engineId: 'engine-a', items: [item('same', 'old source')], scopeKey: 'all', session: sessionA });
  const oldRequest = requests[0];
  await update(tree, { engineId: 'engine-a', items: [item('same', 'new source')], scopeKey: 'all', session: sessionA });
  const newRequest = requests.find((request) => request !== oldRequest && request.segments[0].text === 'new source');
  assert.ok(newRequest, 'changed source text starts a new version under the stable content id');
  await act(async () => {
    oldRequest.reject({ stop: true });
    await waitForPump();
  });
  assert.equal(result.byContentId.get('same').retryable, false, 'a late old-text rejection cannot fail the new source version');
  await act(async () => { newRequest.resolve([succeeded(segmentId('same'), '新译文')]); await waitForPump(); });
  assert.equal(result.byContentId.get('same').bodyText, '新译文');
  await act(async () => { tree.unmount(); });

  requests.splice(0);
  tree = await mount({ engineId: 'engine-a', items: [item('recover', 'recoverable transport')], scopeKey: 'all', session: sessionA });
  const firstRecoverableRequest = requests[0];
  await act(async () => { firstRecoverableRequest.reject({ stop: false }); await waitForPump(); });
  const recoveredRequest = requests.find((request) => request !== firstRecoverableRequest);
  assert.ok(recoveredRequest, 'a recoverable transport failure remains pending and retries automatically');
  assert.equal(result.byContentId.get('recover').retryable, false, 'automatic transport recovery does not expose a terminal retry action');
  await act(async () => { recoveredRequest.resolve([succeeded(segmentId('recover'), '恢复译文')]); await waitForPump(); });
  assert.equal(result.byContentId.get('recover').bodyText, '恢复译文');
  await act(async () => { tree.unmount(); });

  requests.splice(0);
  runtimeGeneration = 1;
  runtime = { generation: runtimeGeneration, identity: { server_id: 'x-feed-test' } };
  let resetProps = { engineId: 'engine-a', items: [item('reset', 'reset source')], scopeKey: 'all', session: sessionA };
  tree = await mount(resetProps);
  const scopeRequest = requests.at(-1);
  resetProps = { ...resetProps, scopeKey: 'folder:news' };
  await update(tree, resetProps);
  assert.equal(scopeRequest.signal.aborted, true, 'scope changes abort old work');
  const engineRequest = requests.at(-1);
  resetProps = { ...resetProps, engineId: 'engine-b' };
  await update(tree, resetProps);
  assert.equal(engineRequest.signal.aborted, true, 'engine changes abort old work');
  const accountRequest = requests.at(-1);
  resetProps = { ...resetProps, session: { access_token: 'token-b', user: { id: 'user-b' } } };
  await update(tree, resetProps);
  assert.equal(accountRequest.signal.aborted, true, 'account changes abort old work');
  const runtimeRequest = requests.at(-1);
  runtimeGeneration += 1;
  runtime = { generation: runtimeGeneration, identity: { server_id: 'x-feed-test-2' } };
  await update(tree, resetProps);
  assert.equal(runtimeRequest.signal.aborted, true, 'runtime generation changes abort old work');
  assert.equal(result.byContentId.get('reset').bodyText, undefined, 'identity resets never retain display text');
  await act(async () => { tree.unmount(); });

  console.log('X feed hook keeps failures segment-scoped and rejects stale route and source results');
} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}
