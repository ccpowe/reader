import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Session } from '../lib/readerAuth';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useTranslation } from '../i18n';

import {
  resolveTitleTranslations,
  type TitleTranslation,
  type TranslationStatus,
} from '../lib/api';
import { mergeCachedTitleTranslations } from '../state/cacheUpdates';
import { readerQueryKeys } from '../state/queryClient';
import { useReaderRuntime, useReaderRuntimeGeneration } from '../lib/connection/react';
import { captureRuntimeContext, isRuntimeContextCurrent } from '../lib/connection';
import {
  TITLE_CONVERGENCE_TIMEOUT_MS,
  titleConvergenceTimeoutMessage,
  nextTitleConvergencePollAt,
  titleConvergenceDecision,
} from '../domain/translationConvergence';

const CONVERGENCE_INTERVAL_MS = 750;
const MAX_RETRY_AFTER_MS = 30_000;
const MAX_TITLES_PER_REQUEST = 100;

type TranslatableTitle = {
  content_id: string;
  translation_status: TranslationStatus;
};

type TitleConvergenceEntry = {
  startedAt: number;
  timedOut: boolean;
};

export type TitleTranslationConvergenceResult = TitleTranslation[] & {
  /** Content ids whose one-shot convergence window expired. */
  timedOutContentIds: string[];
  timeoutMessage: string;
  /** Explicitly reopen a timed-out window and issue one immediate status query. */
  retryTimedOut: (contentIds?: string[]) => Promise<void>;
};

/**
 * A module-level registry makes the first pending timestamp independent from
 * React Query observer lifetimes. A feed can unmount/re-enter or change its
 * query object without silently receiving another 45-second window.
 */
const convergenceRegistry = new Map<string, TitleConvergenceEntry>();

function isInFlight(status: TranslationStatus): boolean {
  return status === 'pending' || status === 'running';
}

function convergenceKey(
  userId: string,
  serverId: string | undefined,
  runtimeGeneration: number,
  locale: string,
  engineId: string | null,
  scopeKey: string,
  contentId: string,
): string {
  return [
    userId,
    serverId ?? 'unknown-server',
    runtimeGeneration,
    locale,
    engineId ?? 'default-engine',
    scopeKey,
    contentId,
  ].join('\u0000');
}

function ensureEntry(key: string): TitleConvergenceEntry {
  const existing = convergenceRegistry.get(key);
  if (existing) return existing;
  const entry = { startedAt: Date.now(), timedOut: false };
  convergenceRegistry.set(key, entry);
  return entry;
}

/** Test/support hook; production callers should use the hook return value. */
export function clearTitleConvergenceRegistry(): void {
  convergenceRegistry.clear();
}

