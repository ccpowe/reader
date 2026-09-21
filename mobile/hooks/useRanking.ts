import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Session } from '../lib/readerAuth';
import { queryOptions, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';

import {
  getRanking,
  type Ranking,
  type RankingKind,
  type RedditRankingSort,
  type TranslationSegment,
  type TranslationSegmentResult,
} from '../lib/api';
import { readerQueryKeys } from '../state/queryClient';
import { useSegmentTranslationQueue } from './useSegmentTranslationQueue';
import { useRankingTitleRetry, type RankingTitleRetryItem } from './useRankingTitleRetry';
import { useReaderRuntime } from '../lib/connection/react';
import { captureRuntimeContext, isRuntimeContextCurrent } from '../lib/connection';

export type RankingOptions = {
  subreddit?: string;
  sort?: RedditRankingSort;
  timeFilter?: 'day' | 'week' | 'month' | 'year';
};

/**
 * The query-key variant identifies one logical ranking view. React Query owns
 * the cache; this function only serialises the selected Reddit filters.
 */
function rankingVariantKey(kind: RankingKind, options?: RankingOptions): string {
  if (kind !== 'reddit') return kind;
  return `reddit:${(options?.subreddit ?? 'MachineLearning').toLowerCase()}:${options?.sort ?? 'hot'}:${options?.timeFilter ?? 'week'}`;
}

export function rankingQueryOptions(session: Session, kind: RankingKind, options?: RankingOptions, serverId?: string, runtime?: import('../lib/connection').ActiveReaderRuntime | null) {
  return queryOptions<Ranking, Error, Ranking, ReturnType<typeof readerQueryKeys.rankings>>({
    queryKey: readerQueryKeys.rankings(session.user.id, rankingVariantKey(kind, options), serverId),
    queryFn: () => getRanking(session, kind, options, false, runtime ?? undefined),
  });
}

export function prefetchRanking(queryClient: QueryClient, session: Session, kind: RankingKind, options?: RankingOptions, serverId?: string, runtime?: import('../lib/connection').ActiveReaderRuntime | null): Promise<void> {
  return queryClient.prefetchQuery(rankingQueryOptions(session, kind, options, serverId, runtime));
}

export function useRanking(
  session: Session,
  kind: RankingKind,
  options: RankingOptions | undefined,
  active: boolean,
  translationItemLimit: number,
  translationEnabled: boolean,
  targetLocale: string,
  engineId: string | null,
) {
  const queryClient = useQueryClient();
  const runtime = useReaderRuntime();
  const queryKey = readerQueryKeys.rankings(session.user.id, rankingVariantKey(kind, options), runtime?.identity.server_id);
  const query = useQuery({
    ...rankingQueryOptions(session, kind, options, runtime?.identity.server_id, runtime),
    enabled: active && (kind !== 'reddit' || Boolean(options?.subreddit)),
  });
  const mergeTranslationResults = useCallback((results: TranslationSegmentResult[]) => {
    if (!results.length) return;
    const context = captureRuntimeContext(runtime);
    if (!context || !isRuntimeContextCurrent(context)) return;
    const byId = new Map(results.map((result) => [result.segment_id, result]));
    queryClient.setQueryData<Ranking>(queryKey, (current) => {
      if (!current) return current;
      let changed = false;
      const items = current.items.map((item) => {
        const title = byId.get(`${item.translation_key}:title`);
        const description = byId.get(`${item.translation_key}:description`);
        if (!title && !description) return item;
        changed = true;
        return {
          ...item,
          ...(title ? {
            translated_title: title.translated_text,
            title_translation_status: title.translation_status,
            translation_locale: title.translation_locale ?? item.translation_locale,
          } : {}),
          ...(description ? {
            translated_description: description.translated_text,
            description_translation_status: description.translation_status,
            translation_locale: description.translation_locale ?? item.translation_locale,
          } : {}),
        };
      });
      return changed ? { ...current, items } : current;
    });
  }, [queryClient, queryKey, runtime]);
  const translationQueue = useSegmentTranslationQueue({
    enabled: active && translationEnabled,
    engineId,
    onResults: mergeTranslationResults,
    session,
    targetLocale,
  });
  const { clearFailedSegments, clearTimedOutSegments, enqueueSegments, isTranslating } = translationQueue;
  const markTitleRetryFailure = useCallback((item: RankingTitleRetryItem) => {
    const context = captureRuntimeContext(runtime);
    if (!context || !isRuntimeContextCurrent(context)) return;
    clearTimedOutSegments([`${item.translation_key}:title`]);
    queryClient.setQueryData<Ranking>(queryKey, (current) => {
      if (!current) return current;
      const items = current.items.map((candidate) => candidate.translation_key === item.translation_key
        ? {
            ...candidate,
            translated_title: null,
            title_translation_status: 'failed' as const,
          }
        : candidate);
      return { ...current, items };
    });
  }, [clearTimedOutSegments, queryClient, queryKey, runtime]);
  const handleTitleRetryResults = useCallback((results: TranslationSegmentResult[]) => {
    const segmentIds = results.map((result) => result.segment_id);
    clearFailedSegments(segmentIds);
    clearTimedOutSegments(segmentIds);
    mergeTranslationResults(results);
  }, [clearFailedSegments, clearTimedOutSegments, mergeTranslationResults]);
  const {
    isTitleRetrying,
    retryTitleTranslation,
  } = useRankingTitleRetry({
    onFailure: markTitleRetryFailure,
    onResults: handleTitleRetryResults,
    session,
    scopeKey: queryKey.join('\u0000'),
  });
  const pendingSegments = useMemo(() => {
    const segments: TranslationSegment[] = [];
    for (const item of (query.data?.items ?? []).slice(0, translationItemLimit)) {
      if (
        !item.translated_title &&
        item.title_translation_status !== 'failed' &&
        item.title_translation_status !== 'succeeded'
      ) {
        segments.push({
          purpose: 'ranking_title',
          segment_id: `${item.translation_key}:title`,
          text: item.title,
        });
      }
      if (
        item.description &&
        !item.translated_description &&
        item.description_translation_status !== 'failed' &&
        item.description_translation_status !== 'succeeded'
      ) {
        segments.push({
          purpose: 'ranking_description',
          segment_id: `${item.translation_key}:description`,
          text: item.description,
        });
      }
    }
    return segments;
  }, [query.data, translationItemLimit]);
  useEffect(() => {
    if (active && translationEnabled) enqueueSegments(pendingSegments);
  }, [active, enqueueSegments, pendingSegments, targetLocale, translationEnabled]);
  const [refreshing, setRefreshing] = useState(false);
  const refresh = useCallback(async () => {
    const context = captureRuntimeContext(runtime);
    if (!context) return null;
    setRefreshing(true);
    try {
      return await queryClient.fetchQuery({
        ...rankingQueryOptions(session, kind, options, runtime?.identity.server_id, runtime),
        staleTime: 0,
        queryFn: () => getRanking(session, kind, options, true, runtime ?? undefined),
      });
    } finally {
      if (context && isRuntimeContextCurrent(context)) setRefreshing(false);
    }
  }, [kind, options, queryClient, runtime, session]);

  return {
    ...query,
    loading: query.isPending && query.fetchStatus !== 'idle' && !query.data,
    message: query.error ? query.error.message : '',
    ranking: query.data ?? null,
    refreshing: refreshing || query.isRefetching,
    translating: isTranslating,
    isTitleRetrying,
    retryTitleTranslation,
    refresh,
    timedOutSegmentIds: translationQueue.timedOutSegmentIds,
  };
}
