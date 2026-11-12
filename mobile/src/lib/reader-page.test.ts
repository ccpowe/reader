import assert from 'node:assert/strict';
import { test } from 'node:test';

import { backStaysInside, documentKey, initialReaderPage, readerKey, readerPageReducer as r, shareUrl } from './reader-page.ts';

const start = initialReaderPage('page', 'https://e.com/a');

test('a later successful load clears an earlier failure', () => {
  let s = r(start, { type: 'failed', message: '网站出错了（500）。' });
  s = r(s, { type: 'load-end' });
  assert.equal(s.load.status, 'failed'); // the failing load's own end keeps the failure
  s = r(s, { type: 'load-start' });
  assert.deepEqual(s.load, { status: 'loading', progress: 0 });
  s = r(s, { type: 'progress', progress: 0.6 });
  s = r(s, { type: 'load-end' });
  assert.deepEqual(s.load, { status: 'loaded' });
  assert.equal(r(s, { type: 'progress', progress: 0.9 }), s); // late tick ignored
});

test('reader mode lies over the page, which keeps its load, history and place (old app)', () => {
  const loaded = r(r(start, { type: 'load-end' }), { type: 'history', canGoBack: true, url: 'https://e.com/a#part' });
  const reader = r(loaded, { type: 'switch', view: 'reader' });
  assert.equal(reader.view, 'reader');
  assert.deepEqual(reader.load, { status: 'loaded' });
  assert.equal(reader.canGoBack, true);
  assert.equal(documentKey(reader), documentKey(loaded), 'the page WebView is not remounted');
  assert.equal(r(reader, { type: 'switch', view: 'reader' }), reader);
  const back = r(reader, { type: 'switch', view: 'page' });
  assert.deepEqual(back, { ...loaded, view: 'page' });
});

test('to and from the YouTube player is a fresh document', () => {
  const player = initialReaderPage('player', 'https://youtube.com/watch?v=x');
  const page = r(r(player, { type: 'switch', view: 'page' }), { type: 'load-end' });
  const again = r(page, { type: 'switch', view: 'player' });
  assert.deepEqual(again.load, { status: 'loading', progress: 0 });
  assert.notEqual(documentKey(again), documentKey(page));
});

test('share uses the page as it is now, or the article reader mode shows', () => {
  const followed = r(start, { type: 'history', canGoBack: true, url: 'https://e.com/next' });
  assert.equal(shareUrl(followed), 'https://e.com/next');
  assert.equal(shareUrl(r(followed, { type: 'switch', view: 'reader' })), 'https://e.com/a');
  const article = { url: 'https://e.com/next', title: 'T', byline: null, lang: 'en', html: '<p>x</p>', text: 'x' };
  assert.equal(shareUrl(r(followed, { type: 'show-extracted', article })), 'https://e.com/next');
  const link = r(r(start, { type: 'switch', view: 'reader' }), { type: 'open-page', url: 'https://other.example/x' });
  assert.equal(shareUrl(link), 'https://other.example/x');
});

test('a link from reader mode opens as the original page at that address', () => {
  const reader = r(start, { type: 'switch', view: 'reader' });
  const page = r(reader, { type: 'open-page', url: 'https://other.example/x' });
  assert.equal(page.view, 'page');
  assert.equal(page.pageUrl, 'https://other.example/x');
  assert.equal(documentKey(page), 'page:https://other.example/x');
});

test('retry reloads', () => {
  const failed = r(start, { type: 'failed', message: 'x' });
  assert.deepEqual(r(failed, { type: 'retry' }).load, { status: 'loading', progress: 0 });
});

test('back from a link returns to reader mode; the page tool shows the article itself', () => {
  const link = r(r(start, { type: 'switch', view: 'reader' }), { type: 'open-page', url: 'https://other.example/x' });
  assert.equal(backStaysInside(link), 'origin');
  assert.equal(backStaysInside(r(link, { type: 'history', canGoBack: true })), 'history');
  const back = r(link, { type: 'back' });
  assert.equal(back.view, 'reader');
  assert.equal(back.pageUrl, 'https://e.com/a');
  assert.equal(r(link, { type: 'switch', view: 'reader' }).pageUrl, 'https://e.com/a');
  assert.equal(backStaysInside(start), null);
});

test('reader mode from the page keeps its article across a link and back', () => {
  const article = { url: 'https://e.com/a', title: 'T', byline: null, lang: 'en', html: '<p>x</p>', text: 'x' };
  const reader = r(start, { type: 'show-extracted', article });
  assert.equal(reader.view, 'reader');
  assert.equal(readerKey(reader), 'reader:https://e.com/a');
  const link = r(reader, { type: 'open-page', url: 'https://e.com/b' });
  const back = r(link, { type: 'back' });
  assert.equal(back.view, 'reader');
  assert.equal(back.extracted, article);
  // the page view and back to reader mode reuse it
  assert.equal(r(r(reader, { type: 'switch', view: 'page' }), { type: 'switch', view: 'reader' }).extracted, article);
});

test('the HN discussion opens from the page and returns to it', () => {
  const discussion = r(start, { type: 'open-page', url: 'https://news.ycombinator.com/item?id=1' });
  assert.equal(backStaysInside(discussion), 'origin');
  const back = r(discussion, { type: 'back' });
  assert.equal(back.view, 'page');
  assert.equal(back.pageUrl, 'https://e.com/a');
  // from reader mode: discussion, a link in it, then back → reader mode
  const viaReader = r(r(r(start, { type: 'switch', view: 'reader' }), { type: 'open-page', url: 'https://hn/1' }), {
    type: 'open-page',
    url: 'https://x/2',
  });
  assert.equal(r(viaReader, { type: 'back' }).view, 'reader');
});

test('reader mode taken from a linked page goes back to that page as it was', () => {
  const link = r(r(start, { type: 'switch', view: 'reader' }), { type: 'open-page', url: 'https://e.com/b' });
  const loaded = r(r(link, { type: 'load-end' }), { type: 'history', canGoBack: true, url: 'https://e.com/b#2' });
  const article = { url: 'https://e.com/b#2', title: 'B', byline: null, lang: 'en', html: '<p>b</p>', text: 'b' };
  const reader = r(loaded, { type: 'show-extracted', article });
  const back = r(reader, { type: 'switch', view: 'page' });
  assert.equal(back.pageUrl, 'https://e.com/b');
  assert.equal(back.currentUrl, 'https://e.com/b#2');
  assert.deepEqual(back.load, { status: 'loaded' });
  assert.equal(back.canGoBack, true);
  assert.equal(documentKey(back), documentKey(loaded));
});
