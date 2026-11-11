import { Pressable, ScrollView } from 'react-native';

import { cn } from '@/lib/cn';

import { Text } from './Text';
import { hitSlopFor } from './theme';

/**
 * qp-tab (segmented, pill): switching between sibling views — folders on Today, boards.
 * Selected = state-selected background + primary text; never accent, underline or bold (D4, D12).
 */
export function Tabs<T extends string | null>({
  options,
  selected,
  onSelect,
}: {
  options: { key: T; label: string }[];
  selected: T;
  onSelect: (key: T) => void;
}) {
  return (
    <ScrollView
      contentContainerClassName="items-center gap-px px-4"
      horizontal
      showsHorizontalScrollIndicator={false}
    >
      {options.map((option) => {
        const active = option.key === selected;
        return (
          <Pressable
            accessibilityRole="tab"
            accessibilityState={{ selected: active }}
            className={cn('h-7 items-center justify-center rounded-full px-3 active:bg-pressed', active ? 'bg-selected' : '')}
            hitSlop={hitSlopFor(28)}
            key={String(option.key)}
            onPress={() => onSelect(option.key)}
          >
            <Text tone={active ? 'primary' : 'secondary'}>{option.label}</Text>
          </Pressable>
        );
      })}
    </ScrollView>
  );
}
