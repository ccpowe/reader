import assert from 'node:assert/strict';
import { buildSync } from 'esbuild';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const cardPath = fileURLToPath(new URL('../components/FeedCards.tsx', import.meta.url));
const source = readFileSync(cardPath, 'utf8');
const postStart = source.indexOf('  if (isPost) {');
const articleStart = source.indexOf('\n  return (', postStart);
const savedCardStart = source.indexOf('\nexport function SavedCard', articleStart);
const postVariant = source.slice(postStart, articleStart);
const articleVariant = source.slice(articleStart, savedCardStart);

assert.match(
  articleVariant,
  /<View style=\{styles\.articleBody\}>[\s\S]*?<View style=\{styles\.articleCopy\}>[\s\S]*?<\/View>\s*\{item\.thumbnail_url \? <ContentImage[\s\S]*?<\/View>\s*<View style=\{styles\.cardFooter\}>/,
  'ordinary article content and thumbnail must share a body row above the card-wide footer',
);
assert.match(
  source,
  /articleBody:\s*\{[^}]*flexDirection:\s*'row'/,
  'ordinary article body must keep copy and thumbnail in a horizontal row',
);
assert.doesNotMatch(
  source,
  /articleCard:\s*\{[^}]*flexDirection:\s*'row'/,
  'ordinary article card must remain vertical so its footer spans the full card',
);
assert.match(
  postVariant,
  /<View style=\{styles\.postHeader\}>[\s\S]*?<SaveButton isSaved=\{item\.is_saved\} onPress=\{onToggleSave\} \/>/,
  'the X card bookmark placement must remain unchanged',
);

console.log('feed card layout keeps ordinary bookmarks at the card edge and leaves X unchanged');

const snippetBundle = buildSync({ bundle: true, entryPoints: [fileURLToPath(new URL('../domain/xCardPreview.ts', import.meta.url))], format: 'esm', platform: 'node', write: false }).outputFiles[0].text;
const { xCardSnippet } = await import(`data:text/javascript;base64,${Buffer.from(snippetBundle).toString('base64')}`);
assert.equal(xCardSnippet('  hello \n world  ', 20), 'hello world');
assert.equal(xCardSnippet('👨‍👩‍👧‍👦'.repeat(181), 180), '👨‍👩‍👧‍👦'.repeat(180) + '…', 'X previews must not split combined emoji');
assert.equal(xCardSnippet('e\u0301'.repeat(81), 80), 'e\u0301'.repeat(80) + '…', 'quote previews must not split combining characters');
assert.equal(xCardSnippet('短帖', 180), '短帖');
console.log('X preview limits preserve complete graphemes and short posts');
