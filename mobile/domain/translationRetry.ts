import { HttpResponseError } from '../lib/http';

const BASE_TRANSPORT_RETRY_MS = 1_500;
const MAX_TRANSPORT_RETRY_MS = 30_000;
const MAX_TRANSPORT_FAILURES = 5;

export type TranslationTransportRetryDecision = {
  delayMs: number | null;
  failureCount: number;
  retry: boolean;
};

/** Bound transient translation transport failures without hammering a dead gateway. */
export function translationTransportRetryDecision(
  cause: unknown,
  previousFailureCount: number,
): TranslationTransportRetryDecision {
  const failureCount = previousFailureCount + 1;
  const retryable = cause instanceof HttpResponseError ? cause.retryable : true;
  if (!retryable || failureCount >= MAX_TRANSPORT_FAILURES) {
    return { delayMs: null, failureCount, retry: false };
  }
  const exponentialDelay = Math.min(
    BASE_TRANSPORT_RETRY_MS * (2 ** Math.max(failureCount - 1, 0)),
    MAX_TRANSPORT_RETRY_MS,
  );
  const requestedDelay = cause instanceof HttpResponseError
    ? Math.min(cause.retryAfterMs ?? 0, MAX_TRANSPORT_RETRY_MS)
    : 0;
  return {
    delayMs: Math.max(exponentialDelay, requestedDelay),
    failureCount,
    retry: true,
  };
}
