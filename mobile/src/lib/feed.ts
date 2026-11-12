/**
 * Today's pure logic: which feeds exist (folders), what a feed request asks for, and how an API
 * item becomes a card. Pure: runs under `node --test`.
 */
import type { FeedItem, SubscriptionResponse } from './api/schema.gen.ts';
import { t } from './i18n/index.ts';

/** What one feed page shows. `group: ''` = subscriptions without a group (API contract). */
export type FeedScope = { kind: 'home' } | { kind: 'group'; group: string } | { kind: 'subscription'; id: string };

export const UNGROUPED = '';

/** Folder tabs after 全部: groups in Chinese collation order, 未分类 last when it has members. */
export function folderList(subscriptions: readonly Pick<SubscriptionResponse, 'group'>[]): string[] {
  const groups = new Set(subscriptions.map((s) => s.group?.trim() || UNGROUPED));
  return [...groups].sort((a, b) => (a === UNGROUPED ? 1 : b === UNGROUPED ? -1 : a.localeCompare(b, 'zh-CN')));
}

export function folderLabel(group: string): string {
  return group === UNGROUPED ? t().common.uncategorized : group;
}

export function scopeParams(scope: FeedScope): Record<string, string> {
  if (scope.kind === 'subscription') return { subscription: scope.id };
  if (scope.kind === 'group') return { group: scope.group };
  return { scope: 'home' };
}

/** The filter badge: new items across the subscriptions shown on 全部 (old app). */
export function homeNewCount(subscriptions: readonly Pick<SubscriptionResponse, 'include_in_home' | 'is_enabled' | 'new_count'>[]): number {
  return subscriptions.reduce((sum, s) => (s.include_in_home && s.is_enabled ? sum + Math.max(0, s.new_count) : sum), 0);
}

/** "12 分钟前", "3 小时后" (a published date in the future, as the old app showed it). */
export function relativeTime(iso: string, now: number): string {
  const elapsed = now - Date.parse(iso);
  if (Number.isNaN(elapsed)) return '';
  const future = elapsed < 0;
  const minutes = Math.floor(Math.abs(elapsed) / 60_000);
  const time = t().time;
  if (minutes < 1) return time.justNow;
  if (minutes < 60) return time.minutes(minutes, future);
  if (minutes < 1_440) return time.hours(Math.floor(minutes / 60), future);
  return time.days(Math.floor(minutes / 1_440), future);
}

export type CardSourceKind = 'rss' | 'web' | 'youtube' | 'x' | 'reddit' | 'github';

export function sourceKind(type: string | undefined): CardSourceKind {
  if (type === 'website') return 'web';
  if (type === 'youtube' || type === 'x' || type === 'reddit') return type;
  return 'rss';
}

/** The card's data (src/components/FeedCard.tsx). Titles show the translation when there is one (P15). */
export function toCard(item: FeedItem, now: number, absolute: (url: string | null | undefined) => string | null) {
  const x = item.x;
  const name = item.source?.name ?? '';
  return {
    id: item.id,
    source: {
      name,
      kind: sourceKind(item.source?.type),
      avatarUrl: absolute(item.source?.icon_url),
      byline: item.source?.type === 'reddit' && item.author ? `/u/${item.author.replace(/^\/?u\//, '')}` : null,
    },
    seenAgo: relativeTime(item.published_at ?? item.first_seen_at, now),
    title: item.title_translation?.text ?? item.title,
    summary: item.summary,
    imageUrl: absolute(item.image_url),
    isSaved: item.is_saved,
    x: x
      ? {
          // A repost shows the original post and author, with the reposter on the line above (P16).
          author:
            x.is_repost && x.repost
              ? { name: x.repost.author.name ?? x.repost.author.handle ?? '', handle: x.repost.author.handle ?? '', avatarUrl: absolute(x.repost.author.avatar_url) }
              : { name: x.author.name ?? name, handle: x.author.handle ?? '', avatarUrl: absolute(x.author.avatar_url) },
          text:
            x.is_repost && x.repost
              ? (x.repost.text ?? (x.repost.availability === 'available' ? '' : t().feed.repostGone))
              : (item.title_translation?.text ?? item.title),
          replyTo: x.reply_to?.handle ?? undefined,
          repostedBy: x.is_repost ? (x.author.name ?? name) : undefined,
          quote: x.quote?.text
            ? { author: x.quote.author.name ?? '', handle: x.quote.author.handle ?? '', text: x.quote.text }
            : undefined,
          threadCount: x.thread_count,
        }
      : undefined,
  };
}

/** Server-relative URLs (icons, avatars) are made absolute against the API base. */
export function absoluteUrl(apiBaseUrl: string) {
  return (url: string | null | undefined): string | null => {
    if (!url) return null;
    if (/^https?:\/\//i.test(url)) return url;
    return `${apiBaseUrl.replace(/\/+$/, '')}/${url.replace(/^\/+/, '')}`;
  };
}

/** Change one item everywhere it appears in cached feed pages (saving, unsaving). */
export function patchItems<P extends { items: FeedItem[] }>(pages: P[], id: string, patch: Partial<FeedItem>): P[] {
  let changed = false;
  const next = pages.map((page) => {
    if (!page.items.some((item) => item.id === id)) return page;
    changed = true;
    return { ...page, items: page.items.map((item) => (item.id === id ? { ...item, ...patch } : item)) };
  });
  return changed ? next : pages;
}

/** What a list item needs for filling in its title translation (feed and saved items). */
type Titled = { title: string; title_translation?: { text: string; engine_fingerprint: string } | null; board?: string | null };

/**
 * Titles the loaded items still lack a translation for, by the purpose they are asked with: items
 * saved from a board use `board_title`, so the board's own translation is found in the cache.
 */
export function missingTitles(items: readonly Titled[]): { title: string[]; board_title: string[] } {
  const title = new Set<string>();
  const board = new Set<string>();
  for (const item of items) {
    if (item.title_translation || !item.title.trim()) continue;
    (item.board ? board : title).add(item.title);
  }
  return { title: [...title], board_title: [...board] };
}

/** Put finished title translations into the items; untouched items keep their object (the card cache). */
export function withTitleTranslations<T extends Titled>(items: readonly T[], done: ReadonlyMap<string, string> | undefined, fingerprint: string): T[] {
  if (!done || done.size === 0) return items as T[];
  return items.map((item) => {
    const text = item.title_translation ? undefined : done.get(item.title);
    return text ? { ...item, title_translation: { text, engine_fingerprint: fingerprint } } : item;
  });
}
