import assert from 'node:assert/strict';
import { buildSync } from 'esbuild';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const mobileRoot = fileURLToPath(new URL('..', import.meta.url));
const bundle = buildSync({
  bundle: true,
  entryPoints: [fileURLToPath(new URL('../domain/xFeedTranslation.ts', import.meta.url))],
  format: 'esm',
  platform: 'node',
  write: false,
}).outputFiles[0].text;
const domain = await import(`data:text/javascript;base64,${Buffer.from(bundle).toString('base64')}`);

const author = { avatar_url: null, handle: 'reader', name: 'Reader' };
const referenceAuthor = { avatar_url: null, handle: 'quoted', name: 'Quoted author' };
const base = {
  content_id: 'content-1',
  excerpt: 'fallback excerpt',
  source_kind: 'x',
  title: 'fallback title',
  x_preview: {
    author,
    completeness: 'parsed',
    external_url: 'https://x.com/reader/status/1',
    is_repost: false,
    quote: {
      author: referenceAuthor,
      availability: 'available',
      external_url: 'https://x.com/quoted/status/2',
      text: 'quoted source',
      tweet_id: '2',
    },
    repost: null,
    text: 'main source',
    tweet_id: '1',
  },
};

const segments = domain.xFeedTranslationSegments(base);
assert.deepEqual(
  segments.map(({ purpose, text }) => ({ purpose, text })),
  [
    { purpose: 'paragraph', text: 'main source' },
    { purpose: 'paragraph', text: 'quoted source' },
  ],
  'X cards translate only the two visible content fragments with the public paragraph purpose',
);
assert.ok(!segments.some(({ text }) => text.includes(author.name) || text.includes(referenceAuthor.name)), 'author metadata is never submitted for translation');

const bodyId = domain.xFeedTranslationSegmentId(base.content_id, 'body');
const referenceId = domain.xFeedTranslationSegmentId(base.content_id, 'reference');
const succeeded = (segmentId, translatedText) => ({
  purpose: 'paragraph',
  segment_id: segmentId,
  translated_text: translatedText,
  translation_status: 'succeeded',
});
let records = new Map([
  [bodyId, { sourceText: 'main source', result: succeeded(bodyId, '主推文译文') }],
  [referenceId, { sourceText: 'quoted source', result: succeeded(referenceId, '引用译文') }],
]);
assert.deepEqual(
  domain.xFeedCardTranslation(base, records, new Set(), false),
  { bodyText: '主推文译文', referenceText: '引用译文', retryable: false },
  'successful body and quote results are projected independently',
);

records = new Map([
  [bodyId, { sourceText: 'obsolete source', result: succeeded(bodyId, '过期译文') }],
  [referenceId, { sourceText: 'quoted source', result: { ...succeeded(referenceId, ''), translated_text: null, translation_status: 'failed' } }],
]);
assert.deepEqual(
  domain.xFeedCardTranslation(base, records, new Set(), false),
  { bodyText: undefined, referenceText: undefined, retryable: true },
  'stale or failed results never replace the current original and remain retryable',
);
assert.equal(
  domain.xFeedCardTranslation(base, new Map(), new Set([bodyId]), false).retryable,
  true,
  'a convergence timeout is explicitly retryable',
);

const repost = {
  ...base,
  x_preview: {
    ...base.x_preview,
    is_repost: true,
    quote: null,
    repost: { ...base.x_preview.quote, text: 'reposted source' },
  },
};
assert.deepEqual(
  domain.xFeedTranslationSegments(repost).map(({ text }) => text),
  ['reposted source'],
  'a repost translates the referenced post without spending quota on its hidden wrapper text',
);
assert.deepEqual(domain.xFeedTranslationSegments({ ...base, source_kind: 'rss' }), [], 'non-X cards are excluded');

const homeSource = readFileSync(fileURLToPath(new URL('../screens/HomeScreen.tsx', import.meta.url)), 'utf8');
const hookSource = readFileSync(fileURLToPath(new URL('../hooks/useXFeedTranslation.ts', import.meta.url)), 'utf8');
assert.match(homeSource, /translationEnabled=\{translationPreference\.enabled\}/, 'the authenticated translation preference gates X translation');
assert.match(homeSource, /xTranslation=\{feed\.xTranslation\.byContentId\.get\(item\.content_id\)\}/, 'each X card receives only its own translation');
assert.match(hookSource, /\[enabled, engineId, runtimeGeneration, scopeKey, session\.user\.id, targetLocale\]/, 'preference, identity, connection, and scope changes discard old display results');

console.log('X feed translation covers body, quote, repost, fallback, retry, and identity boundaries');
