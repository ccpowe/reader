import { i18n } from '../i18n';
export const SEGMENT_CONVERGENCE_TIMEOUT_MS = 45_000;

export function segmentConvergenceTimeoutMessage(): string {
  return i18n.t('errors:segmentConvergenceTimeout');
}

/** Title convergence uses the same one-shot, bounded polling contract. */
export const TITLE_CONVERGENCE_TIMEOUT_MS = SEGMENT_CONVERGENCE_TIMEOUT_MS;
export function titleConvergenceTimeoutMessage(): string {
  return i18n.t('errors:titleConvergenceTimeout');
}

export type TranslationConvergenceDecision = 'retry' | 'settled' | 'timed_out';

/**
 * Bounds polling for backend work that remains pending or running.
 * A fresh enqueue must provide a fresh `startedAt`, so a user retry receives
 * the full convergence window instead of inheriting the previous timeout.
 */
export function translationConvergenceDecision(
  status: string | null,
  startedAt: number,
  now: number,
  timeoutMs = SEGMENT_CONVERGENCE_TIMEOUT_MS,
): TranslationConvergenceDecision {
  if (status !== 'pending' && status !== 'running') return 'settled';
  return now - startedAt >= timeoutMs ? 'timed_out' : 'retry';
}

export function nextTranslationConvergencePollAt(
  startedAt: number,
  now: number,
  requestedDelayMs: number,
  timeoutMs = SEGMENT_CONVERGENCE_TIMEOUT_MS,
): number {
  return Math.min(now + Math.max(requestedDelayMs, 0), startedAt + timeoutMs);
}

export const titleConvergenceDecision = translationConvergenceDecision;
export const nextTitleConvergencePollAt = nextTranslationConvergencePollAt;
