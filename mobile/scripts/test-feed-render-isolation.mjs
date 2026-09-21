import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import Module from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-feed-render-isolation-'));
const require = createRequire(import.meta.url);
process.env.NODE_PATH = join(mobileRoot, 'node_modules');
Module._initPaths();

try {
  execFileSync(process.execPath, [
    join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
    '--ignoreConfig',
    'components/FeedCards.tsx',
    'components/FeedCardRow.tsx',
    'hooks/useXFeedTranslation.ts',
    '--target', 'es2022', '--module', 'commonjs', '--jsx', 'react-jsx',
    '--outDir', outputDirectory, '--lib', 'dom,es2022', '--types', 'node', '--skipLibCheck',
  ], { cwd: mobileRoot, stdio: 'inherit' });

  const React = require('react');
  const { act, create } = require('react-test-renderer');
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  const requests = [];
  let runtimeGeneration = 1;
  let runtime = { generation: runtimeGeneration, identity: { server_id: 'feed-render-test' } };
  let locale = 'zh';
  const localeListeners = new Set();
  const renderCounts = new Map();
  const renderedCardProps = new Map();
  let probeFeedCards = false;
  const originalLoad = Module._load;

  function CardProbe(props) {
    const id = props.item.content_id;
    renderCounts.set(id, (renderCounts.get(id) ?? 0) + 1);
    renderedCardProps.set(id, props);
    return React.createElement('ArticleCardProbe', { contentId: id });
  }

  Module._load = function load(request, parent, isMain) {
    if (probeFeedCards && request === './FeedCards' && parent?.filename.endsWith('/components/FeedCardRow.js')) {
      return { ArticleCard: CardProbe };
    }
    if (request === 'react-native') return {
      Image: 'Image',
      Pressable: 'Pressable',
      StyleSheet: { create: (styles) => styles },
      Text: 'Text',
      View: 'View',
    };
    if (request === '@expo/vector-icons') return {
      Feather: 'Feather',
      MaterialCommunityIcons: 'MaterialCommunityIcons',
    };
    if (request === '../hooks/useAvatarImageSource') return {
      useAvatarImageSource: (url, token) => url ? { uri: `${url}?access=${token}` } : null,
    };
    if (request === '../lib/api') return {
      displayTitle: (item) => item.translated_title || item.title,
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
      i18n: { t: (key) => `${locale}:${key}` },
      useTranslation: () => {
        React.useSyncExternalStore(
          (listener) => { localeListeners.add(listener); return () => localeListeners.delete(listener); },
          () => locale,
        );
        return { t: (key) => `${locale}:${key}` };
      },
    };
    return originalLoad.call(this, request, parent, isMain);
  };

  const item = (contentId, sourceKind = 'x') => ({
    author_name: `${contentId} author`,
    content_id: contentId,
    excerpt: `${contentId} source`,
    fetched_at: '2026-01-01T00:00:00Z',
    is_saved: false,
    published_at: '2026-01-01T00:00:00Z',
    source_avatar_url: `https://avatars.example/${contentId}.jpg`,
    source_kind: sourceKind,
    source_name: `${contentId} source`,
    thumbnail_url: null,
    title: `${contentId} title`,
    translation_status: null,
    x_preview: sourceKind === 'x' ? {
      author: { avatar_url: `https://avatars.example/${contentId}.jpg`, handle: contentId, name: `${contentId} author` },
      completeness: 'parsed',
      external_url: `https://x.com/${contentId}`,
      is_repost: false,
      quote: null,
      repost: null,
      text: `${contentId} source`,
      tweet_id: contentId,
    } : null,
  });
  const result = (contentId, status, translatedText = null) => ({
    cache_expires_at: null,
    effective_engine_fingerprint: 'test-engine',
    engine_id: 'engine-a',
    engine_label: 'Test',
    error_code: status === 'failed' ? 'provider_error' : null,
    error_retryable: status === 'failed',
    purpose: 'paragraph',
    retry_after_ms: null,
    segment_id: `x-feed:${contentId}:body`,
    translated_text: translatedText,
    translation_locale: 'zh-CN',
    translation_status: status,
  });
  const textContent = (tree) => tree.root.findAllByType('Text')
    .flatMap((node) => node.children)
    .filter((child) => typeof child === 'string')
    .join(' ');
  const waitForPump = () => new Promise((resolve) => setTimeout(resolve, 20));

  // Exercise the real memoized ArticleCard before replacing it with the row
  // render probe used for exact per-item counts below.
  const { ArticleCard } = require(join(outputDirectory, 'components/FeedCards.js'));
  const realXItem = item('real');
  let retryCalls = 0;
  let realCard;
  await act(async () => {
    realCard = create(React.createElement(ArticleCard, {
      avatarAccessToken: 'token-a',
      item: realXItem,
      onPress: () => undefined,
      onRetryXTranslation: () => { retryCalls += 1; },
      onToggleSave: () => undefined,
      xTranslation: undefined,
    }));
  });
  assert.match(textContent(realCard), /real source/, 'the real X card initially renders source text');
  await act(async () => {
    realCard.update(React.createElement(ArticleCard, {
      avatarAccessToken: 'token-a',
      item: { ...realXItem, is_saved: true },
      onPress: () => undefined,
      onRetryXTranslation: () => { retryCalls += 1; },
      onToggleSave: () => undefined,
      xTranslation: { bodyText: '真实译文', retryable: true },
    }));
  });
  assert.match(textContent(realCard), /真实译文/, 'the real card updates translated text');
  assert.match(textContent(realCard), /zh:translationFailed/, 'the real card exposes translation failure state');
  assert.equal(realCard.root.findByProps({ accessibilityLabel: 'zh:unsave' }).props.accessibilityState.selected, true, 'the real card updates saved state');
  realCard.root.findByProps({ accessibilityLabel: 'zh:retryTranslation' }).props.onPress({ stopPropagation() {} });
  assert.equal(retryCalls, 1, 'the real card keeps retry behavior');
  await act(async () => { locale = 'en'; localeListeners.forEach((listener) => listener()); });
  assert.match(textContent(realCard), /en:translationFailed/, 'locale subscriptions update a memoized real card');
  await act(async () => { realCard.unmount(); });

  probeFeedCards = true;
  const rowModulePath = join(outputDirectory, 'components/FeedCardRow.js');
  delete require.cache[require.resolve(rowModulePath)];
  const { FeedCardRow } = require(rowModulePath);
  const { useXFeedTranslation } = require(join(outputDirectory, 'hooks/useXFeedTranslation.js'));
  const sessionA = { access_token: 'token-a', user: { id: 'user-a' } };
  const baseItems = [item('a'), item('b'), item('ordinary', 'rss')];
  let opened = [];
  const openA = (nextItem) => { opened.push(`old:${nextItem.content_id}`); };
  const openB = (nextItem) => { opened.push(`new:${nextItem.content_id}`); };
  const toggleSave = () => undefined;
  let props = {
    enabled: true,
    items: baseItems,
    onOpenArticle: openA,
    session: sessionA,
    targetLocale: 'zh-CN',
  };
  let hookResult;
  function Harness() {
    hookResult = useXFeedTranslation(
      props.session,
      props.items,
      true,
      props.enabled,
      props.targetLocale,
      'engine-a',
      'all',
    );
    return React.createElement(React.Fragment, null, props.items.map((nextItem) => React.createElement(FeedCardRow, {
      avatarAccessToken: props.session.access_token,
      item: nextItem,
      key: nextItem.content_id,
      onOpenArticle: props.onOpenArticle,
      onRetryXTranslation: hookResult.retry,
      onToggleSave: toggleSave,
      xTranslation: hookResult.byContentId.get(nextItem.content_id),
    })));
  }
  let tree;
  await act(async () => { tree = create(React.createElement(Harness)); });
  await act(waitForPump);
  assert.deepEqual(Object.fromEntries(renderCounts), { a: 1, b: 1, ordinary: 1 }, 'all rows render once on mount');

  await act(async () => { requests[0].resolve([result('a', 'pending'), result('b', 'pending')]); await waitForPump(); });
  assert.deepEqual(Object.fromEntries(renderCounts), { a: 1, b: 1, ordinary: 1 }, 'non-visible pending results do not rerender rows');
  await act(async () => { requests[1].resolve([result('a', 'pending'), result('b', 'pending')]); await waitForPump(); });
  assert.deepEqual(Object.fromEntries(renderCounts), { a: 1, b: 1, ordinary: 1 }, 'the same result returned again causes zero row renders');
  await act(async () => { requests[2].resolve([result('a', 'succeeded', 'A译文'), result('b', 'pending')]); await waitForPump(); });
  assert.deepEqual(Object.fromEntries(renderCounts), { a: 2, b: 1, ordinary: 1 }, 'updating A translation rerenders A once and B/ordinary zero times');
  await act(async () => { requests[3].resolve([result('b', 'failed')]); await waitForPump(); });
  assert.deepEqual(Object.fromEntries(renderCounts), { a: 2, b: 2, ordinary: 1 }, 'B failure state rerenders only B');

  props = { ...props, items: [{ ...props.items[0], is_saved: true }, props.items[1], props.items[2]] };
  await act(async () => { tree.update(React.createElement(Harness)); });
  assert.deepEqual(Object.fromEntries(renderCounts), { a: 3, b: 2, ordinary: 1 }, 'saving A rerenders A once and leaves other rows unchanged');

  await act(async () => { renderedCardProps.get('b').onRetryXTranslation(); await waitForPump(); });
  assert.equal(renderCounts.get('b'), 3, 'retry clears B failure UI and rerenders B');
  const retryRequest = requests.at(-1);
  await act(async () => { retryRequest.resolve([result('b', 'succeeded', 'B译文')]); await waitForPump(); });
  assert.equal(renderCounts.get('b'), 4, 'retry success updates B translation');

  props = { ...props, enabled: false };
  await act(async () => { tree.update(React.createElement(Harness)); });
  assert.deepEqual(Object.fromEntries(renderCounts), { a: 4, b: 5, ordinary: 1 }, 'disabling translation clears both translated X rows but leaves ordinary rows unchanged');

  props = { ...props, onOpenArticle: openB };
  await act(async () => { tree.update(React.createElement(Harness)); });
  assert.deepEqual(Object.fromEntries(renderCounts), { a: 5, b: 6, ordinary: 2 }, 'a changed callback reaches every memoized row');
  renderedCardProps.get('a').onPress();
  assert.deepEqual(opened, ['new:a'], 'memoized callbacks never retain the old open handler');

  props = { ...props, enabled: true, targetLocale: 'ja-JP' };
  await act(async () => { tree.update(React.createElement(Harness)); await waitForPump(); });
  assert.deepEqual(Object.fromEntries(renderCounts), { a: 6, b: 7, ordinary: 2 }, 'locale and translation preference changes reach both X rows');

  props = { ...props, session: { access_token: 'token-b', user: { id: 'user-b' } } };
  await act(async () => { tree.update(React.createElement(Harness)); await waitForPump(); });
  assert.deepEqual(Object.fromEntries(renderCounts), { a: 7, b: 8, ordinary: 3 }, 'account and avatar identity changes reach every memoized row');
  assert.equal(renderedCardProps.get('a').avatarAccessToken, 'token-b', 'memoized rows never retain the old account token');
  await act(async () => { tree.unmount(); });

  console.log('feed rows isolate translation renders while preserving card, retry, save, locale, and identity updates');
} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}
