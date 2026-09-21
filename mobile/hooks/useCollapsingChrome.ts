import { useCallback, useEffect } from 'react';
import {
  cancelAnimation,
  Easing,
  useAnimatedScrollHandler,
  useAnimatedStyle,
  useSharedValue,
  withTiming,
  type SharedValue,
} from 'react-native-reanimated';

/** Chrome follows scroll distance; the list viewport itself never moves. */
export function useCollapsingChrome(
  active: boolean,
  progress: SharedValue<number>,
  pageKey?: string | null,
  { height = 150, locked = false, scrollOffset }: { height?: number; locked?: boolean; scrollOffset?: SharedValue<number> } = {},
) {
  const previousY = useSharedValue(-1);
  const scrolling = useSharedValue(false);
  const canCollapse = useSharedValue(false);
  const travel = Math.max(1, height);
  const revealChrome = useCallback(() => {
    previousY.value = -1;
    scrolling.value = false;
    cancelAnimation(progress);
    progress.value = withTiming(0, { duration: 140, easing: Easing.out(Easing.cubic) });
  }, [previousY, progress, scrolling]);
  useEffect(() => { if (active) revealChrome(); }, [active, locked, pageKey, revealChrome]);

  const settle = () => {
    'worklet';
    if (!active) return;
    const destination = !locked && canCollapse.value && previousY.value >= travel && progress.value >= 0.5 ? 1 : 0;
    progress.value = withTiming(destination, { duration: 140, easing: Easing.out(Easing.cubic) });
  };
  const onScroll = useAnimatedScrollHandler({
    onBeginDrag(event) {
      if (!active) return;
      cancelAnimation(progress);
      previousY.value = Math.max(0, event.contentOffset.y);
      scrolling.value = true;
      canCollapse.value = event.contentSize.height - event.layoutMeasurement.height > travel;
    },
    onScroll(event) {
      if (!active) return;
      const maximumY = Math.max(0, event.contentSize.height - event.layoutMeasurement.height);
      const nextY = Math.max(0, Math.min(maximumY, event.contentOffset.y));
      if (scrollOffset) scrollOffset.value = nextY;
      const previous = previousY.value;
      previousY.value = nextY;
      canCollapse.value = maximumY > travel;
      if (locked || !canCollapse.value || nextY <= 0) {
        cancelAnimation(progress);
        progress.value = 0;
        return;
      }
      // Layout/restoration events and hidden pager pages must not become gestures.
      if (!scrolling.value || previous < 0) return;
      cancelAnimation(progress);
      progress.value = Math.max(0, Math.min(1, nextY / travel, progress.value + (nextY - previous) / travel));
    },
    onEndDrag() {
      if (!active) return;
      scrolling.value = false;
      settle();
    },
    onMomentumBegin() {
      if (!active) return;
      cancelAnimation(progress);
      scrolling.value = true;
    },
    onMomentumEnd() {
      if (!active) return;
      scrolling.value = false;
      settle();
    },
  }, [active, locked, scrollOffset, travel]);
  return { onScroll, revealChrome };
}

export function useChromeStyle(progress: SharedValue<number>, height: number) {
  return useAnimatedStyle(() => ({ transform: [{ translateY: -height * progress.value }] }));
}
