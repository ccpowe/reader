import type { Icon as TablerIcon } from '@tabler/icons-react-native';
import type { ReactNode } from 'react';
import { Image, Pressable, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Avatar } from './Avatar';
import { Icon } from './Icon';
import { Text } from './Text';
import { useShadow } from './useShadow';

const logo = require('../../assets/reader-logo.png');

/**
 * Tab page header, old app layout (P25): a brand row (logo, READER, the user's avatar), then the
 * page title at 34 with one thing on its right — the date, or a toolbar button. Side margin 24.
 */
export function PageHeader({
  title,
  aside,
  user,
  onPressUser,
}: {
  title: string;
  /** right of the title: a date line or icon buttons */
  aside?: ReactNode;
  user?: { name: string; avatarUrl?: string | null };
  onPressUser?: () => void;
}) {
  return (
    <View className="px-6 pt-2">
      <View className="h-12 flex-row items-center justify-between">
        <View className="flex-row items-center gap-2">
          <Image accessibilityIgnoresInvertColors className="rounded-lg" source={logo} style={{ width: 32, height: 32 }} />
          <Text className="tracking-widest" level="caption-strong">
            READER
          </Text>
        </View>
        {user ? (
          <Pressable accessibilityLabel="我的" accessibilityRole="button" hitSlop={4} onPress={onPressUser}>
            <Avatar name={user.name} size={36} uri={user.avatarUrl} />
          </Pressable>
        ) : null}
      </View>
      <View className="h-14 flex-row items-center gap-2">
        <Text className="flex-1" level="page" numberOfLines={1}>
          {title}
        </Text>
        {typeof aside === 'string' ? (
          <Text level="desc" tone="secondary">
            {aside}
          </Text>
        ) : (
          aside
        )}
      </View>
    </View>
  );
}

/** Top bar of a pushed page (article, board item): back, a short context word, actions. */
export function TopBar({ leading, title, actions }: { leading?: ReactNode; title?: string; actions?: ReactNode }) {
  return (
    <View className="h-14 flex-row items-center gap-3 bg-sidebar px-4">
      {leading}
      <Text className="flex-1" level="section" numberOfLines={1}>
        {title}
      </Text>
      {actions ? <View className="flex-row items-center gap-4">{actions}</View> : null}
    </View>
  );
}

export type TabItem<K extends string> = { key: K; label: string; icon: TablerIcon };

/**
 * Bottom tab bar: icons only, like the old app (P25); labels are accessibility labels. Page
 * background, hairline on top; the current tab is the strong icon — no accent, no bar (D4, D12).
 * It owns the bottom safe area, so nothing above it adds one.
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
    <View className="flex-row border-t border-subtle bg-canvas" style={{ paddingBottom: insets.bottom }}>
      {tabs.map((tab) => {
        const active = tab.key === current;
        return (
          <Pressable
            accessibilityLabel={tab.label}
            accessibilityRole="tab"
            accessibilityState={{ selected: active }}
            className="h-14 flex-1 items-center justify-center active:bg-pressed"
            key={tab.key}
            onPress={() => onSelect?.(tab.key)}
          >
            <Icon icon={tab.icon} size="tab" tone={active ? 'strong' : 'neutral'} />
          </Pressable>
        );
      })}
    </View>
  );
}

/** Floating add button (subscriptions): the one primary action of the page. */
export function Fab({ icon, label, onPress }: { icon: TablerIcon; label: string; onPress?: () => void }) {
  const shadow = useShadow('lg');
  return (
    <Pressable
      accessibilityLabel={label}
      accessibilityRole="button"
      className="absolute bottom-6 right-6 h-14 w-14 items-center justify-center rounded-full bg-accent active:bg-accent-pressed"
      onPress={onPress}
      style={shadow}
    >
      <Icon icon={icon} size="toolbar" tone="on-accent" />
    </Pressable>
  );
}
