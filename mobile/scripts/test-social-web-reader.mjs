import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import Module, { createRequire } from 'node:module';

const componentPath = fileURLToPath(new URL('../components/TranslatableWebView.tsx', import.meta.url));
const readerHtmlPath = fileURLToPath(new URL('../domain/readerHtml.ts', import.meta.url));
const rankingScreenPath = fileURLToPath(new URL('../screens/RankingPreviewScreen.tsx', import.meta.url));
const screenPath = fileURLToPath(new URL('../screens/ArticleReaderScreen.tsx', import.meta.url));
const component = readFileSync(componentPath, 'utf8');
const rankingScreen = readFileSync(rankingScreenPath, 'utf8');
const screen = readFileSync(screenPath, 'utf8');

assert.match(screen, /const isX = item\.source_kind === 'x'/, 'X must have an explicit presentation rule');
assert.match(
  screen,
  /useState<'reader' \| 'web'>\(isReddit \|\| isX \|\| isWebArticle \? 'web' : 'reader'\)/,
  'X, Reddit, Web and RSS open in web mode',
);
const readerHtmlBundle = buildSync({
  bundle: true,
  entryPoints: [readerHtmlPath],
  packages: 'external',
  format: 'cjs',
  platform: 'node',
  write: false,
}).outputFiles[0].text;
// Resolve shared i18n dependencies from the real workspace, including any
// CommonJS dependencies that cannot require packages from a data URL module.
const readerModule = new Module(readerHtmlPath);
readerModule.filename = readerHtmlPath;
readerModule.require = createRequire(readerHtmlPath);
readerModule._compile(readerHtmlBundle, readerHtmlPath);
const { buildReaderHtml } = readerModule.exports;
const article = {
  author_name: 'Alice & Bob',
  body_html: '<p>Post body</p>',
  external_url: 'https://example.com/post',
  title: 'Heading <unsafe>',
};
const item = {
  external_url: article.external_url,
  fetched_at: '2026-09-04T00:00:00Z',
  source_kind: 'x',
  source_name: 'Example',
};
const xHtml = buildReaderHtml(article, item, false);
assert.doesNotMatch(xHtml, /<h1>/, 'the manual X reader must not repeat post text as a heading');
assert.match(xHtml, /class="content post-content"/, 'X content keeps its post presentation');
const articleHtml = buildReaderHtml(article, { ...item, source_kind: 'rss' }, true);
assert.match(articleHtml, /<h1>Heading &lt;unsafe&gt;<\/h1>/, 'ordinary article headings are rendered and escaped');
assert.match(articleHtml, /Alice &amp; Bob/, 'article metadata is escaped');
assert.doesNotMatch(articleHtml, /reader-action:\/\//, 'save and share live in the shared native header');
assert.match(
  screen,
  /isReddit \|\| isX\s*\? \{ uri: item\.external_url \}/,
  'X and Reddit web rendering must not wait for article data',
);
assert.match(
  screen,
  /\{ uri: article\?\.external_url \?\? item\.external_url \}/,
  'ordinary article web mode must prefer the canonical article URL and survive article API failures',
);
assert.match(
  screen,
  /translationProfile=\{translationProfile\}/,
  'the reader must pass a typed site profile into the WebView runtime',
);
assert.doesNotMatch(
  screen,
  /fallbackToXReader|autoFallbackContentRef|onHttpError=/,
  'X web errors and profile mismatches must never switch modes automatically',
);
assert.match(
  screen,
  /<DetailHeader[\s\S]*onSave=[\s\S]*onShare=/,
  'social webpages must expose native save and share actions',
);
assert.doesNotMatch(
  screen,
  /READER_CONTROLS_RESERVED_HEIGHT|webView:\s*\{[^}]*marginBottom/,
  'article controls must retain the original floating overlay UI',
);
assert.doesNotMatch(
  rankingScreen,
  /PREVIEW_CONTROLS_RESERVED_HEIGHT|webView:\s*\{[^}]*marginBottom/,
  'ranking controls must retain the original floating overlay UI',
);

assert.match(component, /const documentEpochRef = useRef<string \| null>\(null\)/);
assert.match(
  component,
  /envelope\.type === 'reader_translation_navigation_reset'[\s\S]*?documentEpochRef\.current = envelope\.document_epoch[\s\S]*?reset\(\)[\s\S]*?confirmEpoch/,
  'a web epoch reset must atomically invalidate native translation work before acknowledgement',
);
assert.match(
  component,
  /applyTranslations\(\$\{payload\},\$\{epoch\}\)/,
  'translation results must be applied with their current document epoch',
);
assert.match(
  component,
  /acceptedSourceTextRef[\s\S]*?\{ \.\.\.result, source_text: sourceText \}/,
  'native results must carry the source text that produced each translation',
);
assert.match(
  component,
  /documentEpochRef\.current !== envelope\.document_epoch/,
  'messages from obsolete SPA epochs must be rejected',
);

console.log('social webpages use site profiles, epoch isolation, explicit X modes, and floating controls');
