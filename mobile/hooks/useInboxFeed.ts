import { useTranslation } from '../i18n';
import { useMemo } from 'react';
import type { Session } from '../lib/readerAuth';
import { infiniteQueryOptions, useInfiniteQuery, type InfiniteData, type QueryClient } from '@tanstack/react-query';

import { getFeedPage, type CursorPage, type FeedItem } from '../lib/api';
import { feedScopeCacheKey, feedScopeRequest, type FeedScope } from '../domain/feed';
import { readerQueryKeys } from '../state/queryClient';
import { useReaderRuntime } from '../lib/connection/react';
import type { ActiveReaderRuntime } from '../lib/connection';
import { useTitleTranslationConvergence } from './useTitleTranslationConvergence';
import { useXFeedTranslation } from './useXFeedTranslation';

const FEED_PAGE_SIZE = 20;

/**
 * The complete interface for one inbox scope lives here: key, cursor
 * protocol, page size, and cache policy are no longer repeated in screens.
 */
export function inboxFeedQueryOptions(session: Session, scope: FeedScope, translationLocale = 'zh-CN', serverId?: string, runtime?: ActiveReaderRuntime | null) {
  return infiniteQueryOptions<CursorPage<FeedItem>, Error, InfiniteData<CursorPage<FeedItem>>, ReturnType<typeof readerQueryKeys.feed>, string | null>({
    queryKey: readerQueryKeys.feed(session.user.id, feedScopeCacheKey(scope), translationLocale, serverId),
    initialPageParam: null,
    queryFn: ({ pageParam }) => getFeedPage(session, { ...feedScopeRequest(scope), cursor: pageParam, limit: FEED_PAGE_SIZE }, runtime ?? undefined),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
}

export function prefetchInboxFeed(queryClient: QueryClient, session: Session, scope: FeedScope, translationLocale = 'zh-CN', serverId?: string, runtime?: ActiveReaderRuntime | null): Promise<void> {
  return queryClient.prefetchInfiniteQuery(inboxFeedQueryOptions(session, scope, translationLocale, serverId, runtime));
}

export function useInboxFeed(
  session: Session,
  scope: FeedScope,
  active: boolean,
  translationLocale = 'zh-CN',
  engineId: string | null = null,
  translationEnabled = false,
) {
  const { t } = useTranslation('errors');
  const runtime = useReaderRuntime();
  const query = useInfiniteQuery({
    ...inboxFeedQueryOptions(session, scope, translationLocale, runtime?.identity.server_id, runtime),
    enabled: active,
  });

  const items = useMemo(() => {
    const seen = new Set<string>();
    return (query.data?.pages ?? []).flatMap((page) => page.items).filter((item) => {
      if (seen.has(item.content_id)) return false;
      seen.add(item.content_id);
      return true;
    });
  }, [query.data]);

  const titleTranslation = useTitleTranslationConvergence(
    session,
    items,
    active,
    translationLocale,
    engineId,
    `feed:${feedScopeCacheKey(scope)}`,
  );
  const xTranslation = useXFeedTranslation(
    session,
    items,
    active,
    translationEnabled,
    translationLocale,
    engineId,
    `feed:${feedScopeCacheKey(scope)}`,
  );

  const message = query.error
    ? query.error.message
    : query.data && items.length === 0
      ? scope.kind === 'source'
        ? t('sourceFeedEmpty')
        : t('feedEmpty')
      : '';

  return {
    ...query,
    items,
    message,
    loading: query.isPending && items.length === 0,
    refreshing: query.isRefetching && !query.isFetchingNextPage,
    titleTranslation,
    xTranslation,
  };
}
