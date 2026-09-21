import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import type { FeedItem, TranslationSegmentResult } from '../lib/api';
import type { Session } from '../lib/readerAuth';
import { useReaderRuntimeGeneration } from '../lib/connection/react';
import {
  xFeedCardTranslation,
  xFeedTranslationSegments,
  type XFeedCardTranslation,
  type XFeedTranslationRecord,
} from '../domain/xFeedTranslation';
import { useSegmentTranslationQueue } from './useSegmentTranslationQueue';

export type XFeedTranslationResult = {
  byContentId: ReadonlyMap<string, XFeedCardTranslation>;
  retry: (item: FeedItem) => void;
};

export function useXFeedTranslation(
  session: Session,
  items: FeedItem[],
  active: boolean,
  enabled: boolean,
  targetLocale: string,
  engineId: string | null,
  scopeKey: string,
): XFeedTranslationResult {
  const runtimeGeneration = useReaderRuntimeGeneration();
  const scopeKeyRef = useRef(scopeKey);
  const [records, setRecords] = useState<ReadonlyMap<string, XFeedTranslationRecord>>(new Map());
  const segments = useMemo(() => items.flatMap(xFeedTranslationSegments), [items]);
  const segmentsById = useMemo(
    () => new Map(segments.map((segment) => [segment.segment_id, segment])),
    [segments],
  );
  const mergeResults = useCallback((results: TranslationSegmentResult[]) => {
    setRecords((current) => {
      const next = new Map(current);
      let changed = false;
      for (const result of results) {
        const segment = segmentsById.get(result.segment_id);
        if (!segment) continue;
        next.set(result.segment_id, { result, sourceText: segment.text });
        changed = true;
      }
      return changed ? next : current;
    });
  }, [segmentsById]);
  const queue = useSegmentTranslationQueue({
    enabled: active && enabled,
    engineId,
    onResults: mergeResults,
    session,
    targetLocale,
  });
  const { enqueueSegments, reset: resetQueue, retrySegments } = queue;

  useEffect(() => {
    if (scopeKeyRef.current === scopeKey) return;
    scopeKeyRef.current = scopeKey;
    resetQueue();
  }, [resetQueue, scopeKey]);

  const pendingSegments = useMemo(() => segments.filter((segment) => {
    const record = records.get(segment.segment_id);
    if (!record || record.sourceText !== segment.text) return true;
    return record.result.translation_status === 'pending' || record.result.translation_status === 'running';
  }), [records, segments]);

  useEffect(() => {
    if (active && enabled) enqueueSegments(pendingSegments);
  }, [active, enabled, enqueueSegments, pendingSegments]);

  // Hide old-route text before the next frame; the queue separately aborts
  // old requests for preference, account, and connection identity changes.
  useLayoutEffect(() => {
    setRecords(new Map());
  }, [enabled, engineId, runtimeGeneration, scopeKey, session.user.id, targetLocale]);

  const retry = useCallback((item: FeedItem) => {
    const itemSegments = xFeedTranslationSegments(item);
    if (!itemSegments.length) return;
    const ids = new Set(itemSegments.map((segment) => segment.segment_id));
    setRecords((current) => {
      const next = new Map(current);
      ids.forEach((id) => next.delete(id));
      return next;
    });
    retrySegments(itemSegments);
  }, [retrySegments]);

  const byContentId = useMemo(() => {
    if (!active || !enabled) return new Map<string, XFeedCardTranslation>();
    const next = new Map<string, XFeedCardTranslation>();
    for (const item of items) {
      const translation = xFeedCardTranslation(
        item,
        records,
        queue.timedOutSegmentIds,
        xFeedTranslationSegments(item).some((segment) => queue.failedSegmentIds.has(segment.segment_id)),
      );
      if (translation) next.set(item.content_id, translation);
    }
    return next;
  }, [active, enabled, items, queue.failedSegmentIds, queue.timedOutSegmentIds, records]);

  return { byContentId, retry };
}
