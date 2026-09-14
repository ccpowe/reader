import { useTranslation } from '../i18n';
import { useQuery, queryOptions } from '@tanstack/react-query';
import type { Session } from '../lib/readerAuth';

import {
  DEFAULT_TRANSLATION_LOCALE,
  getTranslationPreference,
  type TranslationPreference,
} from '../lib/api';
import { readerQueryKeys } from '../state/queryClient';
import { useReaderRuntime } from '../lib/connection/react';
import type { ActiveReaderRuntime } from '../lib/connection';

export function translationPreferenceQueryOptions(session: Session, serverId?: string, runtime?: ActiveReaderRuntime | null) {
  return queryOptions<TranslationPreference, Error>({
    queryKey: readerQueryKeys.translationPreference(session.user.id, serverId),
    queryFn: () => getTranslationPreference(session, runtime ?? undefined),
    staleTime: 5 * 60_000,
  });
}

export function useTranslationPreference(session: Session, active = true) {
  const { t } = useTranslation('errors');
  const runtime = useReaderRuntime();
  const query = useQuery({
    ...translationPreferenceQueryOptions(session, runtime?.identity.server_id, runtime),
    enabled: active,
  });
  return {
    ...query,
    targetLocale: query.data?.target_locale ?? DEFAULT_TRANSLATION_LOCALE,
    effectiveEngineFingerprint: query.data?.effective_engine_fingerprint ?? null,
    effectiveEngineId: query.data?.effective_engine_id ?? null,
    engineLabel: query.data?.effective_engine_label ?? t('translationEngine'),
    engineAvailable: query.data?.effective_engine_available ?? false,
    // Do not spend translation quota or process private WebView text until the
    // authenticated preference has actually been loaded.
    enabled: query.isSuccess && query.data.is_enabled,
  };
}
