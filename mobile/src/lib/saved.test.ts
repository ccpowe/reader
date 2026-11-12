import assert from 'node:assert/strict';
import { test } from 'node:test';

import type { SavedItem } from './api/schema.gen.ts';
import { savedCard, savedParams } from './saved.ts';

const NOW = Date.parse('2026-11-12T10:00:00Z');
const absolute = (url: string | null | undefined) => url ?? null;
const item = (over: Partial<SavedItem> = {}): SavedItem => ({
  id: 'a1',
  source: null,
  kind: 'article',
  title: 'Tool',
  url: 'https://gh/a',
  author: null,
  published_at: null,
  first_seen_at: '2026-11-12T09:00:00Z',
  summary: 'Does things',
  image_url: null,
  sequence: null,
  is_saved: true,
  saved_at: '2026-11-12T09:30:00Z',
  ...over,
});

test('what a saved list asks for', () => {
  assert.deepEqual(savedParams({ q: '  rust ', group: null }), { q: 'rust' });
  assert.deepEqual(savedParams({ q: '', group: '' }), { group: '' }, '未分类 is the empty folder');
  assert.deepEqual(savedParams({ q: '', group: 'AI' }), { group: 'AI' });
});

test('a board item names its board; a source item keeps its source', () => {
  const fromBoard = savedCard(item({ board: 'hn' }), NOW, absolute);
  assert.equal(fromBoard.source.name, 'Hacker News');
  assert.equal(fromBoard.source.kind, 'web');
  assert.equal(savedCard(item({ board: 'github' }), NOW, absolute).source.kind, 'github');
  const sourced = savedCard(
    item({ source: { source_id: 's', subscription_id: null, type: 'rss', name: 'Blog', icon_url: null } }),
    NOW,
    absolute,
  );
  assert.equal(sourced.source.name, 'Blog');
  assert.equal(savedCard(item(), NOW, absolute).source.name, '', 'no board, no source');
});
