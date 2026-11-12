/**
 * 榜单 — assembly only (react-guidelines §6): which board and version is shown, the board data
 * and its missing translations, and the 调整 sheet. Boards are swipeable pages (old app); only the
 * shown page and its neighbours hold a list. Opening an entry goes to the reader page (P29).
 */
import { useTabBarInset } from '@/components/CollapsingChrome';
import { onlineManager, useQuery } from '@tanstack/react-query';
import { router, useFocusEffect, useIsFocused } from 'expo-router';
import { useCallback, useRef, useState, useSyncExternalStore } from 'react';
import { View } from 'react-native';
import PagerView from 'react-native-pager-view';
import Animated from 'react-native-reanimated';

import { errorCode, isApiError } from '@/lib/api/errors';
import { useIdentity, useSession } from '@/lib/app-session';
import {
  BOARDS,
  boardLabel,
  VARIANTS,
  defaultView,
  freshness,
  subscribedCommunities,
  toRow,
  unavailableReason,
  viewLabel,
  withTranslations,
  type BoardKey,
  type BoardView,
} from '@/lib/boards';
import { absoluteUrl } from '@/lib/feed';
import { boardQuery, boardTranslationsQuery, refreshBoard, translationPreferenceQuery } from '@/lib/queries/boards';
import { subscriptionsQuery } from '@/lib/queries/subscriptions';
import { meQuery } from '@/lib/queries/today';
import { isAborted } from '@/lib/query';
import { errorMessage } from '@/lib/user-error';
import { BoardList, BoardOptions, BoardsView, type BoardState } from '@/screens/BoardsView';
import { Sheet, Toast } from '@/ui';
import { duration } from '@/ui/theme';
import { t } from '@/lib/i18n';
import { useLanguage } from '@/lib/interface-language';

const toastLife = {
  animationName: { '0%': { opacity: 0 }, '4%': { opacity: 1 }, '94%': { opacity: 1 }, '100%': { opacity: 0 } },
  animationDuration: `${duration.toast}ms`,
  animationFillMode: 'both',
} as const;

/** Where each board version was left, for this app run. */
const offsets = new Map<string, number>();

function useApiBase(): string {
  const state = useSession();
  return state.status === 'signed-in' ? state.server.apiBaseUrl : '';
}

function useOnline(): boolean {
  return useSyncExternalStore(onlineManager.subscribe.bind(onlineManager), () => onlineManager.isOnline());
}

function BoardPage({
  identity,
  view,
  active,
  refreshing,
  onOpenOptions,
  onRefresh,
}: {
  identity: string;
  view: BoardView;
  active: boolean;
  refreshing: boolean;
  onOpenOptions?: () => void;
  onRefresh: () => void;
}) {
  const needsCommunity = view.board === 'reddit' && !view.subreddit;
  const query = useQuery({ ...boardQuery(identity, view), enabled: !needsCommunity, subscribed: active });
  const preference = useQuery(translationPreferenceQuery(identity));
  const translations = useQuery(boardTranslationsQuery(identity, view, query.data, preference.data));
  const offsetKey = `${identity}|${JSON.stringify(view)}`;

  let state: BoardState;
  if (needsCommunity) state = { kind: 'no-community' };
  else if (query.data === undefined) {
    const error = query.error;
    if (!query.isError || isAborted(error)) state = { kind: 'loading' };
    else if (errorCode(error) === 'board_unavailable' && isApiError(error) && error.problem.kind === 'http')
      state = { kind: 'unavailable', reason: unavailableReason(error.problem.message) };
    else state = { kind: 'failed', message: errorMessage(error) };
  } else {
    // Translations turned off since: shown no longer.
    const tr = preference.data?.enabled ? translations.data : undefined;
    const board = tr ? withTranslations(query.data, tr.titles, tr.descriptions, tr.fingerprint) : query.data;
    const now = query.dataUpdatedAt;
    state = {
      kind: 'ready',
      rows: board.items.map((entry) => toRow(entry, view.board, now)),
      freshness: freshness(board, now),
      refreshFailed: query.isRefetchError && !refreshing ? t().common.refreshFailedWith(errorMessage(query.error)) : null,
    };
  }

  return (
    <BoardList
      active={active}
      initialOffset={offsets.get(offsetKey) ?? 0}
      label={viewLabel(view)}
      onOffset={(offset) => offsets.set(offsetKey, offset)}
      onOpen={(row) =>
        router.push({
          pathname: '/board-item',
          params: { board: view.board, variant: view.variant, subreddit: view.subreddit ?? '', url: row.url },
        })
      }
      onOpenOptions={onOpenOptions}
      onRefresh={onRefresh}
      onRetry={() => void query.refetch()}
      refreshing={refreshing}
      state={state}
    />
  );
}

