import type { ReactNode } from 'react';
import { Pressable, ScrollView, View } from 'react-native';

import { cn } from '@/lib/cn';

import { Text } from './Text';

/**
 * qp-tab (segmented, pill) at the phone scale chosen in calibration (P25): folders on Today and
 * Saved, boards. Selected = state-selected pill + primary text; never accent, underline or bold
 * (D4, D12). Each tab is a full 44-high press target; the pill is drawn inside it.
 * `trailing` sits outside the scroll area (e.g. the channel filter); `bordered` draws the
 * hairline under the strip when it is the last thing above a list.
 */
export function Tabs<T extends string | null>({
  options,
  selected,
  onSelect,
  trailing,
  bordered = false,
  inset = 'page',
}: {
  options: { key: T; label: string }[];
  selected: T;
  onSelect: (key: T) => void;
  trailing?: ReactNode;
  bordered?: boolean;
  /** page: 24 from the screen edge (tab pages); sheet: 16 (inside a sheet) */
  inset?: 'page' | 'sheet';
}) {
  return (
    <View className={cn('flex-row items-center', bordered ? 'border-b border-subtle' : '')}>
      <ScrollView
        className="flex-1"
        // The first label's text lines up with the content edge (24 page / 16 sheet): the pill's
        // own 12 padding plus the tab's 2 hang out to the left of it.
        contentContainerClassName={cn('items-center', inset === 'page' ? 'px-2.5' : 'px-0.5')}
        horizontal
        showsHorizontalScrollIndicator={false}
      >
        {options.map((option) => {
          const active = option.key === selected;
          return (
            <Pressable
              accessibilityRole="tab"
              accessibilityState={{ selected: active }}
              className="h-11 justify-center px-0.5"
              key={String(option.key)}
              onPress={() => onSelect(option.key)}
            >
              {({ pressed }) => (
                <View className={cn('h-8 items-center justify-center rounded-full px-3', active ? 'bg-selected' : pressed ? 'bg-pressed' : '')}>
                  <Text tone={active ? 'primary' : 'secondary'}>{option.label}</Text>
                </View>
              )}
            </Pressable>
          );
        })}
      </ScrollView>
      {trailing ? <View className="pr-4 pl-1">{trailing}</View> : null}
    </View>
  );
}
