import { useTranslation } from '../i18n';
import { useMemo } from 'react';
import { Alert } from 'react-native';
import type { Session } from '../lib/readerAuth';
import {
  useInfiniteQuery,
  useQueryClient,
} from '@tanstack/react-query';

import {
  getSavedContentPage,
  setSavedContent,
  type FeedItem,
} from '../lib/api';
import { invalidateAfterSavedMutation } from '../state/invalidation';
import {
  removeCachedSavedItem,
  restoreSavedCache,
  setCachedFeedSavedState,
} from '../state/cacheUpdates';
import { beginSavedMutation } from '../state/savedMutation';
import { readerQueryKeys } from '../state/queryClient';
import { useSources } from './useSources';
import { useTranslationPreference } from './useTranslationPreference';
import { useTitleTranslationConvergence } from './useTitleTranslationConvergence';
import { useReaderRuntime } from '../lib/connection/react';
import { captureRuntimeContext, isRuntimeContextCurrent } from '../lib/connection';

export function useSavedContent(session: Session, active: boolean, query = '') {
  const { t } = useTranslation('errors');
  const client = useQueryClient();
  const runtime = useReaderRuntime();
  const sourcesQuery = useSources(session, active);
  const translationPreference = useTranslationPreference(session, active);
  const queryKey = useMemo(
    () => readerQueryKeys.saved(session.user.id, translationPreference.targetLocale, query, runtime?.identity.server_id),
    [query, runtime?.identity.server_id, session.user.id, translationPreference.targetLocale],
  );
  const savedQuery = useInfiniteQuery({
    queryKey,
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) => getSavedContentPage(session, { cursor: pageParam, query }, runtime ?? undefined),
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
    enabled: active && (translationPreference.isSuccess || translationPreference.isError),
    gcTime: Infinity,
  });
  const items = useMemo(() => {
    const seen = new Set<string>();
    return (savedQuery.data?.pages ?? [])
      .flatMap((page) => page.items)
      .filter((item) => {
        if (seen.has(item.content_id)) return false;
        seen.add(item.content_id);
        return true;
      });
  }, [savedQuery.data]);
  const titleTranslation = useTitleTranslationConvergence(
    session,
    items,
    active && translationPreference.enabled,
    translationPreference.targetLocale,
    translationPreference.effectiveEngineId,
    'saved',
  );
  const message = savedQuery.error
    ? savedQuery.error instanceof Error
      ? savedQuery.error.message
      : t('savedLoadFailed')
    : '';

  async function removeSaved(item: FeedItem) {
    const context = captureRuntimeContext(runtime);
    if (!context) return;
    const release = beginSavedMutation(client, session.user.id, context.serverId, item.content_id);
    if (!release) return;
    let committed = false;
    const previous = removeCachedSavedItem(client, session.user.id, context.serverId, item.content_id, translationPreference.targetLocale, context);
    setCachedFeedSavedState(client, session.user.id, context.serverId, item.content_id, false, context);
    try {
      await setSavedContent(session, item.content_id, false, context.runtime);
      committed = true;
      if (!isRuntimeContextCurrent(context)) return;
      await invalidateAfterSavedMutation(client, session.user.id, context.serverId, context);
    }
    catch (error) {
      if (!isRuntimeContextCurrent(context)) return;
      if (committed) return; // Refetch failure cannot undo a successful server write.
      restoreSavedCache(client, session.user.id, context.serverId, previous, translationPreference.targetLocale, context);
      setCachedFeedSavedState(client, session.user.id, context.serverId, item.content_id, true, context);
      Alert.alert(t('unsaveFailed'), error instanceof Error ? error.message : t('tryAgainLater'));
    } finally {
      release();
    }
  }
  return { items, loading: savedQuery.isLoading, message, queryKey, removeSaved, savedQuery, sources: sourcesQuery.items, titleTranslation };
}
