/**
 * The tab pages' collapsing chrome (old app, PRODUCT §2): scrolling a list down slides the page's
 * top area (header, folder tabs, search) up and the tab bar down, in step with the finger; on
 * release they settle fully open or fully closed. They open again at the top of the list, when the
 * list is too short to scroll, when the folder / board changes and when a tab is pressed.
 *
 * The list itself never moves: the top area and the tab bar lie over it, and the list's content
 * leaves room for them (useChromeInsets). The motion runs on the UI thread (Reanimated).
 *
 *   ChromeProvider        (tabs)/_layout — one progress (0 open … 1 closed) per tab, the tab bar height
 *   CollapsingScreen      a tab page — measures and moves its top area, gives its lists their insets
 *   useChromeScroll       a list in it — the scroll handler that drives the progress
 */
import { createContext, use, useState, type ReactNode } from 'react';
import { RefreshControl, View, type ScrollViewProps } from 'react-native';
import Animated, {
  cancelAnimation,
  Easing,
  useAnimatedScrollHandler,
  useAnimatedStyle,
  useSharedValue,
  withTiming,
  type SharedValue,
} from 'react-native-reanimated';
import { scheduleOnRN } from 'react-native-worklets';

import { useMountEffect } from '@/lib/use-mount-effect';

export type ChromeTab = 'index' | 'boards' | 'subscriptions' | 'saved' | 'me';

const SETTLE = { duration: 140, easing: Easing.out(Easing.cubic) };
/** How far to scroll before the chrome is fully closed when nothing is measured yet. */
const DEFAULT_TRAVEL = 150;
/** The tab bar's own height, before it is measured (a page without a top area closes over it). */
const TAB_BAR_TRAVEL = 56;

type Shell = {
  progress: Record<ChromeTab, SharedValue<number>>;
  tabBarHeight: number;
  setTabBarHeight: (height: number) => void;
  /** last measured top area per tab, so a remounted page lays out at once */
  headerHeights: Partial<Record<ChromeTab, number>>;
  setHeaderHeight: (tab: ChromeTab, height: number) => void;
};
const ShellContext = createContext<Shell | null>(null);

type Screen = {
  progress: SharedValue<number>;
  /** the measured top area's height (how far it slides) */
  headerTravel: SharedValue<number>;
  /** no top area: only the tab bar slides away, so its height is the distance (我的) */
  fixedTravel: number | null;
  /** keep the chrome open (a focused search field in it) */
  locked: boolean;
  top: number;
  bottom: number;
};
const ScreenContext = createContext<Screen | null>(null);

export function reveal(progress: SharedValue<number>) {
  cancelAnimation(progress);
  progress.value = withTiming(0, SETTLE);
}

export function ChromeProvider({ children }: { children: ReactNode }) {
  const index = useSharedValue(0);
  const boards = useSharedValue(0);
  const subscriptions = useSharedValue(0);
  const saved = useSharedValue(0);
  const me = useSharedValue(0);
  const [tabBarHeight, setTabBarHeight] = useState(0);
  const [headerHeights, setHeaderHeights] = useState<Partial<Record<ChromeTab, number>>>({});
  const shell: Shell = {
    progress: { index, boards, subscriptions, saved, me },
    tabBarHeight,
    setTabBarHeight,
    headerHeights,
    setHeaderHeight: (tab, height) =>
      setHeaderHeights((current) => (current[tab] === height ? current : { ...current, [tab]: height })),
  };
  return <ShellContext value={shell}>{children}</ShellContext>;
}

/** The tab bar, lying over the bottom of the page; it slides down as the current tab's chrome closes. */
export function CollapsingTabBar({ current, children }: { current: ChromeTab; children: ReactNode }) {
  const shell = use(ShellContext);
  const progress = shell?.progress[current];
  const height = shell?.tabBarHeight ?? 0;
  const style = useAnimatedStyle(() => ({ transform: [{ translateY: height * (progress?.value ?? 0) }] }));
  return (
    <Animated.View
      onLayout={(event) => {
        const next = Math.round(event.nativeEvent.layout.height);
        if (shell && next !== shell.tabBarHeight) shell.setTabBarHeight(next);
      }}
      style={[{ position: 'absolute', left: 0, right: 0, bottom: 0 }, style]}
    >
      {children}
    </Animated.View>
  );
}

