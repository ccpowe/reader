import type { ReactNode } from 'react';
import { Pressable, View } from 'react-native';

import { cn } from '@/lib/cn';

import { Text } from './Text';

/**
 * A list row (qp-row on a phone, old app scale P25): leading graphic, a label with an optional
 * second line under it, trailing hint / count / control. 24 from the screen edge, at least 56
 * high. Pressed = state-pressed overlay. Labels ellipsize; trailing never shrinks.
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
  inset = 'page',
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
  /** inside a sheet: 16 from the edge */
  inset?: 'page' | 'sheet';
}) {
  return (
    <Pressable
      accessibilityRole={onPress ? 'button' : undefined}
      className={cn('min-h-14 flex-row items-center gap-3 py-3 active:bg-pressed', inset === 'page' ? 'px-6' : 'px-4', className)}
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
            <Text className="flex-1" level="desc" numberOfLines={1} tone={detailTone}>
              {detail}
            </Text>
          </View>
        ) : null}
      </View>
      {trailing ? <View className="shrink-0 flex-row items-center gap-2">{trailing}</View> : null}
    </Pressable>
  );
}

/**
 * qp-sidebar__heading on a phone list: desc size, secondary (it names the group you operate in);
 * `aside` on the right, e.g. a count. `inset` sheet = 16 from the edge.
 */
export function SectionHeading({ title, aside, inset = 'page', className }: { title: string; aside?: string; inset?: 'page' | 'sheet'; className?: string }) {
  return (
    <View className={cn('flex-row items-center pb-2 pt-6', inset === 'page' ? 'px-6' : 'px-4', className)}>
      <Text className="flex-1" level="desc" numberOfLines={1} tone="secondary">
        {title}
      </Text>
      {aside ? (
        <Text level="caption" tone="tertiary">
          {aside}
        </Text>
      ) : null}
    </View>
  );
}

/** qp-divider; `inset` keeps the page's 24 side margin, like the old app's separators. */
export function Divider({ inset = false }: { inset?: boolean }) {
  return <View className={cn('h-px bg-subtle', inset ? 'mx-6' : '')} />;
}
