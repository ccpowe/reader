/**
 * One feed (全部, a folder, or one subscription) as a list of cards: loading, paging, pull to
 * refresh, saving from a card, and where it was left. Used by 今日 and by a subscription's page.
 */
import { useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { router } from 'expo-router';
import { useCallback, useMemo, useState } from 'react';

import type { FeedCardItem } from '@/components/FeedCard';
import { FeedList, type FeedListState } from '@/components/FeedList';
import type { FeedItem } from '@/lib/api/schema.gen';
import { queryClient, useSession } from '@/lib/app-session';
import { absoluteUrl, missingTitles, toCard, withTitleTranslations, type FeedScope } from '@/lib/feed';
import { createListMemory } from '@/lib/list-memory';
import { clearCountOnLoad, subscriptionsQuery } from '@/lib/queries/subscriptions';
import { useMountEffect } from '@/lib/use-mount-effect';
import { feedQuery, setSaved, UnknownOutcome } from '@/lib/queries/today';
import { titlesFailed, titleTranslationsQuery, translationPreferenceQuery } from '@/lib/queries/translation';
import { isAborted } from '@/lib/query';
import { errorMessage } from '@/lib/user-error';
import { EmptyState } from '@/ui';
import { t } from '@/lib/i18n';
import { useLanguage } from '@/lib/interface-language';
import type { Language } from '@/lib/i18n';

function useApiBase(): string {
  const state = useSession();
  return state.status === 'signed-in' ? state.server.apiBaseUrl : '';
}

/** Where each feed was left, per identity and scope, for this app run (old app: no jump back to top). */
const positions = createListMemory();

/** Cards are rebuilt only for changed items: same API object and same minute → same card object. */
const cardCache = new WeakMap<FeedItem, { minute: number; lang: Language; card: FeedCardItem }>();
function cardsOf(items: FeedItem[], now: number, absolute: (url: string | null | undefined) => string | null, lang: Language): FeedCardItem[] {
  const minute = Math.floor(now / 60_000);
  return items.map((item) => {
    const hit = cardCache.get(item);
    if (hit && hit.minute === minute && hit.lang === lang) return hit.card;
    const card = toCard(item, now, absolute);
    cardCache.set(item, { minute, lang, card });
    return card;
  });
}

export function FeedPage({
  identity,
  scope,
  active,
  onNotice,
}: {
  identity: string;
  scope: FeedScope;
  /** the page on screen, on a focused tab: only then does it follow the server */
  active: boolean;
  onNotice: (message: string) => void;
}) {
  const apiBase = useApiBase();
  const lang = useLanguage(); // card texts (times, labels) are in the interface language
  const query = useInfiniteQuery({ ...feedQuery(identity, scope), subscribed: active });
  const [refreshing, setRefreshing] = useState(false);
  const offsetKey = `${identity}|${JSON.stringify(scope)}`;
  // One subscription shown: its count clears when its feed has loaded (not before, old app).
  useMountEffect(() => (scope.kind === 'subscription' ? clearCountOnLoad(identity, scope.id) : () => undefined));

  // Titles the server had no translation for yet are asked for here, then shown in place.
  const loaded = useMemo(() => query.data?.pages.flatMap((page) => page.items) ?? [], [query.data]);
  const missing = useMemo(() => missingTitles(loaded), [loaded]);
  const preference = useQuery(translationPreferenceQuery(identity));
  const titles = useQuery({ ...titleTranslationsQuery(identity, ['feed', scope], missing, preference.data), subscribed: active });
  // Only while titles are translated: switching it off shows the originals at once.
  const filled = preference.data?.enabled ? titles.data : undefined;

  // Times are relative to when this feed was loaded (render stays pure; a refresh updates them).
  const items = useMemo(
    () => cardsOf(withTitleTranslations(loaded, filled?.done, filled?.fingerprint ?? ''), query.dataUpdatedAt, absoluteUrl(apiBase), lang),
    [loaded, filled, query.dataUpdatedAt, apiBase, lang],
  );

  async function refresh() {
    setRefreshing(true);
    try {
      // The new-content counts (filter badge, picker) come with the subscriptions.
      await Promise.all([query.refetch(), queryClient.invalidateQueries({ queryKey: subscriptionsQuery(identity).queryKey })]);
    } finally {
      setRefreshing(false);
    }
  }

  // Stable references for the memoized cards (react-guidelines §6).
  const open = useCallback((id: string) => router.push({ pathname: '/article/[id]', params: { id } }), []);
  const toggleSave = useCallback(
    async (item: FeedCardItem) => {
      try {
        await setSaved(identity, item.id, !item.isSaved);
      } catch (error) {
        if (error instanceof UnknownOutcome) onNotice(t().common.bookmarkUnknown);
        else onNotice(t().common.bookmarkFailed(item.isSaved, errorMessage(error)));
      }
    },
    [identity, onNotice],
  );
  const onToggleSave = useCallback((item: FeedCardItem) => void toggleSave(item), [toggleSave]);

  let state: FeedListState;
  if (query.data === undefined) {
    state =
      query.isError && !isAborted(query.error) ? { kind: 'failed', message: errorMessage(query.error) } : { kind: 'loading' };
  } else {
    state = {
      kind: 'ready',
      items,
      refreshFailed: query.isRefetchError && !refreshing ? t().common.refreshFailed : null,
      hasMore: query.hasNextPage,
      loadingMore: query.isFetchingNextPage,
      moreFailed: query.isFetchNextPageError ? t().feed.moreFailed(errorMessage(query.error)) : null,
      titlesFailed: titlesFailed(titles, preference.data),
      busy: query.isFetching,
    };
  }

  return (
    <FeedList
      active={active}
      empty={
        <EmptyState
          description={scope.kind === 'home' ? t().feed.emptyHome : t().feed.emptyOther}
          title={t().feed.emptyTitle}
        />
      }
      position={positions.get(offsetKey)}
      onEndReached={() => void query.fetchNextPage({ cancelRefetch: false })}
      onPosition={(position) => positions.remember(offsetKey, position)}
      onOpen={open}
      onRefresh={() => void refresh()}
      onRetry={() => void query.refetch()}
      onToggleSave={onToggleSave}
      refreshing={refreshing}
      state={state}
    />
  );
}
