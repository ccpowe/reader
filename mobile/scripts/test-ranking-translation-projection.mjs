import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const outputDirectory = mkdtempSync(join(tmpdir(), 'reader-ranking-translation-projection-'));
const require = createRequire(import.meta.url);

const baseItem = {
  author: null,
  comments: null,
  description: 'Original description',
  description_translation_status: null,
  forks: null,
  image_urls: [],
  language: null,
  rank: 1,
  score: null,
  source_label: null,
  stars: null,
  stars_this_period: null,
  title: 'Original title',
  title_translation_status: null,
  translated_description: null,
  translated_title: null,
  translation_key: 'ranking:test:one',
  translation_locale: null,
  url: 'https://example.test/item',
};

const ranking = (item, effectiveEngineFingerprint = 'fingerprint-a') => ({
  effective_engine_fingerprint: effectiveEngineFingerprint,
  fetched_at: '2026-09-21T00:00:00Z',
  items: [item],
  kind: 'hacker_news',
  subtitle: 'Top stories',
  title: 'Hacker News',
});

try {
  execFileSync(
    process.execPath,
    [
      join(mobileRoot, 'node_modules', 'typescript', 'bin', 'tsc'),
      '--ignoreConfig',
      'domain/rankingTranslationProjection.ts',
      '--target',
      'es2022',
      '--module',
      'commonjs',
      '--outDir',
      outputDirectory,
      '--skipLibCheck',
    ],
    { cwd: mobileRoot, stdio: 'inherit' },
  );
  const {
    mergeRankingTranslationProjection,
    missingRankingTranslationSegments,
    translationResultMatchesRankingRoute,
  } = require(join(outputDirectory, 'domain/rankingTranslationProjection.js'));

  const segmentResult = {
    effective_engine_fingerprint: 'fingerprint-a',
    segment_id: 'ranking:test:one:title',
    translated_text: '译文',
    translation_locale: 'zh-CN',
    translation_status: 'succeeded',
  };
  assert.equal(
    translationResultMatchesRankingRoute(segmentResult, 'zh-CN', true, 'fingerprint-a'),
    true,
  );
  assert.equal(
    translationResultMatchesRankingRoute(
      { ...segmentResult, translation_locale: 'ja-JP' },
      'zh-CN',
      true,
      'fingerprint-a',
    ),
    false,
  );
  assert.equal(
    translationResultMatchesRankingRoute(
      { ...segmentResult, effective_engine_fingerprint: 'fingerprint-b' },
      'zh-CN',
      true,
      'fingerprint-a',
    ),
    false,
    'same engine ids cannot substitute for an exact fingerprint match',
  );
  assert.equal(
    translationResultMatchesRankingRoute(
      { ...segmentResult, effective_engine_fingerprint: undefined },
      'zh-CN',
      true,
      'fingerprint-a',
    ),
    false,
  );

  const serverHit = ranking({
    ...baseItem,
    translated_title: '服务器标题',
    title_translation_status: 'succeeded',
    translation_locale: 'zh-CN',
  });
  assert.deepEqual(
    missingRankingTranslationSegments(serverHit, 20, 'zh-CN', true, 'fingerprint-a')
      .map((item) => item.segment_id),
    ['ranking:test:one:description'],
    'server hits must not be requested again while missing fields remain eligible',
  );

  const previous = ranking({
    ...baseItem,
    translated_title: '客户端标题',
    title_translation_status: 'succeeded',
    translated_description: '客户端描述',
    description_translation_status: 'succeeded',
    translation_locale: 'zh-CN',
  });
  const refreshed = mergeRankingTranslationProjection(
    previous,
    ranking({ ...baseItem }),
    'zh-CN',
    true,
    'fingerprint-a',
  );
  assert.equal(refreshed.items[0].translated_title, '客户端标题');
  assert.equal(refreshed.items[0].translated_description, '客户端描述');
  assert.deepEqual(
    missingRankingTranslationSegments(refreshed, 20, 'zh-CN', true, 'fingerprint-a'),
    [],
    'a raw refresh must retain valid successful translations',
  );

  const changedSource = mergeRankingTranslationProjection(
    previous,
    ranking({ ...baseItem, title: 'Changed title' }),
    'zh-CN',
    true,
    'fingerprint-a',
  );
  assert.equal(changedSource.items[0].translated_title, null);
  assert.equal(changedSource.items[0].translated_description, '客户端描述');
  assert.deepEqual(
    missingRankingTranslationSegments(changedSource, 20, 'zh-CN', true, 'fingerprint-a')
      .map((item) => item.segment_id),
    ['ranking:test:one:title'],
    'changed source text must invalidate only its matching translation',
  );

  const otherLocale = mergeRankingTranslationProjection(
    { ...previous, items: [{ ...previous.items[0], translation_locale: 'ja-JP' }] },
    ranking({ ...baseItem }),
    'zh-CN',
    true,
    'fingerprint-a',
  );
  assert.equal(otherLocale.items[0].translated_title, null);
  assert.equal(otherLocale.items[0].translated_description, null);

  const staleServerLocale = mergeRankingTranslationProjection(
    previous,
    ranking({
      ...baseItem,
      translated_title: '古い翻訳',
      title_translation_status: 'succeeded',
      translation_locale: 'ja-JP',
    }),
    'zh-CN',
    true,
    'fingerprint-a',
  );
  assert.equal(staleServerLocale.items[0].translated_title, null);
  assert.equal(staleServerLocale.items[0].translated_description, null);
  assert.deepEqual(
    missingRankingTranslationSegments(staleServerLocale, 20, 'zh-CN', true, 'fingerprint-a')
      .map((item) => item.segment_id),
    ['ranking:test:one:title', 'ranking:test:one:description'],
  );

  const changedEngineFingerprint = mergeRankingTranslationProjection(
    previous,
    ranking({
      ...baseItem,
      translated_title: '另一引擎译文',
      title_translation_status: 'succeeded',
      translation_locale: 'zh-CN',
    }, 'fingerprint-b'),
    'zh-CN',
    true,
    'fingerprint-a',
  );
  assert.equal(changedEngineFingerprint.items[0].translated_title, null);
  assert.equal(changedEngineFingerprint.items[0].translated_description, null);
  assert.deepEqual(
    missingRankingTranslationSegments(
      changedEngineFingerprint,
      20,
      'zh-CN',
      true,
      'fingerprint-a',
    ).map((item) => item.segment_id),
    ['ranking:test:one:title', 'ranking:test:one:description'],
    'the same engine id with a different fingerprint must not reuse either response',
  );

  const disabled = mergeRankingTranslationProjection(
    previous,
    ranking({ ...baseItem }),
    'zh-CN',
    false,
    'fingerprint-a',
  );
  assert.equal(disabled.items[0].translated_title, null);
} finally {
  rmSync(outputDirectory, { force: true, recursive: true });
}

console.log('Ranking translation projection behavior passed.');
