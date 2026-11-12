/**
 * 今日 — assembly only (react-guidelines §6): reads subscriptions and feeds, keeps which folder or
 * channel is shown, and hands data and callbacks to TodayView / FeedList / ChannelPicker.
 * Folders are swipeable pages (P8, old app); only the shown page and its neighbours hold a list.
 */
import { ChromePadded, useTabBarInset } from '@/components/CollapsingChrome';
import { onlineManager, useQuery } from '@tanstack/react-query';
import { router, useFocusEffect, useIsFocused } from 'expo-router';
import { useCallback, useRef, useState, useSyncExternalStore } from 'react';
import { BackHandler, View } from 'react-native';
import PagerView from 'react-native-pager-view';
import Animated from 'react-native-reanimated';

import { FeedList } from '@/components/FeedList';
import { FeedPage } from '@/components/FeedPage';
import type { SubscriptionResponse } from '@/lib/api/schema.gen';
import { useIdentity, useSession } from '@/lib/app-session';
import { absoluteUrl, folderLabel, folderList, homeNewCount, sourceKind } from '@/lib/feed';
import { markViewed, setIncludeInHome, subscriptionsQuery } from '@/lib/queries/subscriptions';
import { meQuery, UnknownOutcome } from '@/lib/queries/today';
import { errorMessage } from '@/lib/user-error';
import { ChannelPicker, type ChannelRow } from '@/screens/ChannelPicker';
import { TodayView, todayLabel } from '@/screens/TodayView';
import { Button, EmptyState, Sheet, Toast } from '@/ui';
import { duration } from '@/ui/theme';
import { t } from '@/lib/i18n';
import { useLanguage } from '@/lib/interface-language';

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
  useLanguage(); // texts follow the interface language
  const identity = useIdentity();
  const tabBar = useTabBarInset();
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
  const [markingAll, setMarkingAll] = useState(false);
  const [markError, setMarkError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [notice, setNotice] = useState<{ id: number; message: string } | null>(null);

  const showNotice = useCallback((message: string) => {
    if (visible.current) setNotice((current) => ({ id: (current?.id ?? 0) + 1, message }));
  }, []);
  const current = Math.min(page, pages.length - 1);
  const channelSubscription = channel ? list.find((s) => s.id === channel) : undefined;
  // A subscription gone since (unsubscribed elsewhere) is shown no longer.
  const shownChannel = channel && (subscriptions.data === undefined || channelSubscription) ? channel : null;
  // Android back leaves the one-channel view first (old app), then does what it always does.
  useFocusEffect(
    useCallback(() => {
      if (!shownChannel) return undefined;
      const subscription = BackHandler.addEventListener('hardwareBackPress', () => {
        setChannel(null);
        return true;
      });
      return () => subscription.remove();
    }, [shownChannel]),
  );

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

  /** Clear one subscription's new-content count without opening it. */
  async function markRead(id: string) {
    const subscription = list.find((s) => s.id === id);
    if (!subscription) return;
    setMarkError(null);
    try {
      await markViewed(identity, subscription);
    } catch (error) {
      setMarkError(t().today.markReadFailed(errorMessage(error)));
    }
  }

  /** 全部标为已读: every subscription with new items, up to what the list shows now. */
  async function markAllRead() {
    setMarkingAll(true);
    setMarkError(null);
    const results = await Promise.allSettled(list.filter((s) => s.new_count > 0).map((s) => markViewed(identity, s)));
    setMarkingAll(false);
    const failed = results.find((result): result is PromiseRejectedResult => result.status === 'rejected');
    if (failed) setMarkError(t().today.markReadFailed(errorMessage(failed.reason)));
  }

  async function toggleHome(id: string, include: boolean) {
    const subscription = list.find((s) => s.id === id);
    if (!subscription) return;
    try {
      await setIncludeInHome(identity, subscription, include);
    } catch (error) {
      showNotice(error instanceof UnknownOutcome ? t().today.channelUnknown : t().common.changeFailed(errorMessage(error)));
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
      <ChromePadded>
        <EmptyState action={<Button onPress={() => void subscriptions.refetch()} title={t().common.retry} />} description={errorMessage(subscriptions.error)} title={t().common.loadFailed} />
      </ChromePadded>
    ) : (
      <FeedList empty={<View />} state={{ kind: 'loading' }} />
    );
  } else if (list.length === 0) {
    body = (
      <ChromePadded>
        <EmptyState
          action={<Button large onPress={() => router.navigate('/subscriptions')} title={t().today.addSubscription} variant="primary" />}
          description={t().today.noSubscriptionsHint}
          title={t().today.noSubscriptions}
        />
      </ChromePadded>
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
        onPressUser={() => router.navigate('/me')}
        banner={online ? null : t().common.offline}
        channel={channelSubscription ? (channelSubscription.name ?? channelSubscription.url) : null}
        date={todayLabel(opened)}
        folder={pages[current] ?? null}
        folders={folders.map((group) => ({ key: group, label: folderLabel(group) }))}
        newCount={homeNewCount(list)}
        onClearChannel={() => setChannel(null)}
        onOpenChannels={() => {
          setSearch('');
          setMarkError(null);
          setPickerOpen(true);
        }}
        onSelectFolder={selectFolder}
        user={name ? { name, avatarUrl: absolute(me.data?.avatar_url) } : undefined}
      >
        {body}
      </TodayView>
      {notice ? (
        <Animated.View className="absolute left-0 right-0" key={notice.id} pointerEvents="none" style={[toastLife, { bottom: tabBar + 24 }]}>
          <Toast message={notice.message} />
        </Animated.View>
      ) : null}
      <Sheet onClose={() => setPickerOpen(false)} subtitle={t().today.pickChannelHint} title={t().today.pickChannel} visible={pickerOpen}>
        <ChannelPicker
          channel={shownChannel}
          groups={groups}
          error={markError}
          markingAll={markingAll}
          onMarkAllRead={() => void markAllRead()}
          onMarkRead={(id) => void markRead(id)}
          onSearch={setSearch}
          onSelect={selectChannel}
          onToggleHome={(id, include) => void toggleHome(id, include)}
          query={search}
        />
      </Sheet>
    </>
  );
}
