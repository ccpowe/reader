import { useCallback, useEffect, useMemo, useRef } from 'react';
import type {
  FlatList,
  NativeScrollEvent,
  NativeSyntheticEvent,
  ViewToken,
} from 'react-native';
import {
  useAnimatedScrollHandler,
  useComposedEventHandler,
  useSharedValue,
  type ScrollHandlerProcessed,
} from 'react-native-reanimated';

import {
  clearMissingListPosition,
  rememberListPosition,
  type ListPositionRegistry,
} from '../domain/listMemory';

export function useListPositionMemory<T>({
  active,
  itemId,
  items,
  memoryKey,
  onScroll,
  registry,
}: {
  active: boolean;
  itemId: (item: T) => string;
  items: readonly T[];
  memoryKey: string;
  onScroll?: ScrollHandlerProcessed;
  registry: ListPositionRegistry;
}) {
  const scrollOffset = useSharedValue(0);
  const captureScrollOffset = useAnimatedScrollHandler({
    onScroll(event) {
      if (!active) return;
      const maximumY = Math.max(0, event.contentSize.height - event.layoutMeasurement.height);
      scrollOffset.value = Math.max(0, Math.min(maximumY, event.contentOffset.y));
    },
  }, [active, scrollOffset]);
  const composedOnScroll = useComposedEventHandler([onScroll ?? null, captureScrollOffset]);
  const listRef = useRef<FlatList<T>>(null);
  const itemIds = useMemo(() => items.map(itemId), [itemId, items]);
  const itemIdRef = useRef(itemId);
  const itemIdsRef = useRef(itemIds);
  const memoryKeyRef = useRef(memoryKey);
  const activeRef = useRef(active);
  const wasActiveRef = useRef(active);
  itemIdRef.current = itemId;
  itemIdsRef.current = itemIds;
  memoryKeyRef.current = memoryKey;
  activeRef.current = active;
  const latestOffsetRef = useRef(registry.get(memoryKey)?.offset ?? 0);
  const expectedAnchorRef = useRef<string | null>(null);
  const restoringRef = useRef(false);
  const restorationActivationRef = useRef<string | null>(null);
  const offsetActivationRef = useRef<string | null>(null);
  const retryFrameRef = useRef<number | null>(null);

  useEffect(() => () => {
    if (retryFrameRef.current !== null) cancelAnimationFrame(retryFrameRef.current);
  }, []);

  useEffect(() => {
    if (active) {
      wasActiveRef.current = true;
      return;
    }
    if (!wasActiveRef.current) return;
    const current = registry.get(memoryKey);
    if (current) {
      rememberListPosition(registry, memoryKey, {
        ...current,
        offset: Math.max(0, scrollOffset.value),
      });
    }
    wasActiveRef.current = false;
  }, [active, memoryKey, registry, scrollOffset]);

  useEffect(() => () => {
    if (!wasActiveRef.current) return;
    const current = registry.get(memoryKeyRef.current);
    if (!current) return;
    rememberListPosition(registry, memoryKeyRef.current, {
      ...current,
      offset: Math.max(0, scrollOffset.value),
    });
  }, [registry, scrollOffset]);

  useEffect(() => {
    if (!active) {
      restorationActivationRef.current = null;
      offsetActivationRef.current = null;
      return;
    }
    if (offsetActivationRef.current !== memoryKey) {
      scrollOffset.value = registry.get(memoryKey)?.offset ?? 0;
      offsetActivationRef.current = memoryKey;
    }
    if (!items.length || restorationActivationRef.current === memoryKey) return;
    restorationActivationRef.current = memoryKey;
    const position = clearMissingListPosition(registry, memoryKey, itemIds);
    if (!position || position.offset <= 0) return;
    latestOffsetRef.current = position.offset;
    expectedAnchorRef.current = position.firstVisibleId;
    restoringRef.current = true;
    retryFrameRef.current = requestAnimationFrame(() => {
      retryFrameRef.current = null;
      listRef.current?.scrollToOffset({ animated: false, offset: position.offset });
    });
  }, [active, itemIds, items.length, memoryKey, registry, scrollOffset]);

  const rememberOffset = useCallback((event: NativeSyntheticEvent<NativeScrollEvent>) => {
    const offset = Math.max(0, event.nativeEvent.contentOffset.y);
    latestOffsetRef.current = offset;
    const current = registry.get(memoryKey);
    rememberListPosition(registry, memoryKey, {
      firstVisibleId: current?.firstVisibleId ?? null,
      offset,
    });
  }, [memoryKey, registry]);

  const onScrollBeginDrag = useCallback(() => {
    restoringRef.current = false;
    expectedAnchorRef.current = null;
    if (retryFrameRef.current !== null) {
      cancelAnimationFrame(retryFrameRef.current);
      retryFrameRef.current = null;
    }
  }, []);

  const onViewableItemsChanged = useRef(({ viewableItems }: { viewableItems: ViewToken<T>[] }) => {
    if (!activeRef.current) return;
    const first = viewableItems.find((token) => token.isViewable && token.item !== undefined);
    if (!first) return;
    const firstId = itemIdRef.current(first.item);
    const expected = expectedAnchorRef.current;
    if (restoringRef.current && expected) {
      if (viewableItems.some((token) => token.isViewable && token.item !== undefined && itemIdRef.current(token.item) === expected)) {
        restoringRef.current = false;
        expectedAnchorRef.current = null;
      } else {
        const index = itemIdsRef.current.indexOf(expected);
        if (index >= 0) {
          listRef.current?.scrollToIndex({ animated: false, index, viewPosition: 0 });
          return;
        }
        registry.delete(memoryKeyRef.current);
        restoringRef.current = false;
        expectedAnchorRef.current = null;
        latestOffsetRef.current = 0;
      }
    }
    rememberListPosition(registry, memoryKeyRef.current, {
      firstVisibleId: firstId,
      offset: latestOffsetRef.current,
    });
  }).current;

  const onScrollToIndexFailed = useCallback((info: {
    averageItemLength: number;
    highestMeasuredFrameIndex: number;
    index: number;
  }) => {
    if (!restoringRef.current || !expectedAnchorRef.current) return;
    listRef.current?.scrollToOffset({
      animated: false,
      offset: Math.max(0, info.averageItemLength * info.index),
    });
    retryFrameRef.current = requestAnimationFrame(() => {
      retryFrameRef.current = null;
      if (!restoringRef.current) return;
      listRef.current?.scrollToIndex({ animated: false, index: info.index, viewPosition: 0 });
    });
  }, []);

  return {
    listRef,
    onMomentumScrollEnd: rememberOffset,
    onScroll: composedOnScroll,
    onScrollBeginDrag,
    onScrollEndDrag: rememberOffset,
    onScrollToIndexFailed,
    onViewableItemsChanged,
  };
}
