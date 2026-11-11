import { useEffect, useRef, type ReactNode } from 'react';
import { ActivityIndicator, Animated, View } from 'react-native';
import { useCSSVariable } from 'uniwind';

import { cn } from '@/lib/cn';

import { Text } from './Text';
import { duration, iconSize } from './theme';

/** qp-skeleton: placeholder lines while a list or preview loads (never a full-page spinner). */
export function Skeleton({ lines = 2, className }: { lines?: number; className?: string }) {
  const opacity = useRef(new Animated.Value(1)).current;
  useEffect(() => {
    // --duration-shimmer (1000ms) as a gentle pulse; RN has no gradient shimmer without a library.
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, { toValue: 0.5, duration: 500, useNativeDriver: true }),
        Animated.timing(opacity, { toValue: 1, duration: 500, useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [opacity]);
  return (
    <Animated.View className={cn('gap-2', className)} style={{ opacity }}>
      {Array.from({ length: lines }, (_, index) => (
        <View
          className="h-4 rounded-sm bg-sunken"
          key={index}
          style={{ width: index === lines - 1 && lines > 1 ? '60%' : '100%' }}
        />
      ))}
    </Animated.View>
  );
}

/** Spinner for work a button started (qp-spinner); shown only past --delay-loading by the caller. */
export function Spinner() {
  const color = useCSSVariable('--color-icon-neutral') as string | undefined;
  return <ActivityIndicator color={color} size={iconSize.sm} />;
}

/** qp-empty: "还没有 + 名词" plus at most one way forward. */
export function EmptyState({ title, description, action }: { title: string; description?: string; action?: ReactNode }) {
  return (
    <View className="items-center gap-2 px-4 py-8">
      <Text level="body" tone="secondary">
        {title}
      </Text>
      {description ? (
        <Text className="text-center" level="desc" tone="secondary">
          {description}
        </Text>
      ) : null}
      {action ? <View className="mt-1">{action}</View> : null}
    </View>
  );
}

/** qp-toast: a result outside the viewport, with Undo when it can be undone. */
export function Toast({ message, action }: { message: string; action?: ReactNode }) {
  return (
    <View className="flex-row items-center gap-2 self-center rounded-xl bg-raised px-3 py-1.5 shadow-lg">
      <Text numberOfLines={1}>{message}</Text>
      {action}
    </View>
  );
}

export { duration };
