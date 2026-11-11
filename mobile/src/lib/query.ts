/**
 * TanStack Query defaults for Reader (react-guidelines §4) and the query-key helper.
 *
 * Every key starts with the session identity (server + account), so one account's data is never
 * shown to another; the session owner removes an identity's queries when it ends.
 */
import { QueryClient } from '@tanstack/react-query';

import { isApiError, isRetryable } from './api/errors';

const MAX_RETRIES = 2;

export function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        // Network, timeout and 5xx are retried with backoff; 4xx (401, 404, 429 …) are answers.
        retry: (failures, error) => failures < MAX_RETRIES && isRetryable(error),
        retryDelay: (attempt) => Math.min(1_000 * 2 ** attempt, 8_000),
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        refetchOnReconnect: true,
        refetchOnWindowFocus: true,
        // A cancelled request is not an error to show.
        throwOnError: false,
      },
      mutations: { retry: false },
    },
  });
}

/** `key(identity, 'feed', params)` → `[identity, 'feed', params]`. */
export function key<const T extends readonly unknown[]>(identity: string, ...parts: T) {
  return [identity, ...parts] as const;
}

/** True for a request its owner cancelled (page left, parameters changed). */
export function isAborted(error: unknown) {
  return isApiError(error) && error.problem.kind === 'aborted';
}
