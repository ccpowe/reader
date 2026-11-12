/**
 * 今日 — assembly only (react-guidelines §6): reads subscriptions and feeds, keeps which folder or
 * channel is shown, and hands data and callbacks to TodayView / FeedList / ChannelPicker.
 * Folders are swipeable pages (P8, old app); only the shown page and its neighbours hold a list.
 */
import { onlineManager, useInfiniteQuery, useQuery } from '@tanstack/react-query';
import { router, useFocusEffect, useIsFocused, useLocalSearchParams } from 'expo-router';
import { useCallback, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { View } from 'react-native';
import PagerView from 'react-native-pager-view';
import Animated from 'react-native-reanimated';

import { FeedList, type FeedListState } from '@/components/FeedList';
import type { FeedCardItem } from '@/components/FeedCard';
import type { FeedItem, SubscriptionResponse } from '@/lib/api/schema.gen';
import { queryClient, useIdentity, useSession } from '@/lib/app-session';
import { absoluteUrl, folderLabel, folderList, homeNewCount, sourceKind, toCard, type FeedScope } from '@/lib/feed';
import { markViewed, setIncludeInHome, subscriptionsQuery } from '@/lib/queries/subscriptions';
import { feedQuery, meQuery, setSaved, UnknownOutcome } from '@/lib/queries/today';
import { createListMemory } from '@/lib/list-memory';
import { isAborted } from '@/lib/query';
import { errorMessage } from '@/lib/user-error';
import { ChannelPicker, type ChannelRow } from '@/screens/ChannelPicker';
import { TodayView, todayLabel } from '@/screens/TodayView';
import { Button, EmptyState, Sheet, Toast } from '@/ui';
import { duration } from '@/ui/theme';

function useApiBase(): string {
  const state = useSession();
  return state.status === 'signed-in' ? state.server.apiBaseUrl : '';
}

function useOnline(): boolean {
  return useSyncExternalStore(onlineManager.subscribe.bind(onlineManager), () => onlineManager.isOnline());
}

/** A transient notice (failed save …): fades in, stays --duration-toast, fades out. A CSS animation, no timer state. */
const toastLife = {
  animationName: { '0%': { opacity: 0 }, '4%': { opacity: 1 }, '94%': { opacity: 1 }, '100%': { opacity: 0 } },
  animationDuration: `${duration.toast}ms`,
  animationFillMode: 'both',
} as const;

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

function FeedPage({
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
    state = query.isError && !isAborted(query.error) ? { kind: 'failed', message: errorMessage(query.error) } : { kind: 'loading' };
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

function rowOf(subscription: SubscriptionResponse, absolute: (url: string | null | undefined) => string | null): ChannelRow {
  return {
    id: subscription.id,
    name: subscription.name ?? subscription.url,
    kind: sourceKind(subscription.type),
    address: subscription.url.replace(/^https?:\/\//, ''),
    newCount: subscription.new_count,
    avatarUrl: absolute(subscription.icon_url),
    includeInHome: subscription.include_in_home,
  };
}

export default function Today() {
  const identity = useIdentity();
  const focused = useIsFocused();
  // A result that arrives after the user left Today is not shown (visit-scoped, react-guidelines §1).
  const visible = useRef(true);
  useFocusEffect(
    useCallback(() => {
      visible.current = true;
      return () => {
        visible.current = false;
      };
    }, []),
  );
  const [opened] = useState(() => new Date()); // the date line: when the page was opened
  const absolute = absoluteUrl(useApiBase());
  const online = useOnline();
  const me = useQuery(meQuery(identity));
  const subscriptions = useQuery(subscriptionsQuery(identity));
  const list = subscriptions.data ?? [];
  const folders = folderList(list);
  const pages: (string | null)[] = [null, ...folders];

  const pager = useRef<PagerView>(null);
  const [page, setPage] = useState(0);
  const [channel, setChannel] = useState<string | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [search, setSearch] = useState('');
  const [notice, setNotice] = useState<{ id: number; message: string } | null>(null);

  const showNotice = useCallback((message: string) => {
    if (visible.current) setNotice((current) => ({ id: (current?.id ?? 0) + 1, message }));
  }, []);
  const current = Math.min(page, pages.length - 1);
  const channelSubscription = channel ? list.find((s) => s.id === channel) : undefined;
  // A subscription gone since (unsubscribed elsewhere) is shown no longer.
  const shownChannel = channel && (subscriptions.data === undefined || channelSubscription) ? channel : null;

  function selectFolder(folder: string | null) {
    const index = pages.indexOf(folder);
    if (index < 0) return;
    setPage(index);
    pager.current?.setPage(index);
  }

  function selectChannel(id: string) {
    setChannel(id);
    setPickerOpen(false);
    const subscription = list.find((s) => s.id === id);
    if (subscription) void markViewed(identity, subscription).catch(() => undefined); // the count is a hint; a failure costs nothing
  }

  // 订阅 opens one subscription here (`/?channel=<id>&at=<time>`; it has marked it viewed). Each
  // request has its own `at`, so opening the same one again after leaving it works too.
  const { channel: requested, at } = useLocalSearchParams<{ channel?: string; at?: string }>();
  const [handled, setHandled] = useState<string | undefined>(undefined);
  if (requested && at && at !== handled) {
    setHandled(at);
    setChannel(requested);
    setPickerOpen(false);
  }

  async function toggleHome(id: string, include: boolean) {
    const subscription = list.find((s) => s.id === id);
    if (!subscription) return;
    try {
      await setIncludeInHome(identity, subscription, include);
    } catch (error) {
      showNotice(error instanceof UnknownOutcome ? '没收到服务器的确认，已重新加载频道设置。' : `没能更改：${errorMessage(error)}`);
    }
  }

  const groups = folders.map((group) => ({
    key: group,
    label: folderLabel(group),
    rows: list.filter((s) => (s.group?.trim() || '') === group).map((s) => rowOf(s, absolute)),
  }));

  let body;
  if (subscriptions.data === undefined) {
    body = subscriptions.isError ? (
      <EmptyState action={<Button onPress={() => void subscriptions.refetch()} title="重试" />} description={errorMessage(subscriptions.error)} title="没能加载" />
    ) : (
      <FeedList empty={<View />} state={{ kind: 'loading' }} />
    );
  } else if (list.length === 0) {
    body = (
      <EmptyState
        action={<Button large onPress={() => router.navigate('/subscriptions')} title="添加订阅" variant="primary" />}
        description="添加 RSS、网站、YouTube、X 或 Reddit，新内容会出现在这里。"
        title="还没有订阅"
      />
    );
  } else if (shownChannel) {
    body = <FeedPage active={focused} identity={identity} key={shownChannel} onNotice={showNotice} scope={{ kind: 'subscription', id: shownChannel }} />;
  } else {
    body = (
      <PagerView
        initialPage={current}
        // A different set of folders is a different pager (react-guidelines §2: reset by key).
        key={pages.join('\u0000')}
        onPageSelected={(event) => setPage(event.nativeEvent.position)}
        ref={pager}
        style={{ flex: 1 }}
      >
        {pages.map((folder, index) => (
          <View key={folder ?? '*'} style={{ flex: 1 }}>
            {Math.abs(index - current) <= 1 ? (
              <FeedPage
                active={focused && index === current}
                identity={identity}
                onNotice={showNotice}
                scope={folder === null ? { kind: 'home' } : { kind: 'group', group: folder }}
              />
            ) : null}
          </View>
        ))}
      </PagerView>
    );
  }

  const name = me.data?.display_name || me.data?.email.split('@')[0] || '';
  return (
    <>
      <TodayView
        banner={online ? null : '没有网络，显示的是之前的内容。'}
        channel={channelSubscription ? (channelSubscription.name ?? channelSubscription.url) : null}
        date={todayLabel(opened)}
        folder={pages[current] ?? null}
        folders={folders.map((group) => ({ key: group, label: folderLabel(group) }))}
        newCount={homeNewCount(list)}
        onClearChannel={() => setChannel(null)}
        onOpenChannels={() => {
          setSearch('');
          setPickerOpen(true);
        }}
        onSelectFolder={selectFolder}
        user={name ? { name, avatarUrl: absolute(me.data?.avatar_url) } : undefined}
      >
        {body}
      </TodayView>
      {notice ? (
        <Animated.View className="absolute bottom-6 left-0 right-0" key={notice.id} pointerEvents="none" style={toastLife}>
          <Toast message={notice.message} />
        </Animated.View>
      ) : null}
      <Sheet onClose={() => setPickerOpen(false)} subtitle="勾选的频道显示在全部中" title="选择频道" visible={pickerOpen}>
        <ChannelPicker
          channel={shownChannel}
          groups={groups}
          onClear={() => {
            setChannel(null);
            setPickerOpen(false);
          }}
          onSearch={setSearch}
          onSelect={selectChannel}
          onToggleHome={(id, include) => void toggleHome(id, include)}
          query={search}
        />
      </Sheet>
    </>
  );
}
