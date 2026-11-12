import assert from 'node:assert/strict';
import { test } from 'node:test';

import { backStaysInside, documentKey, initialReaderPage, readerPageReducer as r } from './reader-page.ts';

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

test('switching views starts a fresh document; the same view is not a switch', () => {
  const loaded = r(r(start, { type: 'load-end' }), { type: 'history', canGoBack: true });
  const reader = r(loaded, { type: 'switch', view: 'reader' });
  assert.deepEqual(reader, {
    view: 'reader',
    articleUrl: 'https://e.com/a',
    pageUrl: 'https://e.com/a',
    load: { status: 'loading', progress: 0 },
    canGoBack: false,
    origin: 'page',
    extracted: null,
  });
  assert.equal(r(reader, { type: 'switch', view: 'reader' }), reader);
  assert.notEqual(documentKey(reader), documentKey(loaded));
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
  assert.equal(documentKey(reader), 'reader:https://e.com/a');
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
