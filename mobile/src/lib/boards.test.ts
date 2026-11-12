import assert from 'node:assert/strict';
import { test } from 'node:test';

import type { BoardEntry, BoardResponse } from './api/schema.gen.ts';
import {
  BOARDS,
  boardLabel,
  communityName,
  compact,
  defaultView,
  freshness,
  missingTranslations,
  subscribedCommunities,
  toRow,
  unavailableReason,
  viewLabel,
  viewParams,
  withTranslations,
} from './boards.ts';

const NOW = Date.parse('2026-11-12T10:00:00Z');
const entry = (over: Partial<BoardEntry> = {}): BoardEntry => ({
  rank: 1,
  title: 'Title',
  url: 'https://e.com/a',
  image_urls: [],
  tags: [],
  is_saved: false,
  ...over,
});
const response = (over: Partial<BoardResponse> = {}): BoardResponse => ({
  board: 'hn',
  variant: 'top',
  subreddit: null,
  fetched_at: '2026-11-12T09:58:00Z',
  age_seconds: 120,
  stale: false,
  error_code: null,
  paused_until: null,
  items: [],
  ...over,
});

test('views: default version, request params, label', () => {
  assert.deepEqual(defaultView('github'), { board: 'github', variant: 'weekly', subreddit: null }); // old app's default
  assert.deepEqual(defaultView('hn'), { board: 'hn', variant: '', subreddit: null });
  assert.deepEqual(viewParams({ board: 'reddit', variant: 'top-week', subreddit: 'rust' }), {
    variant: 'top-week',
    subreddit: 'rust',
  });
  assert.deepEqual(viewParams(defaultView('hn')), {});
  assert.equal(viewLabel({ board: 'reddit', variant: 'hot', subreddit: 'MachineLearning' }), 'r/MachineLearning · 热门');
  assert.equal(viewLabel({ board: 'github', variant: 'weekly', subreddit: null }), '周榜');
  assert.equal(viewLabel(defaultView('hn')), undefined);
});

test('community names as typed', () => {
  assert.equal(communityName('r/Python'), 'Python');
  assert.equal(communityName(' /r/rust/ '), 'rust');
  assert.equal(communityName('https://www.reddit.com/r/MachineLearning/'), 'MachineLearning');
  assert.equal(communityName('no spaces'), null);
  assert.equal(communityName('r/'), null);
  assert.deepEqual(
    subscribedCommunities([
      { type: 'reddit', url: 'https://www.reddit.com/r/MachineLearning/' },
      { type: 'rss', url: 'https://reddit.com/r/nope' },
      { type: 'reddit', url: 'https://reddit.com/r/rust/hot' },
    ]),
    ['MachineLearning', 'rust'],
  );
});

test('freshness line: fresh, stale, paused with reason and time left', () => {
  assert.deepEqual(freshness(response(), NOW), { kind: 'fresh', age: '2 分钟前' });
  assert.deepEqual(freshness(response({ stale: true }), NOW), { kind: 'stale', age: '2 分钟前' });
  assert.deepEqual(
    freshness(response({ stale: true, error_code: 'reddit_http_429', paused_until: '2026-11-12T10:08:00Z' }), NOW),
    {
      kind: 'paused',
      reason: '被对方限流',
      resumesIn: '约 8 分钟',
    },
  );
  // a pause that has ended is just stale or fresh
  assert.equal(freshness(response({ paused_until: '2026-11-12T09:00:00Z' }), NOW)?.kind, 'fresh');
  assert.equal(freshness(response({ fetched_at: null }), NOW), null);
});

test('why a board is unavailable', () => {
  assert.equal(unavailableReason('The board could not be fetched (product_hunt_not_configured).'), '服务器没有配置这个榜单');
  assert.equal(
    unavailableReason('The board could not be fetched (techtwitter_challenge_required).'),
    '对方开启了人机验证，服务器暂时抓不到',
  );
  assert.equal(unavailableReason('whatever'), '服务器暂时抓不到这个榜单');
});

test('compact numbers', () => {
  assert.equal(compact(260), '260');
  assert.equal(compact(1000), '1k');
  assert.equal(compact(1040), '1k');
  assert.equal(compact(1250), '1.3k');
  assert.equal(compact(12_345), '12k');
  assert.equal(compact(193_000), '193k');
  assert.equal(compact(1_250_000), '1.3m');
  assert.equal(compact(null), null);
});

test('rows per board', () => {
  const hn = toRow(entry({ source_label: 'gatezh.com', score: 260, comments: 93, author: 'x' }), 'hn', NOW);
  assert.equal(hn.byline, 'gatezh.com');
  assert.equal(hn.score, '260');
  assert.equal(hn.comments, '93');
  const gh = toRow(
    entry({
      image_urls: ['https://a/1', 'https://a/2', 'https://a/3', 'https://a/4'],
      language: 'Rust',
      stars: 22_000,
      forks: 1600,
    }),
    'github',
    NOW,
  );
  assert.equal(gh.contributors.length, 3);
  assert.equal(gh.imageUrl, null);
  assert.equal(gh.stars, '22k');
  assert.equal(gh.forks, '1.6k');
  const reddit = toRow(entry({ author: 'rubanbhatia', image_urls: ['https://i/1.jpg', 'javascript:x'] }), 'reddit', NOW);
  assert.equal(reddit.byline, 'u/rubanbhatia');
  assert.equal(reddit.imageUrl, 'https://i/1.jpg');
  const tt = toRow(entry({ tags: ['AI', 'data', 'x', 'y', 'z'], quality_score: 8 }), 'techtwitter', NOW);
  assert.deepEqual(tt.tags, ['AI', 'data', 'x', 'y']);
  assert.equal(tt.quality, '质量 8');
  // translations replace title and description
  const translated = toRow(
    entry({
      description: 'D',
      title_translation: { text: '标题', engine_fingerprint: 'f' },
      description_translation: { text: '描述', engine_fingerprint: 'f' },
    }),
    'hn',
    NOW,
  );
  assert.equal(translated.title, '标题');
  assert.equal(translated.description, '描述');
});

test('missing translations are collected once and patched in', () => {
  const items = [
    entry({ title: 'A', description: 'dA' }),
    entry({ rank: 2, title: 'A' }),
    entry({ rank: 3, title: 'B', title_translation: { text: '乙', engine_fingerprint: 'f' } }),
  ];
  assert.deepEqual(missingTranslations(items), { titles: ['A'], descriptions: ['dA'] });
  const before = response({ items });
  const after = withTranslations(before, new Map([['A', '甲']]), new Map([['dA', '描述甲']]), 'fp');
  assert.equal(after.items[0].title_translation?.text, '甲');
  assert.equal(after.items[0].description_translation?.text, '描述甲');
  assert.equal(after.items[1].title_translation?.text, '甲');
  assert.equal(after.items[2], before.items[2], 'untouched entries keep their identity');
  assert.equal(withTranslations(before, new Map(), new Map(), 'fp'), before);
});

test('TechTwitter is hidden from the tabs but keeps its name for saved items (P32)', () => {
  assert.deepEqual(
    BOARDS.map((board) => board.key),
    ['hn', 'reddit', 'github', 'product_hunt'],
  );
  assert.equal(boardLabel('techtwitter'), 'X 技术热门');
  assert.equal(boardLabel('hn'), 'Hacker News');
  assert.equal(boardLabel('unknown'), 'unknown');
});
