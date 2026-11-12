/**
 * A feed list with every state it can be in (react-guidelines §4.11): first load (skeleton after
 * --delay-loading), failed with nothing to show (message + retry), failed refresh (old items kept,
 * message above), empty, pull to refresh, and loading older items at the end.
 * Presentational: the route passes data and callbacks.
 */
import { useRef, useState, type ReactElement } from 'react';
import { ActivityIndicator, FlatList, RefreshControl, View, type FlatListProps } from 'react-native';
import Animated from 'react-native-reanimated';
import { useCSSVariable } from 'uniwind';

import { anchorStep, type ListPosition } from '@/lib/list-memory';
import { Button, EmptyState, Message, SectionHeading, Skeleton, Text } from '@/ui';
import { delayLoading, duration } from '@/ui/theme';

import { useChromeInsets, useChromeScroll } from './CollapsingChrome';
import { FeedCard, type FeedCardItem } from './FeedCard';
import { t } from '@/lib/i18n';

export type FeedListState =
  | { kind: 'loading' }
  | { kind: 'failed'; message: string }
  | {
      kind: 'ready';
      items: FeedCardItem[];
      refreshFailed?: string | null;
      hasMore: boolean;
      loadingMore: boolean;
      moreFailed?: string | null;
      /** any fetch in flight (a refresh): loading more waits, so it cannot cancel the refresh */
      busy?: boolean;
    };

const viewability = { itemVisiblePercentThreshold: 10 };
type ViewableInfo = Parameters<NonNullable<FlatListProps<FeedCardItem>['onViewableItemsChanged']>>[0];

const appearLate = {
  animationName: { from: { opacity: 0 }, to: { opacity: 1 } },
  animationDuration: `${duration.fast}ms`,
  animationDelay: `${delayLoading}ms`,
  animationFillMode: 'both',
} as const;

