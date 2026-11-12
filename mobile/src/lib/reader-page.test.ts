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
  assert.deepEqual(reader, { view: 'reader', articleUrl: 'https://e.com/a', pageUrl: 'https://e.com/a', load: { status: 'loading', progress: 0 }, canGoBack: false });
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
  assert.equal(backStaysInside(link), 'reader');
  assert.equal(backStaysInside(r(link, { type: 'history', canGoBack: true })), 'history');
  const back = r(link, { type: 'back' });
  assert.equal(back.view, 'reader');
  assert.equal(back.pageUrl, 'https://e.com/a');
  assert.equal(r(link, { type: 'switch', view: 'reader' }).pageUrl, 'https://e.com/a');
  assert.equal(backStaysInside(start), null);
});
