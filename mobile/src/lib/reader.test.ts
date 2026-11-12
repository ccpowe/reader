import assert from 'node:assert/strict';
import { test } from 'node:test';

import { articleDate, hasFullBody, hasReadableBody, readerDocument, readerKind, safeHttpUrl, youtubeEmbedUrl, youtubeVideoId } from './reader.ts';

test('YouTube video ids from the usual URL shapes', () => {
  assert.equal(youtubeVideoId('https://www.youtube.com/watch?v=abc123_-X'), 'abc123_-X');
  assert.equal(youtubeVideoId('https://youtu.be/xyz'), 'xyz');
  assert.equal(youtubeVideoId('https://www.youtube.com/shorts/sss'), 'sss');
  assert.equal(youtubeVideoId('https://www.youtube.com/channel/UC1'), null);
  assert.equal(youtubeVideoId('not a url'), null);
  assert.match(youtubeEmbedUrl('a<b>"c'), /\/embed\/abc\?/);
});

test('which view an article opens in', () => {
  assert.deepEqual(readerKind({ url: 'https://youtu.be/v1', source: { type: 'youtube' } }), { kind: 'youtube', videoId: 'v1' });
  assert.deepEqual(readerKind({ url: 'https://www.youtube.com/@chan', source: { type: 'youtube' } }), { kind: 'web' });
  assert.deepEqual(readerKind({ url: 'https://x.com/a/status/1', source: { type: 'x' } }), { kind: 'social' });
  assert.deepEqual(readerKind({ url: 'https://reddit.com/r/a', source: { type: 'reddit' } }), { kind: 'social' });
  assert.deepEqual(readerKind({ url: 'https://e.com/post', source: { type: 'rss' } }), { kind: 'web' });
  assert.deepEqual(readerKind({ url: 'https://e.com/post', source: null }), { kind: 'web' });
});

test('readable body: text or media, not empty markup', () => {
  assert.equal(hasReadableBody('<p>Hello</p>'), true);
  assert.equal(hasReadableBody('<p><img src="a.png"></p>'), true);
  assert.equal(hasReadableBody('<p>&nbsp;</p><!-- x --><div> </div>'), false);
  assert.equal(hasReadableBody(null), false);
});

test('reader document: escaped shell, scripts forbidden, body kept', () => {
  const html = readerDocument({
    url: 'https://e.com/a?x=1&y="2"',
    title: 'A <b>title</b>',
    author: null,
    sourceName: 'Simon',
    publishedAt: '2026-11-12T10:00:00Z',
    imageUrl: 'https://e.com/hero.jpg',
    bodyHtml: '<p>Body <em>text</em></p>',
  });
  assert.match(html, /<h1>A &lt;b&gt;title&lt;\/b&gt;<\/h1>/);
  assert.match(html, /<base href="https:\/\/e\.com\/a\?x=1&amp;y=&quot;2&quot;">/);
  assert.match(html, /script-src 'none'/);
  assert.match(html, /Simon · 2026 年 11 月 12 日/);
  assert.match(html, /<figure><img alt="" src="https:\/\/e\.com\/hero\.jpg"><\/figure>/);
  assert.match(html, /data-reader-translatable-root><p>Body <em>text<\/em><\/p><\/section>/);
  // the hero is not repeated when the body already shows it
  assert.doesNotMatch(readerDocument({ url: 'https://e.com', title: 't', imageUrl: 'https://e.com/i.png', bodyHtml: '<img src="https://e.com/i.png">' }), /<figure>/);
  assert.equal(articleDate('nope'), '');
});

test('a feed summary is not a full body', () => {
  assert.equal(hasFullBody('<p>卫星导航基准站网安全国标发布。<a href="x">查看全文</a></p>'), false);
  assert.equal(hasFullBody(`<p>${'word '.repeat(100)}</p>`), true);
  assert.equal(hasFullBody(null), false);
});

test('items without a source are judged by kind and host', () => {
  assert.deepEqual(readerKind({ url: 'https://x.com/a/status/1', kind: 'post', source: null }), { kind: 'social' });
  assert.deepEqual(readerKind({ url: 'https://example.com/thread', kind: 'post', source: null }), { kind: 'social' });
  assert.deepEqual(readerKind({ url: 'https://www.youtube.com/watch?v=v9', kind: 'article', source: null }), { kind: 'youtube', videoId: 'v9' });
  assert.deepEqual(readerKind({ url: 'https://old.reddit.com/r/x', kind: 'article', source: null }), { kind: 'social' });
});

test('only http(s) addresses pass', () => {
  assert.equal(safeHttpUrl('https://e.com/a b'), 'https://e.com/a%20b');
  assert.equal(safeHttpUrl('http://e.com'), 'http://e.com/');
  for (const bad of ['javascript:alert(1)', 'file:///etc/passwd', 'intent://x#Intent;end', 'weixin://dl', 'data:text/html,x', '', null, 'nope']) {
    assert.equal(safeHttpUrl(bad), null, String(bad));
  }
});
