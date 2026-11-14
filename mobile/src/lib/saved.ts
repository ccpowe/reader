/**
 * 收藏's pure logic: a saved item as a card (items saved from a board have no source; the card
 * names the board instead, PRODUCT §3.4) and what a saved list asks the server for.
 * Pure: runs under `node --test`.
 */
import type { SavedItem } from './api/schema.gen.ts';
import { boardLabel } from './boards.ts';
import { toCard, type CardSourceKind } from './feed.ts';

/** Which saved items a list shows: a search (title and summary) and a folder (null = 全部, '' = 未分类). */
export type SavedScope = { q: string; group: string | null };

export function savedParams(scope: SavedScope): Record<string, string> {
  const params: Record<string, string> = {};
  const q = scope.q.trim();
  if (q) params.q = q.slice(0, 200);
  if (scope.group !== null) params.group = scope.group;
  return params;
}

const boardKinds: Record<string, CardSourceKind> = { github: 'github', reddit: 'reddit', techtwitter: 'x' };

export function savedCard(item: SavedItem, now: number, absolute: (url: string | null | undefined) => string | null) {
  const card = toCard(item, now, absolute);
  if (item.source || !item.board) return card;
  const label = boardLabel(item.board);
  return { ...card, source: { ...card.source, name: label, kind: boardKinds[item.board] ?? 'web', avatarUrl: null } };
}
