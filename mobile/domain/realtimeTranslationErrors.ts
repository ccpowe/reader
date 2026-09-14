import { i18n } from '../i18n';
import { translationRequestFailure } from './translationRequestErrors';
type HttpLikeError = Error & { status?: unknown };

/** Keep transport/storage failures distinct from a provider result failure. */
export function realtimeTranslationErrorMessage(error: unknown): string {
  const status = error instanceof Error
    ? (error as HttpLikeError).status
    : undefined;
  if (status === 503) return i18n.t('errors:translationBusy');
  if (status === 429) return i18n.t('errors:translationRateLimited');
  if (
    ['network', 'timeout'].includes(translationRequestFailure(error).kind) ||
    error instanceof TypeError ||
    (error instanceof Error && ['AbortError', 'TimeoutError'].includes(error.name))
  ) {
    return i18n.t('errors:translationNetworkInterrupted');
  }
  return i18n.t('errors:translationRequestFailed');
}

export function providerTranslationErrorMessage(): string {
  return i18n.t('errors:providerTranslationFailed');
}
