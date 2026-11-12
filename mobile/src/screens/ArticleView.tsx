/**
 * Reader page shell (PRODUCT §3.6), old app layout (device screenshot), P23 R3-A: a top bar —
 * back, "文章", save, share, open in browser — above the content; a thin load progress line; one
 * in-place problem line; on the right edge a floating pill with the page's tools (translation
 * original ↔ bilingual P24, reader mode P13 / player ↔ web page). The content (a WebView) is the
 * `children`. Presentational: the route passes state and callbacks.
 */
import type { Icon as TablerIcon } from '@tabler/icons-react-native';
import { IconArrowLeft, IconBookmark, IconBookmarkFilled, IconExternalLink, IconShare2 } from '@tabler/icons-react-native';
import { useState, type ReactNode } from 'react';
import { Pressable, View } from 'react-native';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';
import Animated, { useAnimatedStyle, useSharedValue } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { scheduleOnRN } from 'react-native-worklets';

import { cn } from '@/lib/cn';
import { clampPosition, toolsPosition } from '@/lib/reader-tools-position';
import { useMountEffect } from '@/lib/use-mount-effect';
import { Icon, IconButton, Message, TopBar, useShadow } from '@/ui';
import { t } from '@/lib/i18n';

export type ReaderTool = { key: string; icon: TablerIcon; label: string; on: boolean; onPress?: () => void };

/**
 * The floating tools on the right edge (P23 R3-A, old app): drag the pill up or down; the place is
 * remembered on this device. Screen readers move it with the increment / decrement actions.
 */
function FloatingTools({ tools }: { tools: ReaderTool[] }) {
  const shadow = useShadow('md');
  const [area, setArea] = useState(0);
  const [pill, setPill] = useState(0);
  const travel = Math.max(0, area - pill);
  const position = useSharedValue(toolsPosition.get());
  const start = useSharedValue(0);
  useMountEffect(() =>
    toolsPosition.subscribe((remembered) => {
      position.set(remembered);
    }),
  );
  const remember = (value: number) => toolsPosition.set(value);
  // A vertical drag moves it; a tap still reaches the buttons.
  const drag = Gesture.Pan()
    .activeOffsetY([-6, 6])
    .failOffsetX([-12, 12])
    .onBegin(() => {
      start.set(position.get());
    })
    .onUpdate((event) => {
      if (travel > 0) position.set(Math.max(0, Math.min(1, start.get() + event.translationY / travel)));
    })
    .onFinalize(() => {
      scheduleOnRN(remember, position.get());
    });
  const placed = useAnimatedStyle(() => ({ top: position.get() * travel }));
  function moveBy(delta: number) {
    const next = clampPosition(position.get() + delta);
    position.set(next);
    toolsPosition.set(next);
  }
  if (tools.length === 0) return null;
  return (
    <View className="absolute inset-0" onLayout={(event) => setArea(event.nativeEvent.layout.height)} pointerEvents="box-none">
      <GestureDetector gesture={drag}>
        <Animated.View
          className="absolute right-2 rounded-full bg-raised p-1"
          onLayout={(event) => setPill(event.nativeEvent.layout.height)}
          style={[shadow, placed]}
          testID="align-ignore"
        >
          {tools.map((tool) => (
            <Pressable
              accessibilityActions={[
                { name: 'increment', label: t().reader.moveToolsDown },
                { name: 'decrement', label: t().reader.moveToolsUp },
              ]}
              accessibilityHint={t().reader.dragToolsHint}
              accessibilityLabel={tool.label}
              accessibilityRole="switch"
              accessibilityState={{ checked: tool.on }}
              className={cn('h-12 w-12 items-center justify-center rounded-full active:bg-pressed', tool.on ? 'bg-selected' : '')}
              key={tool.key}
              onAccessibilityAction={(event) => moveBy(event.nativeEvent.actionName === 'increment' ? 0.1 : -0.1)}
              onPress={tool.onPress}
            >
              <Icon icon={tool.icon} size="toolbar" tone={tool.on ? 'strong' : 'neutral'} />
            </Pressable>
          ))}
        </Animated.View>
      </GestureDetector>
    </View>
  );
}

export function ArticleView({
  title = t().reader.article,
  saved,
  progress,
  problem,
  tools = [],
  children,
  onBack,
  onToggleSave,
  onShare,
  onOpenExternal,
}: {
  /** 文章, 视频 or 帖子 (old app) */
  title?: string;
  saved: boolean;
  /** page load progress 0–1; hidden at 1 */
  progress?: number;
  /** one line about what went wrong, with an action */
  problem?: { message: string; action?: ReactNode } | null;
  tools?: ReaderTool[];
  children: ReactNode;
  onBack?: () => void;
  onToggleSave?: () => void;
  onShare?: () => void;
  onOpenExternal?: () => void;
}) {
  const insets = useSafeAreaInsets();
  return (
    <View className="flex-1 bg-canvas" style={{ paddingTop: insets.top }}>
      <TopBar
        actions={
          <>
            <IconButton icon={saved ? IconBookmarkFilled : IconBookmark} label={saved ? t().common.unbookmark : t().common.bookmark} on={saved} onPress={onToggleSave} plain />
            <IconButton icon={IconShare2} label={t().reader.share} onPress={onShare} />
            <IconButton icon={IconExternalLink} label={t().reader.openExternal} onPress={onOpenExternal} />
          </>
        }
        leading={<IconButton icon={IconArrowLeft} label={t().common.back} onPress={onBack} />}
        title={title}
      />
      <View className="h-0.5 bg-sidebar">
        {progress !== undefined && progress < 1 ? <View className="h-full bg-accent" style={{ width: `${Math.max(5, progress * 100)}%` }} /> : null}
      </View>
      {problem ? (
        <Message action={problem.action} bar kind="warning">
          {problem.message}
        </Message>
      ) : null}
      <View className="flex-1" style={{ paddingBottom: insets.bottom }}>
        {children}
        <FloatingTools tools={tools} />
      </View>
    </View>
  );
}
