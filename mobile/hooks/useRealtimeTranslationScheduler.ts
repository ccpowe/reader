import { MAX_TRANSLATION_REQUEST_RECOVERIES, translationRecoveryDelay, translationRequestFailure } from '../domain/translationRequestErrors';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { Session } from '../lib/readerAuth';
import { useTranslation } from '../i18n';

import { recordTranslationDiagnostic } from '../domain/translationDiagnostics';
import { captureRuntimeContext, isRuntimeContextCurrent } from '../lib/connection';
import { useReaderRuntime, useReaderRuntimeGeneration } from '../lib/connection/react';
import { resolveTranslationSegments, type TranslationSegment, type TranslationSegmentResult } from '../lib/api';
import {
  providerTranslationErrorMessage,
  realtimeTranslationErrorMessage,
} from '../domain/realtimeTranslationErrors';

export type RealtimeTranslationBatch = {
  id: string;
  priority: 'urgent' | 'prefetch';
  segments: TranslationSegment[];
  windowId: string;
};

type Options = {
  enabled: boolean;
  engineIdentity?: string | null;
  onResults: (results: TranslationSegmentResult[]) => void;
  session: Session;
  targetLocale: string;
};

type SchedulerError =
  | { kind: 'provider' }
  | { kind: 'transport'; cause: unknown };

const MAX_CONCURRENT = 10;
const POLL_MS = 750;

