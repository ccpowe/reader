import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';

import type { FeedItem, TranslationSegmentResult } from '../lib/api';
import type { Session } from '../lib/readerAuth';
import { useReaderRuntime, useReaderRuntimeGeneration } from '../lib/connection/react';
import {
  xFeedCardTranslation,
  xFeedTranslationSegments,
  type XFeedCardTranslation,
  type XFeedTranslationRecord,
} from '../domain/xFeedTranslation';
import { useSegmentTranslationQueue } from './useSegmentTranslationQueue';
import {
  activateXFeedTranslationMemory,
  readXFeedTranslationMemory,
  writeXFeedTranslationMemory,
  type XFeedTranslationMemoryRoute,
} from '../domain/xFeedTranslationMemory';

export type XFeedTranslationResult = {
  byContentId: ReadonlyMap<string, XFeedCardTranslation>;
  retry: (item: FeedItem) => void;
};

function sameTranslationResult(left: TranslationSegmentResult, right: TranslationSegmentResult): boolean {
  return left.cache_expires_at === right.cache_expires_at &&
    left.effective_engine_fingerprint === right.effective_engine_fingerprint &&
    left.engine_id === right.engine_id &&
    left.engine_label === right.engine_label &&
    left.error_code === right.error_code &&
    left.error_retryable === right.error_retryable &&
    left.purpose === right.purpose &&
    left.retry_after_ms === right.retry_after_ms &&
    left.segment_id === right.segment_id &&
    left.translated_text === right.translated_text &&
    left.translation_locale === right.translation_locale &&
    left.translation_status === right.translation_status;
}

function sameCardTranslation(left: XFeedCardTranslation, right: XFeedCardTranslation): boolean {
  return left.bodyText === right.bodyText &&
    left.referenceText === right.referenceText &&
    left.retryable === right.retryable;
}

export function useXFeedTranslation(
  session: Session,
  items: FeedItem[],
  active: boolean,
  enabled: boolean,
  targetLocale: string,
  engineId: string | null,
  scopeKey: string,
  engineFingerprint: string | null = null,
): XFeedTranslationResult {
  const runtime = useReaderRuntime();
  const runtimeGeneration = useReaderRuntimeGeneration();
  const scopeKeyRef = useRef(scopeKey);
  const projectedTranslationsRef = useRef<ReadonlyMap<string, XFeedCardTranslation>>(new Map());
  const [records, setRecords] = useState<ReadonlyMap<string, XFeedTranslationRecord>>(new Map());
  const segments = useMemo(() => items.flatMap(xFeedTranslationSegments), [items]);
  const segmentsById = useMemo(
    () => new Map(segments.map((segment) => [segment.segment_id, segment])),
    [segments],
  );
  const memoryRoute = useMemo<XFeedTranslationMemoryRoute>(() => ({
    engineFingerprint,
    serverId: runtime?.identity.server_id ?? 'unconfigured',
    targetLocale,
    userId: session.user.id,
  }), [engineFingerprint, runtime?.identity.server_id, session.user.id, targetLocale]);
  activateXFeedTranslationMemory(memoryRoute);
  const memoryRecords = useMemo(() => {
    const cached = new Map<string, XFeedTranslationRecord>();
    for (const segment of segments) {
      const record = readXFeedTranslationMemory(memoryRoute, segment.segment_id, segment.text);
      if (record) cached.set(segment.segment_id, record);
    }
    return cached;
  }, [memoryRoute, segments]);
  const effectiveRecords = useMemo(() => {
    if (!memoryRecords.size) return records;
    const merged = new Map(memoryRecords);
    records.forEach((record, id) => merged.set(id, record));
    return merged;
  }, [memoryRecords, records]);
  const mergeResults = useCallback((results: TranslationSegmentResult[]) => {
    setRecords((current) => {
      const next = new Map(current);
      let changed = false;
      for (const result of results) {
        if (
          engineFingerprint !== null &&
          result.effective_engine_fingerprint !== engineFingerprint
        ) continue;
        const segment = segmentsById.get(result.segment_id);
        if (!segment) continue;
        const existing = current.get(result.segment_id);
        if (
          existing?.sourceText === segment.text &&
          sameTranslationResult(existing.result, result)
        ) continue;
        next.set(result.segment_id, { result, sourceText: segment.text });
        writeXFeedTranslationMemory(memoryRoute, segment.text, result);
        changed = true;
      }
      return changed ? next : current;
    });
  }, [engineFingerprint, memoryRoute, segmentsById]);
  const queue = useSegmentTranslationQueue({
    enabled: active && enabled,
    engineId: engineFingerprint ?? engineId,
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
    const record = effectiveRecords.get(segment.segment_id);
    if (!record || record.sourceText !== segment.text) return true;
    return record.result.translation_status === 'pending' || record.result.translation_status === 'running';
  }), [effectiveRecords, segments]);

  useEffect(() => {
    if (active && enabled) enqueueSegments(pendingSegments);
  }, [active, enabled, enqueueSegments, pendingSegments]);

  // Hide old-route text before the next frame; the queue separately aborts
  // old requests for preference, account, and connection identity changes.
  useLayoutEffect(() => {
    setRecords(new Map());
  }, [enabled, engineFingerprint, engineId, runtimeGeneration, scopeKey, session.user.id, targetLocale]);

  const retry = useCallback((item: FeedItem) => {
    const itemSegments = xFeedTranslationSegments(item);
    if (!itemSegments.length) return;
    const ids = new Set(itemSegments.map((segment) => segment.segment_id));
    setRecords((current) => {
      const next = new Map(current);
      let changed = false;
      ids.forEach((id) => { changed = next.delete(id) || changed; });
      return changed ? next : current;
    });
    retrySegments(itemSegments);
  }, [retrySegments]);

  const byContentId = useMemo(() => {
    if (!active || !enabled) {
      const empty = new Map<string, XFeedCardTranslation>();
      projectedTranslationsRef.current = empty;
      return empty;
    }
    const previous = projectedTranslationsRef.current;
    const next = new Map<string, XFeedCardTranslation>();
    for (const item of items) {
      const translation = xFeedCardTranslation(
        item,
        effectiveRecords,
        queue.timedOutSegmentIds,
        xFeedTranslationSegments(item).some((segment) => queue.failedSegmentIds.has(segment.segment_id)),
      );
      if (translation) {
        const existing = previous.get(item.content_id);
        next.set(
          item.content_id,
          existing && sameCardTranslation(existing, translation) ? existing : translation,
        );
      }
    }
    projectedTranslationsRef.current = next;
    return next;
  }, [active, effectiveRecords, enabled, items, queue.failedSegmentIds, queue.timedOutSegmentIds]);

  return { byContentId, retry };
}
