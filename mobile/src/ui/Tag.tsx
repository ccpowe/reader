import type { ReactNode } from 'react';
import { View } from 'react-native';

import { cn } from '@/lib/cn';

import { Text } from './Text';

/** qp-tag (gray): a category or a count that must be read. `status` = qp-status pill shape. */
export function Tag({ children, status = false }: { children: ReactNode; status?: boolean }) {
  return (
    <View className={cn('h-6 flex-row items-center bg-tag-gray-bg px-1.5', status ? 'rounded-xl' : 'rounded-sm')}>
      <Text className="text-tag-gray-text" level="ui">
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

/**
 * A count on a solid accent pill (old app: channel picker, filter button). Only where the number
 * is the point of the control; elsewhere use Count. Shows 999+ past 999.
 */
export function SolidCount({ value }: { value: number }) {
  return (
    <View className="h-6 min-w-6 items-center justify-center rounded-full bg-accent px-1.5">
      <Text level="caption-strong" numberOfLines={1} style={{ fontVariant: ['tabular-nums'] }} tone="on-accent">
        {value > 999 ? '999+' : value}
      </Text>
    </View>
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
