/**
 * A subscription inside a folder, old app frame (checked on the device, P26): its icon, name,
 * address, and a status line — "同步正常", or the problem in its place (P23 R4-B) with the status
 * color on a small icon only (DESIGN 4). A chevron opens it; ⋮ holds move / rename / unsubscribe.
 * Tapping a row with a problem expands the explanation.
 */
import { IconAlertCircle, IconAlertTriangle, IconChevronRight, IconDotsVertical } from '@tabler/icons-react-native';
import { Pressable, View } from 'react-native';

import { Callout, Icon, IconButton, Text } from '@/ui';

import { SourceMark, type SourceKind } from './FeedCard';

export type SubscriptionHealth =
  | { kind: 'ok' }
  | { kind: 'failing'; reason: string; since: string }
  | { kind: 'gap'; detail: string };

export type SubscriptionItem = {
  id: string;
  name: string;
  kind: SourceKind;
  /** the feed / channel / account address, shown without the scheme */
  address: string;
  newCount: number;
  health: SubscriptionHealth;
  avatarUrl?: string | null;
};

export function healthLine(health: SubscriptionHealth): string {
  if (health.kind === 'failing') return `扫描失败 · ${health.reason} · ${health.since}`;
  if (health.kind === 'gap') return `可能有遗漏 · ${health.detail}`;
  return '同步正常';
}

export function SubscriptionRow({
  item,
  expanded = false,
  onPress,
  onMenu,
}: {
  item: SubscriptionItem;
  expanded?: boolean;
  onPress?: () => void;
  onMenu?: () => void;
}) {
  const problem = item.health.kind !== 'ok';
  return (
    <View className="mx-6 border-b border-subtle">
      <Pressable accessibilityRole="button" className="flex-row items-center gap-3 py-4 active:bg-pressed" onPress={onPress}>
        <SourceMark avatarUrl={item.avatarUrl} kind={item.kind} size={44} />
        <View className="min-w-0 flex-1">
          <Text level="body-strong" numberOfLines={1}>
            {item.name}
          </Text>
          <Text level="desc" numberOfLines={1} tone="secondary">
            {item.address}
          </Text>
          <View className="flex-row items-center gap-1">
            {item.health.kind === 'failing' ? <Icon icon={IconAlertCircle} size="xs" tone="error" /> : null}
            {item.health.kind === 'gap' ? <Icon icon={IconAlertTriangle} size="xs" tone="warning" /> : null}
            <Text className="flex-1" level="desc" numberOfLines={1} tone={problem ? 'secondary' : 'tertiary'}>
              {healthLine(item.health)}
            </Text>
          </View>
        </View>
        <Icon icon={IconChevronRight} />
        <IconButton icon={IconDotsVertical} label={`${item.name} 的更多操作`} onPress={onMenu} size="sm" />
      </Pressable>
      {expanded && problem ? (
        <View className="pb-4">
          <Callout>
            <Text>{healthLine(item.health)}</Text>
            <Text className="mt-1" level="desc" tone="secondary">
              {item.health.kind === 'failing'
                ? 'Reader 会自动重试；一直失败时可以检查地址，或取消订阅。'
                : '下次扫描不会补上更早的内容；需要的话去原网站看。'}
            </Text>
          </Callout>
        </View>
      ) : null}
    </View>
  );
}
