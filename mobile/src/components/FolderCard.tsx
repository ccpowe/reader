/**
 * A folder on the subscriptions page, old app frame (device screenshot): a large rounded card
 * with the count, the folder name, a few source names, and a cluster of up to four source icons
 * (+N for the rest). Quietpaper surface: tinted, no border, no shadow. When subscriptions in the
 * folder have problems, one line says how many (R4-B; the details are on the rows inside).
 */
import { IconAlertCircle } from '@tabler/icons-react-native';
import { Pressable, View } from 'react-native';

import { Icon, Text } from '@/ui';

import { SourceMark } from './FeedCard';
import type { SubscriptionItem } from './SubscriptionRow';

const spots = [
  { left: 0, top: 4 },
  { left: 48, top: 0 },
  { left: 22, top: 30 },
  { left: 56, top: 44 },
];

export function FolderCard({ name, items, onPress }: { name: string; items: SubscriptionItem[]; onPress?: () => void }) {
  const problems = items.filter((item) => item.health.kind !== 'ok').length;
  const sample = items.slice(0, 2).map((item) => item.name).join('、') + (items.length > 2 ? ' 等' : '');
  const shown = items.slice(0, spots.length);
  const rest = items.length - shown.length;
  return (
    <Pressable accessibilityRole="button" className="mx-4 flex-row items-center gap-3 rounded-3xl bg-panel px-6 py-4 active:bg-pressed" onPress={onPress}>
      <View className="min-w-0 flex-1 gap-1">
        <Text level="caption" tone="tertiary">
          {items.length} 个订阅源
        </Text>
        <Text level="h2" numberOfLines={1}>
          {name}
        </Text>
        <Text level="desc" numberOfLines={2} tone="secondary">
          {sample}
        </Text>
        {problems > 0 ? (
          <View className="mt-1 flex-row items-center gap-1">
            <Icon icon={IconAlertCircle} size="xs" tone="error" />
            <Text level="desc" tone="secondary">
              {problems} 个订阅源有问题
            </Text>
          </View>
        ) : null}
      </View>
      <View style={{ width: 100, height: 88 }}>
        {shown.map((item, index) => (
          <View className="absolute rounded-xl border-2 border-panel" key={item.id} style={spots[index]}>
            <SourceMark avatarUrl={item.avatarUrl} kind={item.kind} raised size={40} />
          </View>
        ))}
        {rest > 0 ? (
          <Text className="absolute bottom-0 left-0" level="caption" tone="tertiary">
            +{rest}
          </Text>
        ) : null}
      </View>
    </Pressable>
  );
}
