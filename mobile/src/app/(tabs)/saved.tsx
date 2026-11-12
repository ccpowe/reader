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
import { absoluteUrl, folderLabel, folderList, missingTitles, withTitleTranslations } from '@/lib/feed';
import { createListMemory } from '@/lib/list-memory';
import { hiddenSaved, savedQuery, unsaveLater } from '@/lib/queries/saved';
import { titleTranslationsQuery, translationPreferenceQuery } from '@/lib/queries/translation';
import { subscriptionsQuery } from '@/lib/queries/subscriptions';
import { meQuery, UnknownOutcome } from '@/lib/queries/today';
import { isAborted } from '@/lib/query';
import { savedCard, type SavedScope } from '@/lib/saved';
import { errorMessage } from '@/lib/user-error';
import { SavedView } from '@/screens/SavedView';
import { Button, EmptyState, Toast } from '@/ui';
import { duration } from '@/ui/theme';
import { t } from '@/lib/i18n';
import { useLanguage } from '@/lib/interface-language';

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
  const lang = useLanguage(); // card texts are in the interface language
  useSyncExternalStore(hiddenSaved.subscribe, hiddenSaved.getSnapshot);
  const [refreshing, setRefreshing] = useState(false);
  const offsetKey = `${identity}|${JSON.stringify(scope)}`;
  const searching = scope.q.trim() !== '';

  // Titles the server had no translation for yet (items saved from a board, for one) are asked for here.
  const loaded = useMemo(() => query.data?.pages.flatMap((page) => page.items) ?? [], [query.data]);
  const missing = useMemo(() => missingTitles(loaded), [loaded]);
  const preference = useQuery(translationPreferenceQuery(identity));
  const titles = useQuery({ ...titleTranslationsQuery(identity, ['saved', scope], missing, preference.data), subscribed: focused });
  // Only while titles are translated: switching it off shows the originals at once.
  const filled = preference.data?.enabled ? titles.data : undefined;

  const cards = useMemo(
    () =>
      withTitleTranslations(loaded, filled?.done, filled?.fingerprint ?? '').map((item) =>
        savedCard(item, query.dataUpdatedAt, absoluteUrl(apiBase)),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- lang: the texts savedCard builds
    [loaded, filled, query.dataUpdatedAt, apiBase, lang],
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
      refreshFailed: query.isRefetchError && !refreshing ? t().common.refreshFailed : null,
      hasMore: query.hasNextPage,
      loadingMore: query.isFetchingNextPage,
      moreFailed: query.isFetchNextPageError ? t().saved.moreFailed(errorMessage(query.error)) : null,
      busy: query.isFetching,
    };
  }

  return (
    <FeedList
      empty={
        searching ? (
          <EmptyState title={t().saved.noMatch} />
        ) : scope.group !== null ? (
          <EmptyState title={t().saved.emptyFolder} />
        ) : (
          <EmptyState description={t().saved.empty} title={t().saved.emptyTitle} />
        )
      }
      heading={searching ? t().common.searchResults : t().saved.recent}
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
  useLanguage(); // texts follow the interface language
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
          error instanceof UnknownOutcome ? t().saved.unsaveUnknown : t().saved.unsaveFailed(errorMessage(error)),
        ),
      );
      showNotice(t().saved.unsaved, undo);
    },
    [identity, showNotice],
  );

  const scope: SavedScope = { q: q.trim(), group };
  const name = me.data?.display_name || me.data?.email.split('@')[0] || '';
  return (
    <>
      <SavedView
        banner={online ? null : t().common.offline}
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
                  title={t().common.undo}
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