/** Poll loaded title deltas within one fixed, per-scope/content deadline. */
export function useTitleTranslationConvergence(
  session: Session,
  items: TranslatableTitle[],
  active: boolean,
  translationLocale: string,
  engineId: string | null = null,
  scopeKey = 'default',
): TitleTranslationConvergenceResult {
  useTranslation('errors');
  const queryClient = useQueryClient();
  const runtime = useReaderRuntime();
  const runtimeGeneration = useReaderRuntimeGeneration();
  const [clock, bumpClock] = useState(0);
  const rawPendingIds = useMemo(
    () => [...new Set(
      items
        .filter((item) => isInFlight(item.translation_status))
        .map((item) => item.content_id),
    )].sort().slice(0, MAX_TITLES_PER_REQUEST),
    [items],
  );
  const keyFor = useCallback(
    (contentId: string) => convergenceKey(
      session.user.id,
      runtime?.identity.server_id,
      runtimeGeneration,
      translationLocale,
      engineId,
      scopeKey,
      contentId,
    ),
    [engineId, runtime?.identity.server_id, runtimeGeneration, scopeKey, session.user.id, translationLocale],
  );

  // Register only currently pending work. A terminal server state retires the
  // old entry; a subsequent pending state then represents a new demand.
  const pendingIds = useMemo(
    () => rawPendingIds.filter((contentId) => {
      const entry = ensureEntry(keyFor(contentId));
      return !entry.timedOut;
    }),
    // clock deliberately invalidates reads from the module-level registry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [clock, keyFor, rawPendingIds],
  );

  const timedOutContentIds = useMemo(
    () => rawPendingIds.filter((contentId) => convergenceRegistry.get(keyFor(contentId))?.timedOut),
    // clock deliberately invalidates reads from the module-level registry.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [clock, keyFor, rawPendingIds],
  );

  useEffect(() => {
    // Expire a demand even if a query observer is idle between polls.
    for (const contentId of rawPendingIds) {
      const key = keyFor(contentId);
      const entry = convergenceRegistry.get(key);
      if (!entry) continue;
      if (titleConvergenceDecision('pending', entry.startedAt, Date.now()) === 'timed_out') {
        if (!entry.timedOut) {
          entry.timedOut = true;
          bumpClock((value) => value + 1);
        }
      }
    }
    for (const item of items) {
      if (isInFlight(item.translation_status)) continue;
      convergenceRegistry.delete(keyFor(item.content_id));
    }
  }, [items, keyFor, rawPendingIds]);

  useEffect(() => {
    const timers: ReturnType<typeof setTimeout>[] = [];
    for (const contentId of rawPendingIds) {
      const key = keyFor(contentId);
      const entry = convergenceRegistry.get(key);
      if (!entry || entry.timedOut) continue;
      const remaining = Math.max(entry.startedAt + TITLE_CONVERGENCE_TIMEOUT_MS - Date.now(), 0);
      const timer = setTimeout(() => {
        const current = convergenceRegistry.get(key);
        if (!current || current.timedOut) return;
        current.timedOut = true;
        bumpClock((value) => value + 1);
      }, remaining);
      timers.push(timer);
    }
    return () => timers.forEach((timer) => clearTimeout(timer));
  }, [clock, keyFor, rawPendingIds]);

  const query = useQuery({
    queryKey: readerQueryKeys.titleTranslations(
      session.user.id,
      translationLocale,
      engineId,
      pendingIds,
      runtime?.identity.server_id,
    ),
    queryFn: () => resolveTitleTranslations(session, pendingIds, runtime ?? undefined),
    enabled: active && pendingIds.length > 0,
    gcTime: 30_000,
    retry: false,
    staleTime: 0,
    refetchInterval: (state) => {
      if (state.state.error || !pendingIds.length) return false;
      const now = Date.now();
      const activeTranslations = (state.state.data ?? []).filter((item) => {
        const entry = convergenceRegistry.get(keyFor(item.content_id));
        return isInFlight(item.translation_status) && entry && !entry.timedOut;
      });
      const candidateIds = activeTranslations.length
        ? activeTranslations.map((item) => item.content_id)
        : pendingIds;
      const next = Math.min(...candidateIds.map((contentId) => {
        const entry = convergenceRegistry.get(keyFor(contentId));
        if (!entry) return now + CONVERGENCE_INTERVAL_MS;
        const requestedDelay = activeTranslations.find((item) => item.content_id === contentId)?.retry_after_ms
          ?? CONVERGENCE_INTERVAL_MS;
        return nextTitleConvergencePollAt(
          entry.startedAt,
          now,
          Math.max(CONVERGENCE_INTERVAL_MS, Math.min(requestedDelay, MAX_RETRY_AFTER_MS)),
          TITLE_CONVERGENCE_TIMEOUT_MS,
        );
      }));
      const delay = next - now;
      return delay <= 0 ? false : delay;
    },
  });

  const retryTimedOut = useCallback(async (requestedContentIds?: string[]) => {
    const context = captureRuntimeContext(runtime);
    if (!context) return;
    const requested = requestedContentIds ?? timedOutContentIds;
    const retryIds = [...new Set(requested)].filter((contentId) => (
      rawPendingIds.includes(contentId) &&
      convergenceRegistry.get(keyFor(contentId))?.timedOut === true
    )).sort();
    if (!retryIds.length) return;

    const now = Date.now();
    for (const contentId of retryIds) {
      const entry = convergenceRegistry.get(keyFor(contentId));
      if (entry) {
        entry.startedAt = now;
        entry.timedOut = false;
      }
    }
    // Keep the query key identical to the observer key after the state update.
    // React Query then de-duplicates this immediate request with the observer's
    // own fetch instead of issuing a second provider call.
    const nextPendingIds = rawPendingIds.filter((contentId) => {
      const entry = convergenceRegistry.get(keyFor(contentId));
      return Boolean(entry && !entry.timedOut);
    });
    bumpClock((value) => value + 1);
    try {
      const results = typeof queryClient.fetchQuery === 'function'
        ? await queryClient.fetchQuery<TitleTranslation[]>({
            queryKey: readerQueryKeys.titleTranslations(
              session.user.id,
              translationLocale,
              engineId,
              nextPendingIds,
              context.serverId,
            ),
            queryFn: () => resolveTitleTranslations(session, nextPendingIds, context.runtime),
            gcTime: 30_000,
            retry: false,
            staleTime: 0,
          })
        : await resolveTitleTranslations(session, nextPendingIds, context.runtime);
      if (!isRuntimeContextCurrent(context)) return;
      const fresh = results.filter((item) => {
        const entry = convergenceRegistry.get(keyFor(item.content_id));
        return Boolean(entry && !entry.timedOut && Date.now() < entry.startedAt + TITLE_CONVERGENCE_TIMEOUT_MS);
      });
      if (fresh.length) {
        mergeCachedTitleTranslations(
          queryClient,
          session.user.id,
          context.serverId,
          translationLocale,
          fresh,
          context,
        );
      }
    } catch {
      // The observer remains enabled for the reopened ids and owns the normal
      // error/polling path. Explicit retry itself must not leak stale errors.
    }
  }, [engineId, keyFor, queryClient, rawPendingIds, runtime, session, timedOutContentIds, translationLocale]);

  useEffect(() => {
    const context = captureRuntimeContext(runtime);
    if (!query.data || !context || !isRuntimeContextCurrent(context)) return;
    const now = Date.now();
    const fresh = query.data.filter((item) => {
      const entry = convergenceRegistry.get(keyFor(item.content_id));
      return Boolean(entry && !entry.timedOut && now < entry.startedAt + TITLE_CONVERGENCE_TIMEOUT_MS);
    });
    if (!fresh.length) return;
    mergeCachedTitleTranslations(
      queryClient,
      session.user.id,
      context.serverId,
      translationLocale,
      fresh,
      context,
    );
  }, [keyFor, query.data, queryClient, runtime, runtimeGeneration, session.user.id, translationLocale]);

  // Consumers can use this localized message for an accessible explicit retry
  // action without inventing a second timeout contract.
  return Object.assign([...(query.data ?? [])], {
    retryTimedOut,
    timedOutContentIds,
    timeoutMessage: titleConvergenceTimeoutMessage(),
  }) as TitleTranslationConvergenceResult;
}
