import type { ReactNode } from 'react';
import { View } from 'react-native';

import { cn } from '@/lib/cn';

import { Text } from './Text';

/** qp-tag (gray): a category or a count that must be read. `status` = qp-status pill shape. */
export function Tag({ children, status = false }: { children: ReactNode; status?: boolean }) {
  return (
    <View className={cn('h-5 flex-row items-center px-1 bg-tag-gray-bg', status ? 'rounded-xl' : 'rounded-sm')}>
      <Text className="text-tag-gray-text" level="caption">
        {children}
      </Text>
    </View>
  );
}

/** qp-count: a number nobody has to read (tertiary). Counts that need action use AttentionCount. */
export function Count({ value }: { value: number }) {
  return (
    <Text level="caption" style={{ fontVariant: ['tabular-nums'] }} tone="tertiary">
      {value}
    </Text>
  );
}

/** D18: the only colored count — something the user should deal with. */
export function AttentionCount({ value }: { value: number }) {
  return (
    <View className="h-5 min-w-5 items-center justify-center rounded-xl bg-danger-bg px-1.5">
      <Text level="caption" tone="danger">
        {value}
      </Text>
    </View>
  );
}

/** qp-badge: micro label on a sunken background, e.g. "共 7 条". */
export function Badge({ children }: { children: ReactNode }) {
  return (
    <View className="self-start rounded-sm bg-sunken px-1">
      <Text level="micro" tone="secondary">
        {children}
      </Text>
    </View>
  );
}

/** qp-dot: status dot, always next to text; gray or green only (D12). */
export function Dot({ tone = 'gray' }: { tone?: 'gray' | 'green' }) {
  return (
    <View className={cn('h-2 w-2 rounded-full', tone === 'green' ? 'bg-dot-green' : 'bg-dot-gray')} />
  );
}