/** Opens a tab's chrome (a tab pressed): call from the event handler. */
export function useRevealTab(): (tab: ChromeTab) => void {
  const shell = use(ShellContext);
  return (tab) => {
    if (shell) reveal(shell.progress[tab]);
  };
}

/** The room the bottom of a tab page leaves for the tab bar (toasts, floating buttons). */
export function useTabBarInset(): number {
  return use(ShellContext)?.tabBarHeight ?? 0;
}

/** Mounted under a key: opens the chrome when that key changes (another folder, board or channel). */
function RevealOnMount({ progress }: { progress: SharedValue<number> }) {
  useMountEffect(() => {
    reveal(progress);
    return () => {};
  });
  return null;
}

/**
 * A tab page: `header` is the top area that slides away, `children` the content (lists use
 * useChromeScroll / useChromeInsets). Without a header only the tab bar collapses (我的).
 */
export function CollapsingScreen({
  tab,
  header,
  revealKey,
  locked = false,
  children,
}: {
  tab: ChromeTab;
  header?: ReactNode;
  /** a change of this opens the chrome */
  revealKey?: string;
  /** keep the chrome open, e.g. while a search field in the header has the keyboard */
  locked?: boolean;
  children: ReactNode;
}) {
  const shell = use(ShellContext);
  const fallback = useSharedValue(0);
  const progress = shell?.progress[tab] ?? fallback;
  const [height, setHeight] = useState<number | undefined>(shell?.headerHeights[tab]);
  const headerTravel = useSharedValue(height || DEFAULT_TRAVEL);
  const style = useAnimatedStyle(() => ({ transform: [{ translateY: -headerTravel.value * progress.value }] }));
  const screen: Screen = {
    progress,
    headerTravel,
    fixedTravel: header ? null : shell?.tabBarHeight || TAB_BAR_TRAVEL,
    locked,
    top: header ? (height ?? 0) : 0,
    bottom: shell?.tabBarHeight ?? 0,
  };
  // The content waits for the top area's height, so a list never shifts once laid out.
  const ready = !header || height !== undefined;
  return (
    <View className="flex-1 bg-canvas">
      <RevealOnMount key={`${revealKey ?? ''}|${locked}`} progress={progress} />
      {ready ? <ScreenContext value={screen}>{children}</ScreenContext> : null}
      {header ? (
        <Animated.View
          className="bg-canvas"
          onLayout={(event) => {
            const next = Math.round(event.nativeEvent.layout.height);
            if (next === height) return;
            headerTravel.value = next;
            shell?.setHeaderHeight(tab, next);
            setHeight(next);
          }}
          style={[{ position: 'absolute', left: 0, right: 0, top: 0 }, style]}
        >
          {header}
        </Animated.View>
      ) : null}
    </View>
  );
}

/** The room a list's content leaves for the top area and the tab bar (0 outside a tab page). */
export function useChromeInsets(): { top: number; bottom: number } {
  const screen = use(ScreenContext);
  return { top: screen?.top ?? 0, bottom: screen?.bottom ?? 0 };
}

/**
 * The scroll handler of a list in a tab page (port of the old app's useCollapsingChrome). Only the
 * reader's own drags and flings move the chrome — not layout, restoring a position, or a pager's
 * hidden page (`active` false for pages other than the one shown). `onOffset` gets the offset now
 * and then and when a drag or fling ends; `onDragStart` when the reader starts dragging.
 */
