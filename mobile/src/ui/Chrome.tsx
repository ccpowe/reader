import type { Icon as TablerIcon } from '@tabler/icons-react-native';
import type { ReactNode } from 'react';
import { Pressable, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Icon } from './Icon';
import { Text } from './Text';

/**
 * List page header (DESIGN.md 3.4: qp-h2 + qp-desc + buttons on the right): page title, an
 * optional line below it (e.g. the subscription being filtered), actions as icon / ghost buttons.
 */
export function PageHeader({ title, detail, actions }: { title: string; detail?: ReactNode; actions?: ReactNode }) {
  return (
    <View className="px-4 pb-2 pt-3">
      <View className="min-h-8 flex-row items-center gap-1">
        <Text className="flex-1" level="h2" numberOfLines={1}>
          {title}
        </Text>
        {actions}
      </View>
      {detail ? <View className="mt-0.5">{detail}</View> : null}
    </View>
  );
}

/** Top bar of a pushed page (article, board item): back + context + actions, --layout-topbar-height. */
export function TopBar({ leading, title, actions }: { leading?: ReactNode; title?: string; actions?: ReactNode }) {
  return (
    <View className="h-11 flex-row items-center gap-1 border-b border-subtle px-2">
      {leading}
      <Text className="flex-1" numberOfLines={1} tone="secondary">
        {title}
      </Text>
      {actions}
    </View>
  );
}

export type TabItem<K extends string> = { key: K; label: string; icon: TablerIcon };

/**
 * Bottom tab bar (mobile; quietpaper has none). Sidebar surface, hairline on top; the current
 * tab is shown by strong icon and primary text only — no accent, no bar, no bold (D4, D12).
 */
export function TabBar<K extends string>({
  tabs,
  current,
  onSelect,
}: {
  tabs: TabItem<K>[];
  current: K;
  onSelect?: (key: K) => void;
}) {
  const insets = useSafeAreaInsets();
  return (
    <View className="flex-row border-t border-subtle bg-sidebar" style={{ paddingBottom: insets.bottom }}>
      {tabs.map((tab) => {
        const active = tab.key === current;
        return (
          <Pressable
            accessibilityRole="tab"
            accessibilityState={{ selected: active }}
            className="h-14 flex-1 items-center justify-center gap-0.5 active:bg-pressed"
            key={tab.key}
            onPress={() => onSelect?.(tab.key)}
          >
            <Icon icon={tab.icon} size="tab" tone={active ? 'strong' : 'neutral'} />
            <Text level="caption" tone={active ? 'primary' : 'tertiary'}>
              {tab.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}
