import type { ReactNode } from 'react';
import { Pressable, View } from 'react-native';

import { cn } from '@/lib/cn';

import { Text } from './Text';

/**
 * A list row (qp-row on a phone): leading graphic, a label (+ optional second line), trailing
 * hint / count / control. Pressed = state-pressed overlay. Single-line labels ellipsize.
 */
export function Row({
  leading,
  label,
  detail,
  detailTone = 'tertiary',
  detailIcon,
  trailing,
  onPress,
  className,
}: {
  leading?: ReactNode;
  label: string;
  /** second line: tertiary for skippable meta, secondary for text to read */
  detail?: string | null;
  detailTone?: 'tertiary' | 'secondary';
  /** status icon before the second line (status color lives on the icon, never on bare text) */
  detailIcon?: ReactNode;
  trailing?: ReactNode;
  onPress?: () => void;
  className?: string;
}) {
  return (
    <Pressable
      accessibilityRole={onPress ? 'button' : undefined}
      className={cn('min-h-12 flex-row items-center gap-3 px-4 py-2 active:bg-pressed', className)}
      disabled={!onPress}
      onPress={onPress}
    >
      {leading}
      <View className="min-w-0 flex-1">
        <Text level="body" numberOfLines={1}>
          {label}
        </Text>
        {detail ? (
          <View className="flex-row items-center gap-1">
            {detailIcon}
            <Text className="flex-1" level="caption" numberOfLines={1} tone={detailTone}>
              {detail}
            </Text>
          </View>
        ) : null}
      </View>
      {trailing ? <View className="flex-row items-center gap-1">{trailing}</View> : null}
    </Pressable>
  );
}

/** qp-sidebar__heading on a phone list: caption; `strong` when it must be read to operate. */
export function SectionHeading({ title, strong = false }: { title: string; strong?: boolean }) {
  return (
    <Text className="px-4 pb-1 pt-4" level="caption-strong" tone={strong ? 'secondary' : 'tertiary'}>
      {title}
    </Text>
  );
}

/** qp-divider */
export function Divider({ inset = false }: { inset?: boolean }) {
  return <View className={cn('h-px bg-subtle', inset ? 'ml-4' : '')} />;
}
