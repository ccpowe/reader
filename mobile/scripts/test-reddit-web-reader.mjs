import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const screenPath = fileURLToPath(new URL('../screens/ArticleReaderScreen.tsx', import.meta.url));
const source = readFileSync(screenPath, 'utf8');

assert.match(source, /const isReddit = item\.source_kind === 'reddit'/, 'Reddit must have an explicit presentation rule');
assert.match(source, /useState\(!isVideo && !isReddit && !isWebArticle\)/, 'Reddit must not wait for article loading');
assert.match(source, /if \(isVideo \|\| isReddit \|\| isWebArticle\) \{\s*setLoading\(false\);\s*return;/, 'Reddit must skip the article request');
assert.match(source, /const effectiveMode = isReddit \? 'web' : mode/, 'Reddit must be locked to web mode');
assert.match(source, /\? isReddit \|\| isX\s*\? \{ uri: item\.external_url \}/, 'Reddit and X must load their feed URL directly');
assert.match(source, /contentSource \? \(\s*<TranslatableWebView/, 'Reddit web rendering must not be gated on article data');
assert.match(source, /onSwitchMode=\{isReddit \? undefined/, 'Reddit must hide the unavailable reader toggle');
assert.match(source, /<DetailHeader[\s\S]*saved=\{saved\}[\s\S]*onSave=/, 'Reddit retains the shared native save action');
assert.match(source, /onShare=\{\(\) => void share\(\)\}/, 'Reddit retains the shared native share action');
assert.match(source, /translationPurpose=\{effectiveMode === 'web' \? 'web_segment' : 'paragraph'\}/, 'Reddit must use webpage translation');
assert.match(source, /translationProfile=\{translationProfile\}/, 'Reddit must use its fail-closed webpage profile');

console.log('Reddit reader opens the webpage directly and preserves native content actions');
