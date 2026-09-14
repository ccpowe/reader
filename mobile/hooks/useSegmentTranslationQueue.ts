import { i18n, useTranslation } from '../i18n';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { Session } from '../lib/readerAuth';

import {
  segmentConvergenceTimeoutMessage,
  nextTranslationConvergencePollAt,
  translationConvergenceDecision,
} from '../domain/translationConvergence';
import { translationTransportRetryDecision } from '../domain/translationRetry';
import { captureRuntimeContext, isRuntimeContextCurrent } from '../lib/connection';
import { useReaderRuntime, useReaderRuntimeGeneration } from '../lib/connection/react';
import {
  resolveTranslationSegments,
  type TranslationSegment,
  type TranslationSegmentResult,
} from '../lib/api';

const CONVERGENCE_INTERVAL_MS = 750;
const MAX_RETRY_AFTER_MS = 30_000;
const MAX_CONCURRENT_REQUESTS = 2;
const MAX_SEGMENTS_PER_REQUEST = 12;
const MAX_SEGMENT_CHARS = 8_000;
const MAX_REQUEST_CHARS = 5_000;
const MAX_COMPLETED_RESULTS = 1_000;
const QUOTA_ERROR_CODES = new Set(['rate_limited']);

type QueueRecord = {
  availableAt: number;
  convergenceStartedAt: number;
  failureCount: number;
  inFlightVersion: number | null;
  segment: TranslationSegment;
  version: number;
};

type CompletedRecord = {
  result: TranslationSegmentResult;
  segment: TranslationSegment;
};

type SegmentTranslationQueueOptions = {
  enabled: boolean;
  engineId?: string | null;
  onResults: (results: TranslationSegmentResult[]) => void;
  session: Session;
  targetLocale: string;
};

type QueueError =
  | { kind: 'convergence' }
  | { kind: 'service'; messageKey: 'engineQuota' | 'engineUnavailable' | 'engineFailed'; engineLabel: string | null }
  | { kind: 'transport'; cause: unknown; stopped: boolean };

function sameSegment(left: TranslationSegment, right: TranslationSegment): boolean {
  return left.purpose === right.purpose &&
    left.text === right.text;
}

function segmentCharacterCount(segment: TranslationSegment): number {
  return segment.text.length;
}

function translationServiceError(results: TranslationSegmentResult[]): QueueError | null {
  const errorCodes = results
    .map((result) => result.error_code)
    .filter((code): code is string => Boolean(code));
  const engineLabel = results.find((result) => result.engine_label)?.engine_label
    ?? null;
  if (errorCodes.some((code) => QUOTA_ERROR_CODES.has(code))) {
    return { kind: 'service', messageKey: 'engineQuota', engineLabel };
  }
  if (errorCodes.some((code) => (
    code === 'http_401' ||
    code === 'authentication_failed' ||
    code === 'missing_deepseek_api_key' ||
    code === 'engine_unavailable'
  ))) {
    return { kind: 'service', messageKey: 'engineUnavailable', engineLabel };
  }
  if (results.some((result) => result.translation_status === 'failed')) {
    return { kind: 'service', messageKey: 'engineFailed', engineLabel };
  }
  return null;
}

function queueErrorMessage(error: QueueError | null): string | null {
  if (!error) return null;
  if (error.kind === 'convergence') return segmentConvergenceTimeoutMessage();
  if (error.kind === 'service') {
    return i18n.t(`errors:${error.messageKey}`, {
      engineLabel: error.engineLabel ?? i18n.t('errors:aiTranslation'),
    });
  }
  const message = error.cause instanceof Error ? error.cause.message : i18n.t('errors:translationUnavailable');
  return error.stopped ? i18n.t('errors:automaticRetryStopped', { message }) : message;
}

/**
 * Converges an open-ended stream of UI text without tying it to one screen.
 *
 * WebViews can discover paragraphs or caption cues over time, while ranking
 * screens already know their segments. Both feed this queue and receive the
 * same bounded, stale-safe result stream.
 */
