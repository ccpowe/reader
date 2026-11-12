/**
 * One feed (全部, a folder, or one subscription) as a list of cards: loading, paging, pull to
 * refresh, saving from a card, and where it was left. Used by 今日 and by a subscription's page.
 */
import { useInfiniteQuery } from '@tanstack/react-query';
import { router } from 'expo-router';
import { useCallback, useMemo, useState } from 'react';

import type { FeedCardItem } from '@/components/FeedCard';
import { FeedList, type FeedListState } from '@/components/FeedList';
import type { FeedItem } from '@/lib/api/schema.gen';
import { queryClient, useSession } from '@/lib/app-session';
import { absoluteUrl, toCard, type FeedScope } from '@/lib/feed';
import { createListMemory } from '@/lib/list-memory';
import { subscriptionsQuery } from '@/lib/queries/subscriptions';
import { feedQuery, setSaved, UnknownOutcome } from '@/lib/queries/today';
import { isAborted } from '@/lib/query';
import { errorMessage } from '@/lib/user-error';
import { EmptyState } from '@/ui';

function useApiBase(): string {
  const state = useSession();
  return state.status === 'signed-in' ? state.server.apiBaseUrl : '';
}

/** Where each feed was left, per identity and scope, for this app run (old app: no jump back to top). */
const positions = createListMemory();

/** Cards are rebuilt only for changed items: same API object and same minute → same card object. */
const cardCache = new WeakMap<FeedItem, { minute: number; card: FeedCardItem }>();
function cardsOf(items: FeedItem[], now: number, absolute: (url: string | null | undefined) => string | null): FeedCardItem[] {
  const minute = Math.floor(now / 60_000);
  return items.map((item) => {
    const hit = cardCache.get(item);
    if (hit && hit.minute === minute) return hit.card;
    const card = toCard(item, now, absolute);
    cardCache.set(item, { minute, card });
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
  const query = useInfiniteQuery({ ...feedQuery(identity, scope), subscribed: active });
  const [refreshing, setRefreshing] = useState(false);
  const offsetKey = `${identity}|${JSON.stringify(scope)}`;

  // Times are relative to when this feed was loaded (render stays pure; a refresh updates them).
  const items = useMemo(
    () => cardsOf(query.data?.pages.flatMap((page) => page.items) ?? [], query.dataUpdatedAt, absoluteUrl(apiBase)),
    [query.data, query.dataUpdatedAt, apiBase],
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
        if (error instanceof UnknownOutcome) onNotice('没收到服务器的确认，已重新加载收藏状态。');
        else onNotice(item.isSaved ? `取消收藏没有成功：${errorMessage(error)}` : `收藏没有成功：${errorMessage(error)}`);
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
      refreshFailed: query.isRefetchError && !refreshing ? '刷新没有成功，显示的是之前的内容。' : null,
      hasMore: query.hasNextPage,
      loadingMore: query.isFetchingNextPage,
      moreFailed: query.isFetchNextPageError ? `更早的内容没加载出来：${errorMessage(query.error)}` : null,
      busy: query.isFetching,
    };
  }

  return (
    <FeedList
      empty={
        <EmptyState
          description={scope.kind === 'home' ? '订阅有新内容时会出现在这里。' : '这里还没有内容。'}
          title="还没有内容"
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