export function useChromeScroll({
  onOffset,
  onDragStart,
  active = true,
}: { onOffset?: (y: number) => void; onDragStart?: () => void; active?: boolean } = {}) {
  const screen = use(ScreenContext);
  const fallback = useSharedValue(0);
  const fallbackTravel = useSharedValue(DEFAULT_TRAVEL);
  const progress = screen?.progress ?? fallback;
  const headerTravel = screen?.headerTravel ?? fallbackTravel;
  const fixedTravel = screen?.fixedTravel ?? null;
  const locked = screen?.locked ?? false;
  const collapses = screen !== null && active;
  const previousY = useSharedValue(-1);
  const scrolling = useSharedValue(false);
  const canCollapse = useSharedValue(false);
  const lastReported = useSharedValue(-1000);

  return useAnimatedScrollHandler({
    onBeginDrag(event) {
      if (!collapses) {
        if (onDragStart) scheduleOnRN(onDragStart);
        return;
      }
      cancelAnimation(progress);
      previousY.value = Math.max(0, event.contentOffset.y);
      scrolling.value = true;
      const travel = fixedTravel ?? headerTravel.value;
      canCollapse.value = !locked && event.contentSize.height - event.layoutMeasurement.height > travel;
      if (onDragStart) scheduleOnRN(onDragStart);
    },
    onScroll(event) {
      const maximumY = Math.max(0, event.contentSize.height - event.layoutMeasurement.height);
      const y = Math.max(0, Math.min(maximumY, event.contentOffset.y));
      if (onOffset && Math.abs(event.contentOffset.y - lastReported.value) >= 48) {
        lastReported.value = event.contentOffset.y;
        scheduleOnRN(onOffset, event.contentOffset.y);
      }
      if (!collapses) return;
      const travel = fixedTravel ?? headerTravel.value;
      const previous = previousY.value;
      previousY.value = y;
      canCollapse.value = !locked && maximumY > travel;
      if (!canCollapse.value || y <= 0) {
        cancelAnimation(progress);
        progress.value = 0;
        return;
      }
      // Layout and restoring a position are not the reader's gestures.
      if (!scrolling.value || previous < 0) return;
      cancelAnimation(progress);
      progress.value = Math.max(0, Math.min(1, y / travel, progress.value + (y - previous) / travel));
    },
    onEndDrag(event) {
      scrolling.value = false;
      if (onOffset) {
        lastReported.value = event.contentOffset.y;
        scheduleOnRN(onOffset, event.contentOffset.y);
      }
      if (!collapses) return;
      const travel = fixedTravel ?? headerTravel.value;
      const closed = canCollapse.value && previousY.value >= travel && progress.value >= 0.5;
      progress.value = withTiming(closed ? 1 : 0, SETTLE);
    },
    onMomentumBegin() {
      if (!collapses) return;
      cancelAnimation(progress);
      scrolling.value = true;
    },
    onMomentumEnd(event) {
      scrolling.value = false;
      if (onOffset) {
        lastReported.value = event.contentOffset.y;
        scheduleOnRN(onOffset, event.contentOffset.y);
      }
      if (!collapses) return;
      const travel = fixedTravel ?? headerTravel.value;
      const closed = canCollapse.value && previousY.value >= travel && progress.value >= 0.5;
      progress.value = withTiming(closed ? 1 : 0, SETTLE);
    },
  });
}

/** Content that is not a list (an empty or failed state) sits below the top area. */
export function ChromePadded({ children }: { children: ReactNode }) {
  const { top } = useChromeInsets();
  return (
    <View className="flex-1" style={{ paddingTop: top }}>
      {children}
    </View>
  );
}

/** A ScrollView in a tab page whose header and tab bar slide away as it scrolls. */
export function ChromeScrollView({
  children,
  refreshing,
  onRefresh,
  refreshColor,
  contentContainerClassName,
  keyboardShouldPersistTaps,
  extraBottom = 0,
}: {
  children: ReactNode;
  /** more room at the end, e.g. for a floating button */
  extraBottom?: number;
  refreshing?: boolean;
  onRefresh?: () => void;
  refreshColor?: string;
  contentContainerClassName?: string;
  keyboardShouldPersistTaps?: ScrollViewProps['keyboardShouldPersistTaps'];
}) {
  const insets = useChromeInsets();
  const onScroll = useChromeScroll();
  return (
    <Animated.ScrollView
      contentContainerClassName={contentContainerClassName}
      contentContainerStyle={{ paddingTop: insets.top, paddingBottom: insets.bottom + extraBottom }}
      keyboardShouldPersistTaps={keyboardShouldPersistTaps}
      onScroll={onScroll}
      refreshControl={
        onRefresh ? (
          <RefreshControl
            colors={refreshColor ? [refreshColor] : undefined}
            onRefresh={onRefresh}
            progressViewOffset={insets.top}
            refreshing={refreshing ?? false}
            tintColor={refreshColor}
          />
        ) : undefined
      }
      scrollEventThrottle={16}
    >
      {children}
    </Animated.ScrollView>
  );
}
