/**
 * 字幕轴 under the video player (old app): the captions around the playing moment, each with its
 * time; the one playing is marked; tapping one jumps there. With translation on, each line shows
 * its translation below. The runtime sends a window of captions; reaching the list's top or
 * bottom asks it for the previous or next window. The rows on screen are reported so they are
 * translated first.
 *
 * Following playback: the route calls `follow` (through `ref`) whenever the runtime reports a new
 * timeline, and the playing line scrolls into view, unless the reader is browsing the list (a
 * drag, and a few seconds after it).
 */
import { useImperativeHandle, useRef, useState, type ReactElement, type Ref } from 'react';
import { FlatList, Pressable, View } from 'react-native';

import { captionTime, type Caption, type CaptionTimeline } from '@/lib/caption-host';
import { cn } from '@/lib/cn';
import { Text } from '@/ui';
import { t } from '@/lib/i18n';

const VIEWABILITY = { itemVisiblePercentThreshold: 10 };
type ViewToken = { item: Caption; isViewable: boolean };

function CaptionRow({
  caption,
  active,
  translated,
  onPress,
}: {
  caption: Caption;
  active: boolean;
  translated: boolean;
  onPress: () => void;
}) {
  return (
    <Pressable
      accessibilityLabel={t().reader.seekTo(captionTime(caption.startMs), caption.source)}
      accessibilityRole="button"
      accessibilityState={{ selected: active }}
      className={cn('flex-row gap-4 rounded-lg px-3 py-3 active:bg-pressed', active ? 'bg-selected' : '')}
      onPress={onPress}
    >
      <Text className="w-12 pt-0.5" level="desc" style={{ fontVariant: ['tabular-nums'] }} tone={active ? 'primary' : 'tertiary'}>
        {captionTime(caption.startMs)}
      </Text>
      <View className="flex-1 gap-1">
        <Text level="body" tone={active ? 'primary' : 'secondary'}>
          {caption.source}
        </Text>
        {translated && caption.translated ? (
          <Text level="desc" tone="secondary">
            {caption.translated}
          </Text>
        ) : null}
      </View>
    </Pressable>
  );
}

/** After the reader's own scrolling, playback is not followed for this long. */
const BROWSE_PAUSE_MS = 4_000;

export type CaptionListHandle = { follow: (timeline: CaptionTimeline) => void };

export function CaptionList({
  ref,
  timeline,
  translated,
  empty,
  header,
  onSeek,
  onVisible,
  onMoveWindow,
}: {
  ref?: Ref<CaptionListHandle>;
  timeline: CaptionTimeline;
  /** show translations under the lines */
  translated: boolean;
  /** shown while there are no captions (waiting, or the video has none) */
  empty: string;
  header?: ReactElement;
  onSeek: (caption: Caption) => void;
  onVisible?: (ids: string[]) => void;
  onMoveWindow?: (direction: 'previous' | 'next', firstId: string) => void;
}) {
  // FlatList takes its viewability callback once: kept from mount (the parent's `onVisible` must
  // reach the current host when called, not capture it).
  const [onViewable] = useState(() => {
    let last = '';
    return ({ viewableItems }: { viewableItems: ViewToken[] }) => {
      const ids = viewableItems
        .filter((token) => token.isViewable)
        .map((token) => token.item.id)
        .slice(0, 20);
      if (ids.join('|') === last) return;
      last = ids.join('|');
      onVisible?.(ids);
    };
  });
  const first = timeline.captions[0]?.id;
  // Only a drag moves the window, once per drag: a new window remounts the list at its top, and
  // that alone must not count as reaching the top (it would walk straight back).
  const dragged = useRef(false);
  const pending = useRef<'previous' | 'next' | null>(null);
  const list = useRef<FlatList<Caption> | null>(null);
  const browsing = useRef({ touching: false, until: 0 });
  const followed = useRef<string | null>(null);
  useImperativeHandle(ref, () => ({
    follow(next) {
      const id = next.currentId;
      if (!id || id === followed.current) return;
      if (browsing.current.touching || Date.now() < browsing.current.until) return;
      const index = next.captions.findIndex((caption) => caption.id === id);
      if (index < 0) return;
      followed.current = id;
      // after this timeline has rendered
      requestAnimationFrame(() => list.current?.scrollToIndex({ index, viewPosition: 0.3, animated: true }));
    },
  }));
  function stopBrowsing() {
    browsing.current = { touching: false, until: Date.now() + BROWSE_PAUSE_MS };
  }

  function move(direction: 'previous' | 'next') {
    if (!dragged.current || !first) return;
    if (direction === 'previous' ? !timeline.hasPrevious : !timeline.hasNext) return;
    dragged.current = false;
    pending.current = direction;
    onMoveWindow?.(direction, first);
  }

  return (
    <FlatList
      ListEmptyComponent={
        <Text className="px-3 pt-4" level="desc" tone="secondary">
          {empty}
        </Text>
      }
      ListHeaderComponent={header}
      contentContainerClassName="px-3 pb-8"
      data={timeline.captions}
      extraData={`${timeline.currentId}:${translated}`}
      // A new window of captions is a new list (scroll starts over), as in the old app.
      key={first ?? 'empty'}
      keyExtractor={(caption) => caption.id}
      onContentSizeChange={() => {
        // The earlier window arrives above where the reader was: start at its end.
        if (pending.current === 'previous') list.current?.scrollToEnd({ animated: false });
        pending.current = null;
      }}
      onEndReached={() => move('next')}
      onEndReachedThreshold={0.1}
      onMomentumScrollEnd={stopBrowsing}
      onScrollBeginDrag={() => {
        dragged.current = true;
        browsing.current = { touching: true, until: 0 };
      }}
      onScrollEndDrag={stopBrowsing}
      onScrollToIndexFailed={(info) => {
        // Rows not measured yet: jump near it, then try once more.
        list.current?.scrollToOffset({ offset: info.averageItemLength * info.index, animated: false });
        setTimeout(() => list.current?.scrollToIndex({ index: info.index, viewPosition: 0.3, animated: true }), 100);
      }}
      onStartReached={() => move('previous')}
      onStartReachedThreshold={0.1}
      onViewableItemsChanged={onViewable}
      ref={list}
      renderItem={({ item }) => (
        <CaptionRow active={item.id === timeline.currentId} caption={item} onPress={() => onSeek(item)} translated={translated} />
      )}
      showsVerticalScrollIndicator={false}
      viewabilityConfig={VIEWABILITY}
    />
  );
}