export function useSegmentTranslationQueue({
  enabled,
  engineId,
  onResults,
  session,
  targetLocale,
}: SegmentTranslationQueueOptions) {
  useTranslation('errors');
  const runtimeGeneration = useReaderRuntimeGeneration();
  const runtime = useReaderRuntime();
  const pendingRef = useRef(new Map<string, QueueRecord>());
  const completedRef = useRef(new Map<string, CompletedRecord>());
  const timedOutRef = useRef(new Map<string, TranslationSegment>());
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeControllersRef = useRef(new Set<AbortController>());
  const inFlightCountRef = useRef(0);
  const mountedRef = useRef(true);
  const enabledRef = useRef(enabled);
  const generationRef = useRef(0);
  const versionRef = useRef(0);
  const pumpRef = useRef<() => void>(() => undefined);
  const onResultsRef = useRef(onResults);
  const [activeCount, setActiveCount] = useState(0);
  const [error, setError] = useState<QueueError | null>(null);
  const [timedOutSegmentIds, setTimedOutSegmentIds] = useState<ReadonlySet<string>>(new Set());

  enabledRef.current = enabled;
  onResultsRef.current = onResults;

  const updateActiveCount = useCallback(() => {
    if (mountedRef.current) setActiveCount(pendingRef.current.size);
  }, []);

  const schedulePump = useCallback((delayMs = 0) => {
    if (!enabledRef.current || !mountedRef.current) return;
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      pumpRef.current();
    }, Math.max(delayMs, 0));
  }, []);

  const reset = useCallback(() => {
    generationRef.current += 1;
    activeControllersRef.current.forEach((controller) => controller.abort());
    activeControllersRef.current.clear();
    inFlightCountRef.current = 0;
    pendingRef.current.clear();
    completedRef.current.clear();
    timedOutRef.current.clear();
    setTimedOutSegmentIds(new Set());
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = null;
    setError(null);
    updateActiveCount();
  }, [updateActiveCount]);

  const pump = useCallback(async () => {
    if (
      !enabledRef.current ||
      !mountedRef.current ||
      inFlightCountRef.current >= MAX_CONCURRENT_REQUESTS
    ) return;
    const now = Date.now();
    let expiredPending = false;
    for (const record of pendingRef.current.values()) {
      if (
        record.inFlightVersion === null &&
        translationConvergenceDecision(
          'pending',
          record.convergenceStartedAt,
          now,
        ) === 'timed_out'
      ) {
        pendingRef.current.delete(record.segment.segment_id);
        timedOutRef.current.set(record.segment.segment_id, record.segment);
        expiredPending = true;
      }
    }
    if (expiredPending) {
      setTimedOutSegmentIds(new Set(timedOutRef.current.keys()));
      setError({ kind: 'convergence' });
      updateActiveCount();
    }
    const due = [...pendingRef.current.values()]
      .filter((record) => record.inFlightVersion === null && record.availableAt <= now)
      .sort((left, right) => left.availableAt - right.availableAt);
    if (!due.length) {
      const next = Math.min(
        ...[...pendingRef.current.values()]
          .filter((record) => record.inFlightVersion === null)
          .map((record) => record.availableAt),
      );
      if (Number.isFinite(next)) schedulePump(next - now);
      return;
    }

    const batch: QueueRecord[] = [];
    let characterCount = 0;
    for (const record of due) {
      const nextCount = characterCount + segmentCharacterCount(record.segment);
      if (batch.length && nextCount > MAX_REQUEST_CHARS) break;
      if (record.segment.text.length > MAX_SEGMENT_CHARS) {
        pendingRef.current.delete(record.segment.segment_id);
        continue;
      }
      batch.push(record);
      characterCount = nextCount;
      if (batch.length >= MAX_SEGMENTS_PER_REQUEST) break;
    }
    updateActiveCount();
    if (!batch.length) {
      schedulePump(0);
      return;
    }

    const generation = generationRef.current;
    const context = captureRuntimeContext(runtime);
    if (!context) return;
    const sentVersions = new Map(
      batch.map((record) => [record.segment.segment_id, record.version]),
    );
    batch.forEach((record) => {
      record.inFlightVersion = record.version;
    });
    const controller = new AbortController();
    activeControllersRef.current.add(controller);
    inFlightCountRef.current += 1;
    if (inFlightCountRef.current < MAX_CONCURRENT_REQUESTS) schedulePump(0);
    try {
      const results = await resolveTranslationSegments(
        session,
        batch.map((record) => record.segment),
        controller.signal,
        context.runtime,
      );
      if (generation !== generationRef.current || !mountedRef.current || !isRuntimeContextCurrent(context)) return;

      for (const record of batch) {
        const current = pendingRef.current.get(record.segment.segment_id);
        if (current?.version === record.version) current.failureCount = 0;
      }

      const freshResults = results.filter((result) => {
        const current = pendingRef.current.get(result.segment_id);
        return current?.version === sentVersions.get(result.segment_id);
      });
      if (freshResults.length && isRuntimeContextCurrent(context)) onResultsRef.current(freshResults);

      const returnedIds = new Set(freshResults.map((result) => result.segment_id));
      let convergenceTimedOut = false;
      for (const result of freshResults) {
        const current = pendingRef.current.get(result.segment_id);
        if (!current) continue;
        if (result.translation_status === 'succeeded' && result.translated_text) {
          pendingRef.current.delete(result.segment_id);
          completedRef.current.set(result.segment_id, {
            result,
            segment: current.segment,
          });
          continue;
        }
        const convergence = translationConvergenceDecision(
          result.translation_status,
          current.convergenceStartedAt,
          Date.now(),
        );
        if (convergence === 'timed_out') {
          pendingRef.current.delete(result.segment_id);
          timedOutRef.current.set(result.segment_id, current.segment);
          setTimedOutSegmentIds(new Set(timedOutRef.current.keys()));
          convergenceTimedOut = true;
          continue;
        }
        if (convergence === 'settled') {
          pendingRef.current.delete(result.segment_id);
          continue;
        }
        const resultAt = Date.now();
        current.availableAt = nextTranslationConvergencePollAt(
          current.convergenceStartedAt,
          resultAt,
          Math.max(
            CONVERGENCE_INTERVAL_MS,
            Math.min(result.retry_after_ms ?? 0, MAX_RETRY_AFTER_MS),
          ),
        );
      }
      for (const record of batch) {
        if (returnedIds.has(record.segment.segment_id)) continue;
        const current = pendingRef.current.get(record.segment.segment_id);
        if (current?.version === record.version) {
          if (
            translationConvergenceDecision(
              'pending',
              current.convergenceStartedAt,
              Date.now(),
          ) === 'timed_out'
          ) {
            pendingRef.current.delete(record.segment.segment_id);
            timedOutRef.current.set(record.segment.segment_id, current.segment);
            setTimedOutSegmentIds(new Set(timedOutRef.current.keys()));
            convergenceTimedOut = true;
          } else {
            const resultAt = Date.now();
            current.availableAt = nextTranslationConvergencePollAt(
              current.convergenceStartedAt,
              resultAt,
              CONVERGENCE_INTERVAL_MS,
            );
          }
        }
      }
      while (completedRef.current.size > MAX_COMPLETED_RESULTS) {
        const oldest = completedRef.current.keys().next().value as string | undefined;
        if (!oldest) break;
        completedRef.current.delete(oldest);
      }
      if (isRuntimeContextCurrent(context)) {
        setError(
          convergenceTimedOut || timedOutRef.current.size > 0
            ? { kind: 'convergence' }
            : translationServiceError(freshResults),
        );
      }
    } catch (cause) {
      if (generation !== generationRef.current || !mountedRef.current || !isRuntimeContextCurrent(context)) return;
      let stopped = false;
      for (const record of batch) {
        const current = pendingRef.current.get(record.segment.segment_id);
        if (current?.version === record.version) {
          const decision = translationTransportRetryDecision(cause, current.failureCount);
          current.failureCount = decision.failureCount;
          if (decision.retry && decision.delayMs !== null) {
            current.availableAt = Date.now() + decision.delayMs;
          } else {
            pendingRef.current.delete(record.segment.segment_id);
            stopped = true;
          }
        }
      }
      if (isRuntimeContextCurrent(context)) setError({ kind: 'transport', cause, stopped });
    } finally {
      for (const record of batch) {
        const current = pendingRef.current.get(record.segment.segment_id);
        if (current?.inFlightVersion === record.version) current.inFlightVersion = null;
      }
      if (activeControllersRef.current.delete(controller)) {
        inFlightCountRef.current = Math.max(inFlightCountRef.current - 1, 0);
      }
      if (mountedRef.current && isRuntimeContextCurrent(context)) {
        updateActiveCount();
        const next = Math.min(
          ...[...pendingRef.current.values()]
            .filter((record) => record.inFlightVersion === null)
            .map((record) => record.availableAt),
        );
        if (Number.isFinite(next) && inFlightCountRef.current < MAX_CONCURRENT_REQUESTS) {
          schedulePump(next - Date.now());
        }
      }
    }
  }, [runtime, schedulePump, session, updateActiveCount]);

  pumpRef.current = () => {
    void pump();
  };

  const enqueueSegments = useCallback((segments: TranslationSegment[]) => {
    if (!enabledRef.current || !segments.length) return;
    const context = captureRuntimeContext(runtime);
    if (!context) return;
    const cachedResults: TranslationSegmentResult[] = [];
    const seen = new Set<string>();
    let clearedTimedOutSegment = false;
    for (const candidate of segments) {
      const text = candidate.text.trim();
      if (
        seen.has(candidate.segment_id) ||
        !candidate.segment_id ||
        candidate.segment_id.length > 160 ||
        !text ||
        text.length > MAX_SEGMENT_CHARS
      ) continue;
      seen.add(candidate.segment_id);
      const segment = { ...candidate, text };
      const completed = completedRef.current.get(segment.segment_id);
      if (completed && sameSegment(completed.segment, segment)) {
        cachedResults.push(completed.result);
        continue;
      }
      if (completed) completedRef.current.delete(segment.segment_id);
      const pending = pendingRef.current.get(segment.segment_id);
      if (pending && sameSegment(pending.segment, segment)) continue;
      const timedOut = timedOutRef.current.has(segment.segment_id);
      // A render, query refresh, or re-entry must not silently reopen a
      // convergence window.  `retrySegments` is the only path that sets
      // allowTimedOut and deliberately starts a fresh window.
      if (timedOut) continue;
      if (timedOutRef.current.delete(segment.segment_id)) clearedTimedOutSegment = true;
      versionRef.current += 1;
      pendingRef.current.set(segment.segment_id, {
        availableAt: Date.now(),
        convergenceStartedAt: Date.now(),
        failureCount: 0,
        inFlightVersion: null,
        segment,
        version: versionRef.current,
      });
    }
    if (clearedTimedOutSegment) {
      setTimedOutSegmentIds(new Set(timedOutRef.current.keys()));
      if (timedOutRef.current.size === 0) setError(null);
    }
    if (cachedResults.length && isRuntimeContextCurrent(context)) onResultsRef.current(cachedResults);
    updateActiveCount();
    schedulePump(0);
  }, [runtime, schedulePump, updateActiveCount]);

  const retrySegments = useCallback((segments: TranslationSegment[]) => {
    if (!enabledRef.current || !segments.length) return;
    for (const segment of segments) {
      if (segment.segment_id) timedOutRef.current.delete(segment.segment_id);
    }
    setTimedOutSegmentIds(new Set(timedOutRef.current.keys()));
    if (!timedOutRef.current.size) setError(null);
    enqueueSegments(segments);
  }, [enqueueSegments]);

  const clearTimedOutSegments = useCallback((segmentIds: string[]) => {
    if (!segmentIds.length) return;
    let changed = false;
    for (const segmentId of segmentIds) {
      changed = timedOutRef.current.delete(segmentId) || changed;
    }
    if (!changed) return;
    setTimedOutSegmentIds(new Set(timedOutRef.current.keys()));
    if (!timedOutRef.current.size) setError(null);
  }, []);

  useEffect(() => {
    reset();
  }, [enabled, engineId, reset, runtimeGeneration, session.user.id, targetLocale]);

  useEffect(() => {
    const activeControllers = activeControllersRef.current;
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      generationRef.current += 1;
      activeControllers.forEach((controller) => controller.abort());
      activeControllers.clear();
      inFlightCountRef.current = 0;
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, []);

  return {
    activeCount,
    clearTimedOutSegments,
    enqueueSegments,
    error: queueErrorMessage(error),
    isTranslating: activeCount > 0,
    retrySegments,
    reset,
    timedOutSegmentIds,
  };
}
