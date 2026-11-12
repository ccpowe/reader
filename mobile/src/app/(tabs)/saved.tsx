/**
 * 收藏 — assembly only (react-guidelines §6): which folder and search are shown, the saved list
 * for them, and unsaving with Undo (the item leaves the list at once, PRODUCT §3.4).
 * Items saved from a board name the board and are only under 全部.
 */
import { onlineManager, useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { router, useFocusEffect, useIsFocused } from 'expo-router';
import { useCallback, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import Animated from 'react-native-reanimated';

import type { FeedCardItem } from '@/components/FeedCard';
import { FeedList, type FeedListState } from '@/components/FeedList';
import { useIdentity, useSession } from '@/lib/app-session';
import { absoluteUrl, folderLabel, folderList } from '@/lib/feed';
import { createListMemory } from '@/lib/list-memory';
import { hiddenSaved, savedQuery, unsaveLater } from '@/lib/queries/saved';
import { subscriptionsQuery } from '@/lib/queries/subscriptions';
import { meQuery, UnknownOutcome } from '@/lib/queries/today';
import { isAborted } from '@/lib/query';
import { savedCard, type SavedScope } from '@/lib/saved';
import { errorMessage } from '@/lib/user-error';
import { SavedView } from '@/screens/SavedView';
import { Button, EmptyState, Toast } from '@/ui';
import { duration } from '@/ui/theme';

const toastLife = {
  animationName: { '0%': { opacity: 0 }, '4%': { opacity: 1 }, '94%': { opacity: 1 }, '100%': { opacity: 0 } },
  animationDuration: `${duration.toast}ms`,
  animationFillMode: 'both',
} as const;

/** Typing pauses this long before the server is asked. */
const SEARCH_DELAY = 300;

/** Where each saved list was left, for this app run. */
const positions = createListMemory();

function useApiBase(): string {
  const state = useSession();
  return state.status === 'signed-in' ? state.server.apiBaseUrl : '';
}

function useOnline(): boolean {
  return useSyncExternalStore(onlineManager.subscribe.bind(onlineManager), () => onlineManager.isOnline());
}

function SavedList({
  identity,
  scope,
  onUnsave,
}: {
  identity: string;
  scope: SavedScope;
  onUnsave: (item: FeedCardItem) => void;
}) {
  const apiBase = useApiBase();
  // Follows the server only while 收藏 is on screen; coming back re-reads a list gone stale.
  const focused = useIsFocused();
  const query = useInfiniteQuery({ ...savedQuery(identity, scope), subscribed: focused });
  useSyncExternalStore(hiddenSaved.subscribe, hiddenSaved.getSnapshot);
  const [refreshing, setRefreshing] = useState(false);
  const offsetKey = `${identity}|${JSON.stringify(scope)}`;
  const searching = scope.q.trim() !== '';

  const cards = useMemo(
    () =>
      (query.data?.pages.flatMap((page) => page.items) ?? []).map((item) =>
        savedCard(item, query.dataUpdatedAt, absoluteUrl(apiBase)),
      ),
    [query.data, query.dataUpdatedAt, apiBase],
  );
  const items = cards.filter((card) => !hiddenSaved.has(identity, card.id));

  async function refresh() {
    setRefreshing(true);
    try {
      await query.refetch();
    } finally {
      setRefreshing(false);
    }
  }

  const open = useCallback((id: string) => router.push({ pathname: '/article/[id]', params: { id } }), []);

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
      moreFailed: query.isFetchNextPageError ? `更早的收藏没加载出来：${errorMessage(query.error)}` : null,
      busy: query.isFetching,
    };
  }

  return (
    <FeedList
      empty={
        searching ? (
          <EmptyState title="没有匹配的收藏" />
        ) : scope.group !== null ? (
          <EmptyState title="这个分类里还没有收藏" />
        ) : (
          <EmptyState description="点卡片右下角的书签即可收藏。" title="还没有收藏" />
        )
      }
      heading={searching ? '搜索结果' : '最近收藏'}
      onEndReached={() => void query.fetchNextPage({ cancelRefetch: false })}
      onOpen={open}
      onPosition={(position) => positions.remember(offsetKey, position)}
      onRefresh={() => void refresh()}
      onRetry={() => void query.refetch()}
      onToggleSave={onUnsave}
      position={positions.get(offsetKey)}
      refreshing={refreshing}
      state={state}
    />
  );
}

export default function Saved() {
  const identity = useIdentity();
  const visible = useRef(true);
  useFocusEffect(
    useCallback(() => {
      visible.current = true;
      return () => {
        visible.current = false;
      };
    }, []),
  );
  const absolute = absoluteUrl(useApiBase());
  const online = useOnline();
  const me = useQuery(meQuery(identity));
  const subscriptions = useQuery(subscriptionsQuery(identity));
  const folders = folderList(subscriptions.data ?? []).map((group) => ({ key: group, label: folderLabel(group) }));

  const [typed, setTyped] = useState('');
  const [q, setQ] = useState('');
  const searchTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const [chosen, setChosen] = useState<string | null>(null);
  // A folder gone since (its last subscription moved or left) falls back to 全部.
  const group = chosen !== null && folders.some((f) => f.key === chosen) ? chosen : null;
  const [notice, setNotice] = useState<{ id: number; message: string; undo?: () => void } | null>(null);

  const showNotice = useCallback((message: string, undo?: () => void) => {
    if (!visible.current) return;
    const id = Date.now();
    setNotice({ id, message, undo });
    // An Undo must not stay pressable once its toast has faded.
    setTimeout(() => setNotice((current) => (current?.id === id ? null : current)), duration.toast);
  }, []);

  function search(text: string) {
    setTyped(text);
    clearTimeout(searchTimer.current);
    searchTimer.current = setTimeout(() => setQ(text), SEARCH_DELAY);
  }

  const unsave = useCallback(
    (item: FeedCardItem) => {
      const undo = unsaveLater(identity, item.id, duration.toast, (error) =>
        showNotice(
          error instanceof UnknownOutcome ? '没收到服务器的确认，已重新加载收藏。' : `取消收藏没有成功：${errorMessage(error)}`,
        ),
      );
      showNotice('已取消收藏', undo);
    },
    [identity, showNotice],
  );

  const scope: SavedScope = { q: q.trim(), group };
  const name = me.data?.display_name || me.data?.email.split('@')[0] || '';
  return (
    <>
      <SavedView
        banner={online ? null : '没有网络，显示的是之前的内容。'}
        folder={group}
        folders={folders}
        onSearch={search}
        onSelectFolder={setChosen}
        query={typed}
        user={name ? { name, avatarUrl: absolute(me.data?.avatar_url) } : undefined}
      >
        {/* Another search or folder is another list (its own data and position). */}
        <SavedList identity={identity} key={JSON.stringify(scope)} onUnsave={unsave} scope={scope} />
      </SavedView>
      {notice ? (
        <Animated.View className="absolute bottom-6 left-0 right-0" key={notice.id} pointerEvents="box-none" style={toastLife}>
          <Toast
            action={
              notice.undo ? (
                <Button
                  onPress={() => {
                    notice.undo?.();
                    setNotice(null);
                  }}
                  title="撤销"
                  variant="ghost"
                />
              ) : undefined
            }
            message={notice.message}
          />
        </Animated.View>
      ) : null}
    </>
  );
}
