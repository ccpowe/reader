import { useCallback, useEffect, useRef, useState } from 'react';
import type { Session } from '../lib/readerAuth';

import {
  resolveTranslationSegments,
  type RankingItem,
  type TranslationSegmentResult,
} from '../lib/api';
import { captureRuntimeContext, isRuntimeContextCurrent } from '../lib/connection';
import { useReaderRuntime, useReaderRuntimeGeneration } from '../lib/connection/react';

export type RankingTitleRetryItem = Pick<RankingItem, 'title' | 'translation_key'>;

type RankingTitleRetryOptions = {
  onFailure?: (item: RankingTitleRetryItem, cause: unknown) => void;
  onResults: (
    results: TranslationSegmentResult[],
    item: RankingTitleRetryItem,
    scopeKey: string,
  ) => void;
  session: Session;
  scopeKey?: string;
};

function titleSegmentId(item: RankingTitleRetryItem): string {
  return `${item.translation_key}:title`;
}

/**
 * Run one user-requested ranking-title retry.  This is deliberately separate
 * from the background queue: a failed item is terminal for automatic work and
 * only this explicit action may issue another request for it.
 */
export function useRankingTitleRetry({
  onFailure,
  onResults,
  session,
  scopeKey = 'ranking',
}: RankingTitleRetryOptions) {
  const runtime = useReaderRuntime();
  const runtimeGeneration = useReaderRuntimeGeneration();
  const operationGenerationRef = useRef(0);
  const retryingRef = useRef(new Set<string>());
  const [retryingIds, setRetryingIds] = useState<ReadonlySet<string>>(new Set());
  const onFailureRef = useRef(onFailure);
  const onResultsRef = useRef(onResults);

  onFailureRef.current = onFailure;
  onResultsRef.current = onResults;

  useEffect(() => {
    const retrying = retryingRef.current;
    operationGenerationRef.current += 1;
    retrying.clear();
    setRetryingIds(new Set());
    return () => {
      operationGenerationRef.current += 1;
      retrying.clear();
    };
  }, [runtimeGeneration, scopeKey]);

  const retryTitleTranslation = useCallback(async (item: RankingTitleRetryItem) => {
    const segmentId = titleSegmentId(item);
    if (!segmentId || retryingRef.current.has(segmentId)) return;
    const context = captureRuntimeContext(runtime);
    if (!context) return;
    const operationGeneration = operationGenerationRef.current;
    retryingRef.current.add(segmentId);
    setRetryingIds(new Set(retryingRef.current));
    try {
      const results = await resolveTranslationSegments(
        session,
        [{ purpose: 'ranking_title', segment_id: segmentId, text: item.title }],
        undefined,
        context.runtime,
      );
      if (
        operationGeneration !== operationGenerationRef.current ||
        !isRuntimeContextCurrent(context)
      ) return;
      onResultsRef.current(results, item, scopeKey);
    } catch (cause) {
      if (
        operationGeneration !== operationGenerationRef.current ||
        !isRuntimeContextCurrent(context)
      ) return;
      onFailureRef.current?.(item, cause);
    } finally {
      if (
        operationGeneration === operationGenerationRef.current &&
        isRuntimeContextCurrent(context)
      ) {
        retryingRef.current.delete(segmentId);
        setRetryingIds(new Set(retryingRef.current));
      }
    }
  }, [runtime, scopeKey, session]);

  const isTitleRetrying = useCallback(
    (item: RankingTitleRetryItem) => retryingIds.has(titleSegmentId(item)),
    [retryingIds],
  );

  return { isTitleRetrying, retryTitleTranslation, retryingIds };
}