/** Window-owned scheduler: only queued batches are discarded on navigation. */
export function useRealtimeTranslationScheduler({ enabled, engineIdentity, onResults, session, targetLocale }: Options) {
  useTranslation('errors');
  const enabledRef = useRef(enabled);
  enabledRef.current = enabled;
  const identityRef = useRef(engineIdentity);
  identityRef.current = engineIdentity;
  const runtime = useReaderRuntime();
  const runtimeGeneration = useReaderRuntimeGeneration();
  const batches = useRef(new Map<string, RealtimeTranslationBatch>());
  const batchOwners = useRef(new Map<string, number>());
  const segmentBatch = useRef(new Map<string, string>());
  const sent = useRef(new Map<string, number>());
  const terminalSegments = useRef(new Map<string, {
    source: TranslationSegment;
    result: TranslationSegmentResult;
    receivedAt: number;
  }>());
  const manualRetryAt = useRef(new Map<string, number>());
  const nextAttemptAt = useRef(new Map<string, number>());
  const recoveries = useRef(new Map<string, { attempt: number; notBefore: number }>());
  const controllers = useRef(new Map<number, AbortController>());
  const active = useRef(0);
  const generation = useRef(0);
  const ownerSequence = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const callback = useRef(onResults);
  const pumpRef = useRef<() => void>(() => {});
  const [activeCount, setActiveCount] = useState(0);
  const [failure, setError] = useState<SchedulerError | null>(null);
  const [retryAt, setRetryAt] = useState<number | null>(null);
  callback.current = onResults;

  const pump = useCallback(() => {
    if (!enabledRef.current) return;
    while (active.current < MAX_CONCURRENT) {
      const batch = [...batches.current.values()]
        .filter((candidate) => !sent.current.has(candidate.id) &&
          (nextAttemptAt.current.get(candidate.id) ?? 0) <= Date.now())
        .sort((a, b) => (a.priority === b.priority ? 0 : a.priority === 'urgent' ? -1 : 1))[0];
      if (!batch) break;
      const owner = batchOwners.current.get(batch.id);
      if (owner === undefined) {
        batches.current.delete(batch.id);
        continue;
      }
      sent.current.set(batch.id, owner);
      batch.segments.forEach((segment) => manualRetryAt.current.delete(segment.segment_id));
      setRetryAt((deadline) => deadline !== null && deadline > Date.now() ? deadline : null);
      const controller = new AbortController();
      controllers.current.set(owner, controller);
      active.current += 1;
      setActiveCount(active.current + batches.current.size - sent.current.size);
      const taskGeneration = generation.current;
      const taskIdentity = identityRef.current;
      const releaseSent = () => {
        if (sent.current.get(batch.id) === owner) sent.current.delete(batch.id);
      };
      const releaseBatch = () => {
        if (batchOwners.current.get(batch.id) !== owner) return;
        batches.current.delete(batch.id);
        batchOwners.current.delete(batch.id);
        nextAttemptAt.current.delete(batch.id);
        releaseSent();
        batch.segments.forEach((segment) => {
          if (segmentBatch.current.get(segment.segment_id) === batch.id) {
            segmentBatch.current.delete(segment.segment_id);
          }
        });
      };
      const context = captureRuntimeContext(runtime);
      if (!context) {
        releaseSent();
        controllers.current.delete(owner);
        active.current -= 1;
        setActiveCount(active.current + batches.current.size - sent.current.size);
        break;
      }
      recordTranslationDiagnostic("batch_sent", { stage: "native", batchId: batch.id, count: batch.segments.length });
      void resolveTranslationSegments(session, [...batch.segments], controller.signal, context.runtime)
        .then((rawResults) => {
          if (taskIdentity !== identityRef.current || taskGeneration !== generation.current || !isRuntimeContextCurrent(context) || batchOwners.current.get(batch.id) !== owner) return;
          const results = rawResults.filter(result => batch.segments.some(segment => segment.segment_id === result.segment_id))
            .map(result => result.effective_engine_fingerprint && taskIdentity && result.effective_engine_fingerprint !== taskIdentity
              ? { ...result, translated_text: null, translation_status: 'pending' as const } : result)
            .map(result => result.translation_status === 'succeeded' && !result.cache_expires_at
              ? { ...result, cache_expires_at: new Date(Date.now() + 60 * 60_000).toISOString() } : result)
            .map(result => result.translation_status === 'succeeded' && result.cache_expires_at && Date.parse(result.cache_expires_at) <= Date.now()
              ? { ...result, translated_text: null, translation_status: 'pending' as const } : result)
            .map(result => result.translation_status === 'cancelled' || result.translation_status === null
              ? { ...result, translation_status: 'failed' as const, error_code: result.error_code ?? (result.translation_status === null ? 'translation_disabled' : 'cancelled') } : result);
          const completed = results.filter((result) =>
            result.translation_status === 'succeeded' || result.translation_status === 'failed');
          if (completed.length) {
            completed.forEach((result) => {
              const source = batch.segments.find((segment) => segment.segment_id === result.segment_id);
              recordTranslationDiagnostic('segment_result', { stage: 'native', segmentId: result.segment_id,
                level: result.translation_status === 'succeeded' ? 'debug' : 'info',
                status: result.translation_status, reason: result.error_code, retryAfterMs: result.retry_after_ms });
              if (source) terminalSegments.current.set(result.segment_id, { source, result, receivedAt: Date.now() });
              recoveries.current.delete(result.segment_id);
              if (segmentBatch.current.get(result.segment_id) === batch.id) segmentBatch.current.delete(result.segment_id);
            });
            batch.segments = batch.segments.filter(segment => !terminalSegments.current.has(segment.segment_id));
            callback.current(completed);
          }
          if (results.some((result) => result.translation_status === 'failed')) {
            setError({ kind: 'provider' });
          }
          if (results.some((result) => result.translation_status === 'pending' || result.translation_status === 'running')) {
            // Only unfinished IDs are polled. Success is terminal even if a later poll fails.
            const pending = new Set(results.filter(result => result.translation_status === 'pending' || result.translation_status === 'running').map(result => result.segment_id));
            batch.segments = batch.segments.filter(segment => pending.has(segment.segment_id));
            releaseSent();
            if (batchOwners.current.get(batch.id) === owner) batches.current.set(batch.id, batch);
            const delay = Math.max(POLL_MS, ...results.filter(result => pending.has(result.segment_id)).map((result) => result.retry_after_ms ?? 0));
            const deadline = Date.now() + Math.min(delay, 300_000);
            nextAttemptAt.current.set(batch.id, deadline);
            batch.segments.forEach(segment => recoveries.current.set(segment.segment_id, {
              attempt: recoveries.current.get(segment.segment_id)?.attempt ?? 0, notBefore: deadline,
            }));
          } else {
            releaseBatch();
          }
        })
        .catch((caught: unknown) => {
          if (taskIdentity === identityRef.current && taskGeneration === generation.current && isRuntimeContextCurrent(context) && batchOwners.current.get(batch.id) === owner) {
            const failure = translationRequestFailure(caught);
            const retrying = batch.segments.filter(segment => failure.retryable &&
              (recoveries.current.get(segment.segment_id)?.attempt ?? 0) < MAX_TRANSLATION_REQUEST_RECOVERIES);
            const exhausted = batch.segments.filter(segment => !retrying.includes(segment));
            if (exhausted.length) {
              setError({ kind: 'transport', cause: caught });
              const failed = exhausted.map<TranslationSegmentResult>((segment) => ({
                engine_id: null, engine_label: null, error_code: 'transport_failure', error_retryable: false,
                purpose: segment.purpose, retry_after_ms: failure.retryAfterMs || null,
                segment_id: segment.segment_id, translated_text: null,
                translation_locale: targetLocale, translation_status: 'failed',
              }));
              failed.forEach((result, index) => {
                terminalSegments.current.set(result.segment_id, { source: exhausted[index], result, receivedAt: Date.now() });
                if (segmentBatch.current.get(result.segment_id) === batch.id) segmentBatch.current.delete(result.segment_id);
                recordTranslationDiagnostic(failure.retryable ? 'request_recovery_exhausted' : 'request_not_retried', {
                  stage: 'native', batchId: batch.id, segmentId: result.segment_id,
                  attempt: recoveries.current.get(result.segment_id)?.attempt ?? 0, reason: failure.kind, httpStatus: failure.status,
                });
              });
              callback.current(failed);
            }
            if (retrying.length) {
              let deadline = Date.now();
              retrying.forEach(segment => {
                const attempt = (recoveries.current.get(segment.segment_id)?.attempt ?? 0) + 1;
                const delay = translationRecoveryDelay(attempt, failure.retryAfterMs);
                const notBefore = Date.now() + delay;
                recoveries.current.set(segment.segment_id, { attempt, notBefore });
                deadline = Math.max(deadline, notBefore);
                recordTranslationDiagnostic('request_recovery_scheduled', { stage: 'native', batchId: batch.id,
                  segmentId: segment.segment_id, attempt, reason: failure.kind, httpStatus: failure.status, retryAfterMs: delay });
              });
              batch.segments = retrying;
              nextAttemptAt.current.set(batch.id, deadline);
              releaseSent();
              return;
            }
          }
          releaseBatch();
        })
        .finally(() => {
          if (controllers.current.delete(owner)) active.current -= 1;
          setActiveCount(active.current + batches.current.size - sent.current.size);
          pumpRef.current();
        });
    }
    setActiveCount(active.current + batches.current.size - sent.current.size);
    if (timer.current) clearTimeout(timer.current);
    const waiting = [...batches.current.keys()].filter((id) => !sent.current.has(id))
      .map((id) => nextAttemptAt.current.get(id) ?? 0).filter((at) => at > Date.now());
    if (waiting.length) timer.current = setTimeout(() => pumpRef.current(), Math.min(2_147_483_647, Math.max(1, Math.min(...waiting) - Date.now())));
  }, [runtime, session, targetLocale]);
  pumpRef.current = pump;

  const enqueuePlan = useCallback((plan: RealtimeTranslationBatch[]) => {
    if (!enabled) return;
    const replay = new Map<string, TranslationSegmentResult>();
    for (const requested of plan) {
      for (const segment of requested.segments) {
        const cached = terminalSegments.current.get(segment.segment_id);
        if (!cached) continue;
        if (cached.result.cache_expires_at && Date.parse(cached.result.cache_expires_at) <= Date.now()) {
          terminalSegments.current.delete(segment.segment_id);
          continue;
        }
        if (cached.source.text === segment.text && cached.source.purpose === segment.purpose) {
          recordTranslationDiagnostic('segment_replayed', { stage: 'native', level: 'debug', segmentId: segment.segment_id, status: cached.result.translation_status });
          replay.set(segment.segment_id, cached.result);
        } else {
          terminalSegments.current.delete(segment.segment_id);
        }
      }
      const existingIds = new Set(requested.segments.map((segment) => segmentBatch.current.get(segment.segment_id)).filter(Boolean));
      // A visible urgent segment covered by queued prefetch upgrades that
      // exact batch; no duplicate model call is created.
      for (const id of existingIds) {
        const existing = batches.current.get(id!);
        if (existing && requested.priority === 'urgent' && existing.priority === 'prefetch') {
          // Keep the in-flight request's owner object so scope retirement
          // updates the same segment set even after an urgent upgrade.
          existing.priority = 'urgent';
          existing.windowId = requested.windowId;
        }
      }
      const segments = requested.segments.filter((segment) =>
        !segmentBatch.current.has(segment.segment_id) && !terminalSegments.current.has(segment.segment_id));
      if (!segments.length) continue;
      const batch = { ...requested, segments };
      ownerSequence.current += 1;
      batches.current.set(batch.id, batch);
      const retryDeadline = Math.max(0, ...segments.map((segment) => Math.max(manualRetryAt.current.get(segment.segment_id) ?? 0, recoveries.current.get(segment.segment_id)?.notBefore ?? 0)));
      if (retryDeadline > Date.now()) nextAttemptAt.current.set(batch.id, retryDeadline);
      batchOwners.current.set(batch.id, ownerSequence.current);
      segments.forEach((segment) => segmentBatch.current.set(segment.segment_id, batch.id));
    }
    // A host render may remove the translation while preserving the source
    // identity. Reapply this generation's exact result without a provider call.
    if (replay.size) callback.current([...replay.values()]);
    setError(null);
    pump();
  }, [enabled, pump]);

  const replaceWindow = useCallback((windowId: string) => {
    for (const [id, batch] of batches.current) {
      if (batch.windowId !== windowId && !sent.current.has(id)) {
        batches.current.delete(id);
        batchOwners.current.delete(id);
        nextAttemptAt.current.delete(id);
        batch.segments.forEach((segment) => {
          if (segmentBatch.current.get(segment.segment_id) === id) {
            segmentBatch.current.delete(segment.segment_id);
          }
        });
      }
    }
    setActiveCount(active.current + batches.current.size - sent.current.size);
    pump();
  }, [pump]);

  const forgetSegments = useCallback((segmentIds: readonly string[]) => {
    const removed = new Set(segmentIds);
    for (const id of removed) {
      terminalSegments.current.delete(id);
      manualRetryAt.current.delete(id);
      recoveries.current.delete(id);
      segmentBatch.current.delete(id);
    }
    for (const [id, batch] of batches.current) {
      batch.segments = batch.segments.filter(segment => !removed.has(segment.segment_id));
      if (batch.segments.length) continue;
      const owner = batchOwners.current.get(id);
      if (owner !== undefined && controllers.current.has(owner)) {
        controllers.current.get(owner)!.abort();
        controllers.current.delete(owner);
        active.current -= 1;
      }
      batches.current.delete(id);
      batchOwners.current.delete(id);
      nextAttemptAt.current.delete(id);
      sent.current.delete(id);
    }
    if (![...terminalSegments.current.values()].some(item => item.result.translation_status === 'failed')) setError(null);
    const deadline = Math.max(0, ...manualRetryAt.current.values());
    setRetryAt(deadline > Date.now() ? deadline : null);
    setActiveCount(active.current + batches.current.size - sent.current.size);
    pumpRef.current();
  }, []);

  const reset = useCallback(() => {
    generation.current += 1;
    controllers.current.forEach(controller => controller.abort());
    controllers.current.clear();
    active.current = 0;
    recoveries.current.clear();
    batches.current.clear();
    batchOwners.current.clear();
    segmentBatch.current.clear();
    sent.current.clear();
    terminalSegments.current.clear();
    nextAttemptAt.current.clear();
    manualRetryAt.current.clear();
    setRetryAt(null);
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
    setActiveCount(active.current);
    setError(null);
  }, []);

  const retryFailed = useCallback(() => {
    for (const [id, cached] of terminalSegments.current) {
      if (cached.result.translation_status === 'failed') {
        recoveries.current.delete(id);
        const delay = cached.result.retry_after_ms;
        const deadline = cached.receivedAt + (typeof delay === 'number' && Number.isFinite(delay) ? Math.max(0, delay) : 0);
        manualRetryAt.current.set(id, deadline);
        terminalSegments.current.delete(id);
      }
    }
    const deadline = Math.max(0, ...manualRetryAt.current.values());
    setRetryAt(deadline > Date.now() ? deadline : null);
    setError(null);
  }, []);

  const rejectResult = useCallback((segmentId: string) => {
    terminalSegments.current.delete(segmentId);
  }, []);

  useEffect(() => {
    if (enabled) pump();
    else if (timer.current) { clearTimeout(timer.current); timer.current = null; }
  }, [enabled, pump]);
  useEffect(() => { reset(); return reset; }, [reset, runtimeGeneration, targetLocale, engineIdentity, session.user?.id]);
  useEffect(() => {
    const expiryTimer = setInterval(() => {
      for (const [id, cached] of terminalSegments.current) {
        if (cached.result.cache_expires_at && Date.parse(cached.result.cache_expires_at) <= Date.now()) {
          terminalSegments.current.delete(id);
        }
      }
    }, 60_000);
    return () => clearInterval(expiryTimer);
  }, []);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const error = failure?.kind === 'provider'
    ? providerTranslationErrorMessage()
    : failure?.kind === 'transport' ? realtimeTranslationErrorMessage(failure.cause) : null;
  return { activeCount, enqueuePlan, error, replaceWindow, forgetSegments, reset, retryFailed, rejectResult, retryAt };
}
