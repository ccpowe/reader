import assert from 'node:assert/strict';
import { test } from 'node:test';

import type { FeedItem } from './api/schema.gen.ts';
import { absoluteUrl, folderLabel, folderList, homeNewCount, patchItems, relativeTime, scopeParams, toCard } from './feed.ts';

const NOW = Date.parse('2026-11-12T12:00:00Z');

test('folders: zh-CN collation (Chinese before Latin, as the old app), 未分类 last', () => {
  assert.deepEqual(folderList([{ group: '新闻' }, { group: null }, { group: 'AI' }, { group: '编程' }, { group: 'AI' }]), ['编程', '新闻', 'AI', '']);
  assert.equal(folderLabel(''), '未分类');
  assert.deepEqual(folderList([{ group: 'AI' }]), ['AI']);
});

test('scope parameters follow the contract', () => {
  assert.deepEqual(scopeParams({ kind: 'home' }), { scope: 'home' });
  assert.deepEqual(scopeParams({ kind: 'group', group: '' }), { group: '' });
  assert.deepEqual(scopeParams({ kind: 'subscription', id: 's1' }), { subscription: 's1' });
});

test('home badge counts only subscriptions shown on 全部', () => {
  assert.equal(
    homeNewCount([
      { include_in_home: true, is_enabled: true, new_count: 3 },
      { include_in_home: false, is_enabled: true, new_count: 50 },
      { include_in_home: true, is_enabled: false, new_count: 7 },
      { include_in_home: true, is_enabled: true, new_count: 10 },
    ]),
    13,
  );
});

test('relative time, past and future', () => {
  assert.equal(relativeTime('2026-11-12T11:59:40Z', NOW), '刚刚');
  assert.equal(relativeTime('2026-11-12T11:48:00Z', NOW), '12 分钟前');
  assert.equal(relativeTime('2026-11-12T01:00:00Z', NOW), '11 小时前');
  assert.equal(relativeTime('2026-11-12T23:00:00Z', NOW), '11 小时后');
  assert.equal(relativeTime('2026-11-09T12:00:00Z', NOW), '3 天前');
  assert.equal(relativeTime('nonsense', NOW), '');
});

const base: FeedItem = {
  id: 'a1',
  author: 'Financial_Prune2298',
  first_seen_at: '2026-11-12T11:00:00Z',
  published_at: null,
  image_url: '/media/a1.jpg',
  is_saved: false,
  kind: 'article',
  sequence: 4,
  source: { group: 'AI', icon_url: '/sources/x/icon', name: 'r/codex', source_id: 's', subscription_id: 'sub', type: 'reddit' },
  summary: 'got 6 months',
  title: 'So happy this morning',
  title_translation: { engine_fingerprint: 'f', text: '今天早上开心极了' },
  url: 'https://reddit.com/x',
  x: null,
};

test('an article card: translated title, reddit byline, absolute image', () => {
  const card = toCard(base, NOW, absoluteUrl('https://r.example/api/'));
  assert.equal(card.title, '今天早上开心极了');
  assert.equal(card.seenAgo, '1 小时前');
  assert.deepEqual(card.source, { name: 'r/codex', kind: 'reddit', avatarUrl: 'https://r.example/api/sources/x/icon', byline: '/u/Financial_Prune2298' });
  assert.equal(card.imageUrl, 'https://r.example/api/media/a1.jpg');
  assert.equal(card.x, undefined);
});

test('an X card keeps author, quote and thread', () => {
  const card = toCard(
    {
      ...base,
      title_translation: null,
      source: { ...base.source!, type: 'x', name: 'Simon Willison' },
      x: {
        author: { name: 'Simon Willison', handle: 'simonw', avatar_url: 'https://pbs.example/a.jpg', id: '1' },
        completeness: 'parsed',
        is_repost: false,
        quote: { author: { name: 'Hamel', handle: 'HamelHusain', avatar_url: null, id: '2' }, availability: 'available', external_url: 'u', text: 'evals', tweet_id: '9' },
        reply_to: null,
        repost: null,
        thread_count: 7,
        tweet_id: '8',
      },
    },
    NOW,
    absoluteUrl('https://r.example'),
  );
  assert.deepEqual(card.x, {
    author: { name: 'Simon Willison', handle: 'simonw', avatarUrl: 'https://pbs.example/a.jpg' },
    text: 'So happy this morning',
    replyTo: undefined,
    repostedBy: undefined,
    quote: { author: 'Hamel', handle: 'HamelHusain', text: 'evals' },
    threadCount: 7,
  });
});

test('patching an item touches only the pages that hold it', () => {
  const pages = [{ items: [base] }, { items: [{ ...base, id: 'b2' }] }];
  const next = patchItems(pages, 'b2', { is_saved: true });
  assert.equal(next[0], pages[0]);
  assert.equal(next[1].items[0].is_saved, true);
  assert.equal(patchItems(pages, 'missing', { is_saved: true }), pages);
});

test('an X repost shows the original author and text, the reposter above', () => {
  const card = toCard(
    {
      ...base,
      title: 'RT wrapper',
      title_translation: null,
      source: { ...base.source!, type: 'x', name: 'Alice' },
      x: {
        author: { name: 'Alice', handle: 'alice', avatar_url: null, id: '1' },
        completeness: 'parsed',
        is_repost: true,
        quote: null,
        reply_to: null,
        repost: { author: { name: 'Bob', handle: 'bob', avatar_url: null, id: '2' }, availability: 'available', external_url: 'u', text: 'the original', tweet_id: '9' },
        thread_count: 1,
        tweet_id: '8',
      },
    },
    NOW,
    absoluteUrl('https://r.example'),
  );
  assert.equal(card.x?.repostedBy, 'Alice');
  assert.deepEqual(card.x?.author, { name: 'Bob', handle: 'bob', avatarUrl: null });
  assert.equal(card.x?.text, 'the original');
});