export default function Boards() {
  useLanguage(); // texts follow the interface language
  const identity = useIdentity();
  const tabBar = useTabBarInset();
  const focused = useIsFocused();
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
  const communities = subscribedCommunities(subscriptions.data ?? []);

  const pager = useRef<PagerView>(null);
  const [page, setPage] = useState(0);
  // The version chosen per board; Reddit's community defaults to the first subscribed one (old app).
  const [chosen, setChosen] = useState<Partial<Record<BoardKey, BoardView>>>({});
  const viewOf = (board: BoardKey): BoardView =>
    chosen[board] ?? defaultView(board, board === 'reddit' ? (communities[0] ?? null) : null);
  const [options, setOptions] = useState<BoardKey | null>(null);
  const [refreshing, setRefreshing] = useState<BoardKey | null>(null);
  const [notice, setNotice] = useState<{ id: number; message: string } | null>(null);

  const board = BOARDS[page].key;

  function selectBoard(key: BoardKey) {
    const index = BOARDS.findIndex((b) => b.key === key);
    setPage(index);
    pager.current?.setPage(index);
  }

  async function refresh(key: BoardKey) {
    const view = viewOf(key);
    if (refreshing || (key === 'reddit' && !view.subreddit)) return;
    setRefreshing(key);
    try {
      await refreshBoard(identity, view);
    } catch (error) {
      if (visible.current)
        setNotice((current) => ({ id: (current?.id ?? 0) + 1, message: t().common.refreshFailedWith(errorMessage(error)) }));
    } finally {
      setRefreshing(null);
    }
  }

  const name = me.data?.display_name || me.data?.email.split('@')[0] || '';
  const optionsView = options ? viewOf(options) : null;
  return (
    <>
      <BoardsView
        onPressUser={() => router.navigate('/me')}
        banner={online ? null : t().common.offline}
        board={board}
        onRefresh={() => void refresh(board)}
        onSelectBoard={selectBoard}
        refreshing={refreshing === board}
        user={name ? { name, avatarUrl: absolute(me.data?.avatar_url) } : undefined}
      >
        <PagerView
          initialPage={page}
          onPageSelected={(event) => setPage(event.nativeEvent.position)}
          ref={pager}
          style={{ flex: 1 }}
        >
          {BOARDS.map(({ key }, index) => (
            <View key={key} style={{ flex: 1 }}>
              {Math.abs(index - page) <= 1 ? (
                <BoardPage
                  active={focused && index === page}
                  identity={identity}
                  // Another version is another list (its own data and scroll position).
                  key={JSON.stringify(viewOf(key))}
                  onOpenOptions={VARIANTS[key].length > 0 ? () => setOptions(key) : undefined}
                  onRefresh={() => void refresh(key)}
                  refreshing={refreshing === key}
                  view={viewOf(key)}
                />
              ) : null}
            </View>
          ))}
        </PagerView>
      </BoardsView>
      {notice ? (
        <Animated.View className="absolute left-0 right-0" key={notice.id} pointerEvents="none" style={[toastLife, { bottom: tabBar + 24 }]}>
          <Toast message={notice.message} />
        </Animated.View>
      ) : null}
      <Sheet
        onClose={() => setOptions(null)}
        title={optionsView ? t().boards.adjust(boardLabel(optionsView.board)) : ''}
        visible={optionsView !== null}
      >
        {optionsView ? (
          <BoardOptions
            key={JSON.stringify(optionsView)}
            onApply={(next) => {
              setChosen((current) => ({ ...current, [next.board]: next }));
              setOptions(null);
            }}
            subscribedCommunities={communities}
            view={optionsView}
          />
        ) : null}
      </Sheet>
    </>
  );
}
