/**
 * 订阅 — assembly only (react-guidelines §6): reads the subscriptions, keeps which folder, search
 * and sheet are shown, and calls the subscription commands. Opening a subscription shows it alone
 * on Today (PRODUCT §3.3, old app). Unsubscribing hides it at once with Undo (P27).
 */
import { onlineManager, useQuery } from '@tanstack/react-query';
import { router, useFocusEffect } from 'expo-router';
import { useCallback, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import Animated from 'react-native-reanimated';

import type { SubscriptionResponse } from '@/lib/api/schema.gen';
import { useIdentity, useSession } from '@/lib/app-session';
import { absoluteUrl, folderList, UNGROUPED } from '@/lib/feed';
import {
  markViewed,
  setIncludeInHome,
  subscribe,
  subscriptionsQuery,
  unsubscribeLater,
  updateSubscription,
} from '@/lib/queries/subscriptions';
import { meQuery, UnknownOutcome } from '@/lib/queries/today';
import { displayAddress, foldersOf, toItem, type SubscriptionType } from '@/lib/subscriptions';
import { errorMessage } from '@/lib/user-error';
import { AddSubscriptionForm, SubscriptionMenu, SubscriptionsView, type SubscriptionsState } from '@/screens/SubscriptionsView';
import { Button, Sheet, Toast } from '@/ui';
import { duration } from '@/ui/theme';

const toastLife = {
  animationName: { '0%': { opacity: 0 }, '4%': { opacity: 1 }, '94%': { opacity: 1 }, '100%': { opacity: 0 } },
  animationDuration: `${duration.toast}ms`,
  animationFillMode: 'both',
} as const;

function useApiBase(): string {
  const state = useSession();
  return state.status === 'signed-in' ? state.server.apiBaseUrl : '';
}

function useOnline(): boolean {
  return useSyncExternalStore(onlineManager.subscribe.bind(onlineManager), () => onlineManager.isOnline());
}

const unknown = '没收到服务器的确认，已重新加载订阅列表。';

export default function Subscriptions() {
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
  const apiBase = useApiBase();
  const absolute = absoluteUrl(apiBase);
  const online = useOnline();
  const me = useQuery(meQuery(identity));
  const subscriptions = useQuery(subscriptionsQuery(identity));
  const list = useMemo(() => subscriptions.data ?? [], [subscriptions.data]);

  const [openFolder, setOpenFolder] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [refreshing, setRefreshing] = useState(false);
  const [menuId, setMenuId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  // Each opening of the add sheet starts empty (a new key); a failure keeps the input (same key).
  const [addKey, setAddKey] = useState(0);
  const [addError, setAddError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState<{ id: number; message: string; undo?: () => void } | null>(null);

  const showNotice = useCallback((message: string, undo?: () => void) => {
    if (!visible.current) return;
    const id = Date.now();
    setNotice({ id, message, undo });
    // An Undo must not stay pressable once its toast has faded.
    setTimeout(() => setNotice((current) => (current?.id === id ? null : current)), duration.toast);
  }, []);

  const now = subscriptions.dataUpdatedAt;
  const folders = useMemo(() => foldersOf(list, (s) => toItem(s, now, absoluteUrl(apiBase))), [list, now, apiBase]);
  const folderNames = folderList(list).filter((group) => group !== UNGROUPED);
  const menuSubscription = menuId ? list.find((s) => s.id === menuId) : undefined;

  let state: SubscriptionsState;
  if (subscriptions.data !== undefined) state = { kind: 'ready', folders };
  else if (subscriptions.isError) state = { kind: 'failed', message: errorMessage(subscriptions.error) };
  else state = { kind: 'loading' };

  async function refresh() {
    setRefreshing(true);
    try {
      await subscriptions.refetch();
    } finally {
      setRefreshing(false);
    }
  }

  async function add(request: { type: SubscriptionType; value: string; group: string | null }) {
    if (submitting) return;
    setSubmitting(true);
    setAddError(null);
    try {
      const { subscription, existed } = await subscribe(identity, { ...request, name: null });
      setAdding(false);
      setOpenFolder(subscription.group?.trim() || UNGROUPED);
      setSearch('');
      const name = subscription.name ?? displayAddress(subscription.url);
      showNotice(existed ? `已经订阅过「${name}」` : `已添加「${name}」，等待首次扫描`);
    } catch (error) {
      // The input stays either way. Adding again is safe: a source already followed is not followed twice.
      setAddError(
        error instanceof UnknownOutcome
          ? '没收到服务器的确认，不知道是否已添加。可以再点一次「添加」，不会重复订阅。'
          : errorMessage(error),
      );
    } finally {
      setSubmitting(false);
    }
  }

  function change(task: () => Promise<void>) {
    setMenuId(null);
    task().catch((error: unknown) => showNotice(error instanceof UnknownOutcome ? unknown : `没能更改：${errorMessage(error)}`));
  }

  function leave(subscription: SubscriptionResponse) {
    setMenuId(null);
    const undo = unsubscribeLater(identity, subscription, duration.toast, (error) =>
      showNotice(`没能取消订阅：${errorMessage(error)}`),
    );
    showNotice(`已取消订阅「${subscription.name ?? displayAddress(subscription.url)}」`, undo);
  }

  const name = me.data?.display_name || me.data?.email.split('@')[0] || '';
  return (
    <>
      <SubscriptionsView
        banner={online ? null : '没有网络，显示的是之前的内容。'}
        expandedId={expandedId}
        onAdd={() => {
          setAddError(null);
          setAddKey((n) => n + 1);
          setAdding(true);
        }}
        onCloseFolder={() => setOpenFolder(null)}
        onMenu={setMenuId}
        onOpen={(id) => {
          const subscription = list.find((s) => s.id === id);
          if (subscription) void markViewed(identity, subscription).catch(() => undefined); // the count is a hint
          router.navigate({ pathname: '/', params: { channel: id, at: String(Date.now()) } });
        }}
        onOpenFolder={setOpenFolder}
        onRefresh={() => void refresh()}
        onRetry={() => void subscriptions.refetch()}
        onSearch={setSearch}
        onToggleDetail={(id) => setExpandedId((current) => (current === id ? null : id))}
        openFolder={openFolder}
        query={search}
        refreshing={refreshing}
        state={state}
        user={name ? { name, avatarUrl: absolute(me.data?.avatar_url) } : undefined}
      />
      {notice ? (
        <Animated.View className="absolute bottom-24 left-0 right-0" key={notice.id} pointerEvents="box-none" style={toastLife}>
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
      <Sheet onClose={() => !submitting && setAdding(false)} title="添加订阅" visible={adding}>
        <AddSubscriptionForm
          error={addError}
          folders={folderNames}
          initial={{ folder: openFolder || null }}
          key={addKey}
          onEdit={() => setAddError(null)}
          onSubmit={(request) => void add(request)}
          submitting={submitting}
        />
      </Sheet>
      <Sheet
        onClose={() => setMenuId(null)}
        title={menuSubscription?.name ?? menuSubscription?.url ?? ''}
        visible={menuSubscription !== undefined}
      >
        {menuSubscription ? (
          <SubscriptionMenu
            folder={menuSubscription.group?.trim() || UNGROUPED}
            folders={folderNames}
            includeInHome={menuSubscription.include_in_home}
            key={menuSubscription.id}
            name={menuSubscription.name ?? menuSubscription.url}
            onMove={(group) => change(() => updateSubscription(identity, menuSubscription, { group }))}
            onRename={(next) => change(() => updateSubscription(identity, menuSubscription, { name: next }))}
            onToggleHome={(include) => {
              setIncludeInHome(identity, menuSubscription, include).catch((error: unknown) =>
                showNotice(error instanceof UnknownOutcome ? unknown : `没能更改：${errorMessage(error)}`),
              );
            }}
            onUnsubscribe={() => leave(menuSubscription)}
          />
        ) : null}
      </Sheet>
    </>
  );
}