export function FeedList({
  state,
  refreshing = false,
  empty,
  onRefresh,
  onRetry,
  onEndReached,
  onOpen,
  onToggleSave,
  position,
  onPosition,
  heading,
  active = true,
}: {
  /** the list on screen (a pager shows one of several): only it moves the collapsing chrome */
  active?: boolean;
  state: FeedListState;
  /** a section heading above the items, when there are any (收藏: 最近收藏 / 搜索结果) */
  heading?: string;
  /** where the reader left this list last time; restored once, by offset and then by item */
  position?: ListPosition | null;
  onPosition?: (position: ListPosition) => void;
  refreshing?: boolean;
  empty: ReactElement;
  onRefresh?: () => void;
  onRetry?: () => void;
  onEndReached?: () => void;
  onOpen?: (id: string) => void;
  onToggleSave?: (item: FeedCardItem) => void;
}) {
  const spinner = useCSSVariable('--color-icon-neutral') as string | undefined;
  const list = useRef<FlatList<FeedCardItem>>(null);
  // Scroll bookkeeping is UI lifecycle, not business state (react-guidelines §5), so it lives in
  // refs, written only in event handlers.
  const viewport = useRef(0);
  const offset = useRef(position?.offset ?? 0);
  const anchor = useRef<string | null>(position?.anchorId ?? null);
  const restoring = useRef<'offset' | 'anchor' | 'done'>(position && position.offset > 0 ? 'offset' : 'done');
  const ids = useRef<string[]>([]);
  // In a tab page the header and tab bar slide away as the list scrolls (CollapsingChrome); the
  // content leaves room for them. The top area is measured before the list mounts.
  const insets = useChromeInsets();
  const topInset = insets.top;
  // FlatList needs one stable function for the life of the list.
  const [onViewable] = useState(() => ({ viewableItems }: ViewableInfo) => {
    const visible = viewableItems.filter((t) => t.isViewable && t.item).map((t) => t.item.id);
    if (restoring.current === 'anchor') {
      const step = anchorStep(anchor.current, visible, ids.current);
      if (step.kind === 'scroll') {
        // Below the page's top area, which lies over the list.
        list.current?.scrollToIndex({ index: step.index, animated: false, viewPosition: 0, viewOffset: topInset });
        return;
      }
      restoring.current = 'done';
    }
    // The top item is reported with the next scroll event (remember below).
    if (restoring.current === 'done' && visible.length > 0) anchor.current = visible[0];
  });
  function remember(y: number) {
    if (restoring.current !== 'done') return; // our own restore scrolls are not the reader's
    offset.current = y;
    onPosition?.({ offset: y, anchorId: anchor.current });
  }
  const onScroll = useChromeScroll({
    active,
    onOffset: remember,
    onDragStart: () => {
      restoring.current = 'done'; // the reader took over
    },
  });

  if (state.kind === 'loading') {
    return (
      <Animated.View className="gap-8 px-6 py-6" style={[appearLate, { marginTop: insets.top }]}>
        {[0, 1, 2, 3].map((index) => (
          <Skeleton key={index} lines={3} />
        ))}
      </Animated.View>
    );
  }
  if (state.kind === 'failed') {
    return (
      <View className="flex-1" style={{ paddingTop: insets.top }}>
        <EmptyState action={<Button onPress={onRetry} title={t().common.retry} />} description={state.message} title={t().common.loadFailed} />
      </View>
    );
  }
  return (
    <Animated.FlatList
      ListEmptyComponent={empty}
      ListFooterComponent={
        state.moreFailed ? (
          <View className="items-center gap-2 px-6 py-6">
            <Text level="desc" tone="secondary">
              {state.moreFailed}
            </Text>
            <Button onPress={onEndReached} title={t().common.retry} variant="ghost" />
          </View>
        ) : state.loadingMore ? (
          <View className="items-center py-6">
            <ActivityIndicator color={spinner} />
          </View>
        ) : undefined
      }
      ListHeaderComponent={
        state.refreshFailed || (heading && state.items.length > 0) ? (
          <>
            {state.refreshFailed ? (
              <View className="px-6 pt-4">
                <Message action={<Button onPress={onRetry} title={t().common.retry} variant="ghost" />} kind="danger">
                  {state.refreshFailed}
                </Message>
              </View>
            ) : null}
            {heading && state.items.length > 0 ? <SectionHeading className="-mb-3 pt-4" title={heading} /> : null}
          </>
        ) : undefined
      }
      contentContainerStyle={{ paddingTop: insets.top, paddingBottom: insets.bottom }}
      data={state.items}
      keyExtractor={(item) => item.id}
      // Restore once the list can scroll that far (content ≥ offset + its own height); then check
      // the item that was on top and, if items above changed, scroll to that item instead.
      onContentSizeChange={(_, height) => {
        ids.current = state.items.map((item) => item.id);
        if (restoring.current !== 'offset' || viewport.current === 0 || height < offset.current + viewport.current) return;
        restoring.current = 'anchor';
        list.current?.scrollToOffset({ offset: offset.current, animated: false });
      }}
      onScrollToIndexFailed={(info) => {
        list.current?.scrollToOffset({ offset: info.averageItemLength * info.index, animated: false });
      }}
      onViewableItemsChanged={onViewable}
      viewabilityConfig={viewability}
      onLayout={(event) => {
        viewport.current = event.nativeEvent.layout.height;
      }}
      onEndReached={state.hasMore && !state.loadingMore && !state.moreFailed && !state.busy ? onEndReached : undefined}
      onEndReachedThreshold={0.6}
      // Every frame for the chrome; the position is remembered now and then and when a drag or fling ends.
      onScroll={onScroll}
      ref={list}
      scrollEventThrottle={16}
      refreshControl={
        <RefreshControl colors={spinner ? [spinner] : undefined} onRefresh={onRefresh} progressViewOffset={insets.top} refreshing={refreshing} />
      }
      renderItem={({ item }) => <FeedCard item={item} onOpen={onOpen} onToggleSave={onToggleSave} />}
    />
  );
}
