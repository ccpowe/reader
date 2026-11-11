import type { ReactNode } from 'react';
import { ActivityIndicator, View } from 'react-native';
import Animated, { useReducedMotion } from 'react-native-reanimated';
import { useCSSVariable } from 'uniwind';

import { cn } from '@/lib/cn';

import { Text } from './Text';
import { duration, iconSize } from './theme';
import { useShadow } from './useShadow';

/**
 * qp-skeleton: placeholder lines while a list or preview loads (never a full-page spinner).
 * --duration-shimmer (1000ms) as a gentle pulse (RN has no gradient shimmer without a library);
 * static when the system asks for reduced motion. A CSS animation: no effect, no state.
 */
const pulse = {
  animationName: { from: { opacity: 1 }, to: { opacity: 0.5 } },
  animationDuration: '500ms',
  animationIterationCount: 'infinite',
  animationDirection: 'alternate',
} as const;

export function Skeleton({ lines = 2, className }: { lines?: number; className?: string }) {
  const reduceMotion = useReducedMotion();
  return (
    <Animated.View className={cn('gap-2', className)} style={reduceMotion ? undefined : pulse}>
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
  const shadow = useShadow('lg');
  return (
    <View className="mx-6 max-w-full flex-row items-center gap-2 self-center rounded-xl bg-raised px-3 py-1.5" style={shadow}>
      <Text className="shrink" numberOfLines={2}>
        {message}
      </Text>
      {action}
    </View>
  );
}

export { duration };
