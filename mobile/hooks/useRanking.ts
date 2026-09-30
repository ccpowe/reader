import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Session } from '../lib/readerAuth';
import { queryOptions, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';

import {
  getRanking,
  type Ranking,
  type RankingKind,
  type RedditRankingSort,
  type TranslationSegmentResult,
} from '../lib/api';
import { readerQueryKeys } from '../state/queryClient';
import { useSegmentTranslationQueue } from './useSegmentTranslationQueue';
import { useRankingTitleRetry, type RankingTitleRetryItem } from './useRankingTitleRetry';
import { useReaderRuntime } from '../lib/connection/react';
import { captureRuntimeContext, isRuntimeContextCurrent } from '../lib/connection';
import {
  mergeRankingTranslationProjection,
  missingRankingTranslationSegments,
  translationResultMatchesRankingRoute,
} from '../domain/rankingTranslationProjection';

export type RankingOptions = {
  subreddit?: string;
  sort?: RedditRankingSort;
  timeFilter?: 'day' | 'week' | 'month' | 'year';
};

/**
 * The query-key variant identifies one logical ranking view. React Query owns
 * the cache; this function only serialises the selected Reddit filters.
 */
export function rankingVariantKey(kind: RankingKind, options?: RankingOptions): string {
  if (kind !== 'reddit') return kind;
  return `reddit:${(options?.subreddit ?? 'MachineLearning').toLowerCase()}:${options?.sort ?? 'hot'}:${options?.timeFilter ?? 'week'}`;
}

export function rankingQueryOptions(
  session: Session,
  kind: RankingKind,
  options: RankingOptions | undefined,
  serverId: string | undefined,
  runtime: import('../lib/connection').ActiveReaderRuntime | null | undefined,
  targetLocale: string,
  translationEnabled: boolean,
  engineFingerprint: string | null,
) {
  return queryOptions<Ranking, Error, Ranking, ReturnType<typeof readerQueryKeys.rankings>>({
    queryKey: readerQueryKeys.rankings(
      session.user.id,
      rankingVariantKey(kind, options),
      targetLocale,
      translationEnabled,
      engineFingerprint,
      serverId,
    ),
    queryFn: () => getRanking(session, kind, options, false, runtime ?? undefined),
    gcTime: Infinity,
    structuralSharing: (previous, incoming) => (
      mergeRankingTranslationProjection(
        previous as Ranking | undefined,
        incoming as Ranking,
        targetLocale,
        translationEnabled,
        engineFingerprint,
      )
    ),
  });
}

export function prefetchRanking(
  queryClient: QueryClient,
  session: Session,
  kind: RankingKind,
  options: RankingOptions | undefined,
  serverId: string | undefined,
  runtime: import('../lib/connection').ActiveReaderRuntime | null | undefined,
  targetLocale: string,
  translationEnabled: boolean,
  engineFingerprint: string | null,
): Promise<void> {
  return queryClient.prefetchQuery(rankingQueryOptions(
    session,
    kind,
    options,
    serverId,
    runtime,
    targetLocale,
    translationEnabled,
    engineFingerprint,
  ));
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
  engineFingerprint: string | null,
  visibleItemKeys?: ReadonlySet<string>,
) {
  const queryClient = useQueryClient();
  const runtime = useReaderRuntime();
  const queryKey = readerQueryKeys.rankings(
    session.user.id,
    rankingVariantKey(kind, options),
    targetLocale,
    translationEnabled,
    engineFingerprint,
    runtime?.identity.server_id,
  );
  const query = useQuery({
    ...rankingQueryOptions(
      session,
      kind,
      options,
      runtime?.identity.server_id,
      runtime,
      targetLocale,
      translationEnabled,
      engineFingerprint,
    ),
    enabled: active && (kind !== 'reddit' || Boolean(options?.subreddit)),
  });
  const queuedSourcesRef = useRef(new Map<string, string>());
  const queuedRouteRef = useRef('');
  const routeIdentity = `${targetLocale}\u0000${translationEnabled}\u0000${engineFingerprint ?? engineId ?? ''}`;
  const translationResultMatchesRoute = useCallback((result: TranslationSegmentResult) => (
    translationResultMatchesRankingRoute(
      result,
      targetLocale,
      translationEnabled,
      engineFingerprint,
    )
  ), [engineFingerprint, targetLocale, translationEnabled]);
  const mergeTranslationResults = useCallback((
    results: TranslationSegmentResult[],
    expectedSources: ReadonlyMap<string, string>,
    expectedRouteIdentity: string,
  ) => {
    if (!translationEnabled || !results.length || expectedRouteIdentity !== routeIdentity) return;
    const context = captureRuntimeContext(runtime);
    if (!context || !isRuntimeContextCurrent(context)) return;
    const matchedResults = results.filter(translationResultMatchesRoute);
    if (!matchedResults.length) return;
    const byId = new Map(matchedResults.map((result) => [result.segment_id, result]));
    queryClient.setQueryData<Ranking>(queryKey, (current) => {
      if (!current) return current;
      let changed = false;
      const items = current.items.map((item) => {
        const title = byId.get(`${item.translation_key}:title`);
        const description = byId.get(`${item.translation_key}:description`);
        const bodySegments = item.body_segments?.map((segment) => {
          const result = byId.get(segment.segment_id);
          return result && expectedSources.get(result.segment_id) === segment.text
            ? { ...segment, translated_text: result.translated_text, translation_status: result.translation_status }
            : segment;
        });
        const freshBody = bodySegments?.some((segment, index) => segment !== item.body_segments?.[index]);
        const freshTitle = title
          && expectedSources.get(title.segment_id) === item.title
          ? title
          : null;
        const freshDescription = description
          && expectedSources.get(description.segment_id) === item.description
          ? description
          : null;
        if (!freshTitle && !freshDescription && !freshBody) return item;
        changed = true;
        return {
          ...item,
          ...(freshTitle ? {
            translated_title: freshTitle.translated_text,
            title_translation_status: freshTitle.translation_status,
            translation_locale: freshTitle.translation_locale ?? item.translation_locale,
          } : {}),
          ...(freshDescription ? {
            translated_description: freshDescription.translated_text,
            description_translation_status: freshDescription.translation_status,
            translation_locale: freshDescription.translation_locale ?? item.translation_locale,
          } : {}),
          ...(freshBody ? {
            body_segments: bodySegments,
            translation_locale: matchedResults[0]?.translation_locale ?? item.translation_locale,
          } : {}),
        };
      });
      return changed ? { ...current, effective_engine_fingerprint: engineFingerprint, items } : current;
    });
  }, [engineFingerprint, queryClient, queryKey, routeIdentity, runtime, translationEnabled, translationResultMatchesRoute]);
  const mergeQueuedTranslationResults = useCallback((results: TranslationSegmentResult[]) => {
    mergeTranslationResults(results, queuedSourcesRef.current, queuedRouteRef.current);
  }, [mergeTranslationResults]);
  const translationQueue = useSegmentTranslationQueue({
    enabled: active && translationEnabled,
    engineId: engineFingerprint ?? engineId,
    onResults: mergeQueuedTranslationResults,
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
  const retryScopeKey = queryKey.join('\u0000');
  const handleTitleRetryResults = useCallback((
    results: TranslationSegmentResult[],
    item: RankingTitleRetryItem,
    requestScopeKey: string,
  ) => {
    if (requestScopeKey !== retryScopeKey) return;
    const matchedResults = results.filter(translationResultMatchesRoute);
    if (!matchedResults.length) return;
    const segmentIds = matchedResults.map((result) => result.segment_id);
    clearFailedSegments(segmentIds);
    clearTimedOutSegments(segmentIds);
    mergeTranslationResults(
      matchedResults,
      new Map([[`${item.translation_key}:title`, item.title]]),
      routeIdentity,
    );
  }, [clearFailedSegments, clearTimedOutSegments, mergeTranslationResults, retryScopeKey, routeIdentity, translationResultMatchesRoute]);
  const {
    isTitleRetrying,
    retryTitleTranslation,
  } = useRankingTitleRetry({
    onFailure: markTitleRetryFailure,
    onResults: handleTitleRetryResults,
    session,
    scopeKey: retryScopeKey,
  });
  const pendingSegments = useMemo(
    () => missingRankingTranslationSegments(
      query.data,
      translationItemLimit,
      targetLocale,
      translationEnabled,
      engineFingerprint,
      visibleItemKeys,
    ),
    [engineFingerprint, query.data, targetLocale, translationEnabled, translationItemLimit, visibleItemKeys],
  );
  useEffect(() => {
    if (!active || !translationEnabled) return;
    if (queuedRouteRef.current !== routeIdentity) queuedSourcesRef.current.clear();
    for (const segment of pendingSegments) queuedSourcesRef.current.set(segment.segment_id, segment.text);
    queuedRouteRef.current = routeIdentity;
    enqueueSegments(pendingSegments);
  }, [active, enqueueSegments, pendingSegments, routeIdentity, translationEnabled]);
  const [refreshing, setRefreshing] = useState(false);
  const refresh = useCallback(async () => {
    const context = captureRuntimeContext(runtime);
    if (!context) return null;
    setRefreshing(true);
    try {
      return await queryClient.fetchQuery({
        ...rankingQueryOptions(
          session,
          kind,
          options,
          runtime?.identity.server_id,
          runtime,
          targetLocale,
          translationEnabled,
          engineFingerprint,
        ),
        staleTime: 0,
        queryFn: () => getRanking(session, kind, options, true, runtime ?? undefined),
      });
    } finally {
      if (context && isRuntimeContextCurrent(context)) setRefreshing(false);
    }
  }, [engineFingerprint, kind, options, queryClient, runtime, session, targetLocale, translationEnabled]);

  return {
    translationEnabled,
    ...query,
    loading: query.isPending && query.fetchStatus !== 'idle' && !query.data,
    message: query.error ? query.error.message : '',
    ranking: query.data ?? null,
    refreshing: refreshing || query.isRefetching,
    translating: isTranslating,
    isTitleRetrying,
    retryTitleTranslation,
    refresh,
    retryBodyTranslation: (item: Ranking['items'][number]) => {
      const segments = (item.body_segments ?? [])
        .filter((segment) => segment.text.trim() && segment.translation_status !== 'succeeded')
        .map((segment) => ({ purpose: 'paragraph' as const, segment_id: segment.segment_id, text: segment.text }));
      for (const segment of segments) queuedSourcesRef.current.set(segment.segment_id, segment.text);
      queuedRouteRef.current = routeIdentity;
      translationQueue.retrySegments(segments);
    },
    failedSegmentIds: translationQueue.failedSegmentIds,
    timedOutSegmentIds: translationQueue.timedOutSegmentIds,
  };
}
