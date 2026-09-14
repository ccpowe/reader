import { bindLocalizedErrorMessage, resolveMessage, type LocalizedMessage } from '../i18n/message';

/** Transport recovery is separate from backend model attempts and pending polls. */
export type TranslationRequestErrorKind = 'network' | 'timeout' | 'abort' | 'http' | 'parse' | 'contract' | 'unknown';
export class TranslationRequestError extends Error {
  constructor(
    message: string | LocalizedMessage,
    readonly kind: TranslationRequestErrorKind,
    readonly status?: number,
    readonly retryAfterMs?: number,
  ) {
    super(resolveMessage(message));
    bindLocalizedErrorMessage(this, message);
    this.name = 'TranslationRequestError';
  }
}

export function translationRequestFailure(error: unknown) {
  const value = error instanceof Error ? error as Error & {
    kind?: TranslationRequestErrorKind; status?: number; retryAfterMs?: number; code?: string;
  } : undefined;
  const kind: TranslationRequestErrorKind = value?.kind ??
    (value?.code === 'stale_runtime' || value?.code === 'discovery_cancelled' || value?.name === 'AbortError' ? 'abort' :
      value?.name === 'TimeoutError' ? 'timeout' :
        typeof value?.status === 'number' ? 'http' : error instanceof TypeError ? 'network' : 'unknown');
  const status = typeof value?.status === 'number' ? value.status : undefined;
  const retryAfterMs = typeof value?.retryAfterMs === 'number' && Number.isFinite(value.retryAfterMs)
    ? Math.max(0, value.retryAfterMs) : 0;
  return { kind, status, retryAfterMs,
    retryable: kind === 'network' || kind === 'timeout' ||
      (kind === 'http' && status !== undefined && [408, 429, 500, 502, 503, 504].includes(status)),
  };
}

export const MAX_TRANSLATION_REQUEST_RECOVERIES = 2;
export function translationRecoveryDelay(attempt: number, retryAfterMs: number, random = Math.random()): number {
  return Math.max(retryAfterMs, 1_000 * 2 ** (attempt - 1) + Math.floor(Math.max(0, Math.min(1, random)) * 250));
}
