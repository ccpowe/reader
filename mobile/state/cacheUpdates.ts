import type { InfiniteData, QueryClient, QueryKey } from '@tanstack/react-query';

import type { CursorPage, FeedItem, TitleTranslation } from '../lib/api';
import { isRuntimeContextCurrent, type ReaderRuntimeContext } from '../lib/connection/guard';
import { readerQueryKeys } from './queryClient';

export type ReaderFeedCache = InfiniteData<CursorPage<FeedItem>>;

/**
 * Update the saved flag in every cached feed scope without refetching.
 * The server remains authoritative; this only makes the UI respond instantly
 * while the mutation and subsequent invalidation are in flight.
 */
export function setCachedFeedSavedState(
  queryClient: QueryClient,
  userId: string,
  serverId: string,
  contentId: string,
  isSaved: boolean,
  context?: ReaderRuntimeContext | null,
): void {
  if (context && !isRuntimeContextCurrent(context)) return;
  queryClient.setQueriesData<ReaderFeedCache>(
    { queryKey: readerQueryKeys.feedPrefix(userId, serverId) },
    (current) => {
      if (!current) return current;
      return {
        ...current,
        pages: current.pages.map((page) => ({
          ...page,
          items: page.items.map((item) =>
            item.content_id === contentId ? { ...item, is_saved: isSaved } : item,
          ),
        })),
      };
    },
  );
}

/**
 * Remove one saved item and retain only its positions for an item-scoped rollback.
 * Keeping this operation beside the query keys prevents screen-local copies of
 * the same cache update from drifting apart.
 */
export type SavedItemRollback = {
  key: QueryKey;
  removed: { pageIndex: number; itemIndex: number; item: FeedItem }[];
}[];

export function removeCachedSavedItem(
  queryClient: QueryClient,
  userId: string,
  serverId: string,
  contentId: string,
  _translationLocale = 'zh-CN',
  context?: ReaderRuntimeContext | null,
): SavedItemRollback {
  if (context && !isRuntimeContextCurrent(context)) return [];
  const prefix = readerQueryKeys.savedPrefix(userId, serverId);
  const previous: SavedItemRollback = queryClient.getQueriesData<ReaderFeedCache>({ queryKey: prefix })
    .map(([key, data]) => ({
      key,
      removed: (data?.pages ?? []).flatMap((page, pageIndex) => page.items.flatMap((item, itemIndex) =>
        item.content_id === contentId ? [{ pageIndex, itemIndex, item }] : [],
      )),
    }));
  queryClient.setQueriesData<ReaderFeedCache>({ queryKey: prefix }, (current) => {
    if (!current) return current;
    return {
      ...current,
      pages: current.pages.map((page) => ({
        ...page,
        items: page.items.filter((item) => item.content_id !== contentId),
      })),
    };
  });
  return previous;
}

export function restoreSavedCache(
  queryClient: QueryClient,
  userId: string,
  serverId: string,
  previous: SavedItemRollback,
  _translationLocale = 'zh-CN',
  context?: ReaderRuntimeContext | null,
): void {
  if (context && !isRuntimeContextCurrent(context)) return;
  for (const { key, removed } of previous) {
    if (key[0] !== 'reader' || key[1] !== serverId || key[2] !== userId) continue;
    queryClient.setQueryData<ReaderFeedCache>(key, (current) => {
      if (!current?.pages.length) return current;
      const pages = current.pages.map((page) => ({ ...page, items: [...page.items] }));
      const present = new Set(pages.flatMap((page) => page.items.map((item) => item.content_id)));
      for (const { pageIndex, itemIndex, item } of removed) {
        if (present.has(item.content_id)) continue;
        const page = pages[Math.min(pageIndex, pages.length - 1)];
        page.items.splice(Math.min(itemIndex, page.items.length), 0, item);
        present.add(item.content_id);
      }
      return { ...current, pages };
    });
  }
}

/** Merge a compact translation delta into every cache for the same locale. */
export function mergeCachedTitleTranslations(
  queryClient: QueryClient,
  userId: string,
  serverId: string,
  translationLocale: string,
  translations: TitleTranslation[],
  context?: ReaderRuntimeContext | null,
): void {
  if (!translations.length || (context && !isRuntimeContextCurrent(context))) return;
  const byContentId = new Map(translations.map((item) => [item.content_id, item]));
  const feedLocaleMatches = (query: { queryKey: readonly unknown[] }) =>
    query.queryKey[query.queryKey.length - 1] === translationLocale;
  const savedLocaleMatches = (query: { queryKey: readonly unknown[] }) =>
    query.queryKey[4] === translationLocale;
  const merge = (current: ReaderFeedCache | undefined): ReaderFeedCache | undefined => {
    if (!current) return current;
    let changed = false;
    const pages = current.pages.map((page) => {
      let pageChanged = false;
      const items = page.items.map((item) => {
        const translation = byContentId.get(item.content_id);
        if (
          !translation ||
          (item.translated_title === translation.translated_title &&
            item.translation_locale === translation.translation_locale &&
            item.translation_status === translation.translation_status)
        ) {
          return item;
        }
        changed = true;
        pageChanged = true;
        return { ...item, ...translation };
      });
      return pageChanged ? { ...page, items } : page;
    });
    return changed ? { ...current, pages } : current;
  };
  queryClient.setQueriesData<ReaderFeedCache>(
    { predicate: feedLocaleMatches, queryKey: readerQueryKeys.feedPrefix(userId, serverId) },
    merge,
  );
  queryClient.setQueriesData<ReaderFeedCache>(
    { predicate: savedLocaleMatches, queryKey: readerQueryKeys.savedPrefix(userId, serverId) },
    merge,
  );
}
