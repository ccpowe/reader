import assert from 'node:assert/strict';
import { test } from 'node:test';

import type { SubscriptionResponse } from './api/schema.gen.ts';
import {
  displayAddress,
  folderInput,
  foldersOf,
  healthLine,
  healthOf,
  scanErrorReason,
  searchFolders,
  toItem,
} from './subscriptions.ts';

const NOW = Date.parse('2026-11-12T10:00:00Z');
const sub = (over: Partial<SubscriptionResponse> = {}): SubscriptionResponse => ({
  id: 's1',
  source_id: 'src1',
  type: 'rss',
  name: 'Blog',
  url: 'https://www.example.com/feed.xml',
  icon_url: null,
  group: null,
  include_in_home: true,
  is_enabled: true,
  new_count: 0,
  latest_sequence: 0,
  status: 'active',
  last_success_at: '2026-11-12T07:00:00Z',
  next_scan_at: null,
  last_error_code: null,
  may_have_gap: false,
  created_at: '2026-11-01T00:00:00Z',
  ...over,
});
const absolute = (url: string | null | undefined) => url ?? null;

test('scan error reasons', () => {
  assert.equal(scanErrorReason('reddit_http_429'), '被对方限流');
  assert.equal(scanErrorReason('rate_limited'), '被对方限流', 'Scweet (X) says it without a status');
  assert.equal(scanErrorReason('user_not_found'), '账号不存在');
  assert.equal(scanErrorReason('rss_http_404'), '地址已经不存在');
  assert.equal(scanErrorReason('website_http_503'), '对方服务器出错');
  assert.equal(scanErrorReason('rss_http_418'), '对方返回错误 418');
  assert.equal(scanErrorReason('techtwitter_challenge_required'), '对方要求人机验证');
  assert.equal(scanErrorReason('youtube_timeout'), '对方响应太慢');
  assert.equal(scanErrorReason('rss_network_error'), '连不上对方');
  assert.equal(scanErrorReason('scweet_not_configured'), '服务器没有配置这类来源');
  assert.equal(scanErrorReason('youtube_quota_exceeded'), 'YouTube 配额用完了');
  assert.equal(scanErrorReason('something_new'), '暂时读不到');
});

test('health: paused, failing, waiting, gap, ok', () => {
  assert.deepEqual(healthOf(sub({ is_enabled: false, last_error_code: 'x' }), NOW), { kind: 'paused' });
  assert.deepEqual(healthOf(sub({ status: 'paused' }), NOW), { kind: 'paused' });
  assert.deepEqual(healthOf(sub({ last_error_code: 'rss_http_429' }), NOW), {
    kind: 'failing',
    reason: '被对方限流',
    since: '3 小时前成功过',
  });
  assert.equal(
    healthLine(healthOf(sub({ status: 'pending', last_success_at: null, last_error_code: 'rss_timeout' }), NOW)),
    '扫描失败 · 对方响应太慢 · 还没成功过',
  );
  assert.deepEqual(healthOf(sub({ status: 'pending', last_success_at: null }), NOW), { kind: 'waiting' });
  assert.equal(healthOf(sub({ may_have_gap: true }), NOW).kind, 'gap');
  assert.equal(healthLine(healthOf(sub(), NOW)), '同步正常');
});

test('rows, folders and search', () => {
  assert.equal(displayAddress('https://www.youtube.com/@x/'), 'youtube.com/@x');
  const row = toItem(sub({ name: '  ', type: 'website' }), NOW, absolute);
  assert.equal(row.name, 'example.com/feed.xml');
  assert.equal(row.kind, 'web');
  const list = [
    sub({ id: 'a', name: 'Zeta', group: 'AI' }),
    sub({ id: 'b', name: 'Alpha', group: 'AI' }),
    sub({ id: 'c', name: 'Loose', group: null }),
    sub({ id: 'd', name: 'News', group: '新闻', url: 'https://reddit.com/r/news' }),
  ];
  const folders = foldersOf(list, (s) => toItem(s, NOW, absolute));
  assert.deepEqual(
    folders.map((f) => [f.key, f.name, f.items.map((i) => i.id)]),
    [
      ['新闻', '新闻', ['d']],
      ['AI', 'AI', ['b', 'a']],
      ['', '未分类', ['c']],
    ],
  );
  assert.deepEqual(
    searchFolders(folders, 'REDDIT').map((i) => i.id),
    ['d'],
  );
  assert.deepEqual(
    searchFolders(folders, 'ai').map((i) => i.id),
    ['b', 'a'],
    'folder names match too',
  );
  assert.deepEqual(searchFolders(folders, '  '), []);
});

test('folder names as typed', () => {
  assert.equal(folderInput('  AI '), 'AI');
  assert.equal(folderInput(''), null);
  assert.equal(folderInput('未分类'), null);
  assert.equal(folderInput('x'.repeat(130))?.length, 120);
});
