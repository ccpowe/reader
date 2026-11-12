/**
 * A subscription inside a folder, old app frame (checked on the device, P26): its icon, name,
 * address, and a status line — "同步正常", or the problem in its place (P23 R4-B) with the status
 * color on a small icon only (DESIGN 4). The row opens the subscription's own page (P30); ⋮ holds
 * rename / move / 全部 / unsubscribe. Tapping the line of a row with a problem expands the explanation.
 */
import {
  IconAlertCircle,
  IconAlertTriangle,
  IconChevronRight,
  IconDotsVertical,
  IconPlayerPause,
} from '@tabler/icons-react-native';
import { Pressable, View } from 'react-native';

import { hasProblem, healthLine, type SubscriptionHealth, type SubscriptionItem } from '@/lib/subscriptions';
import { Callout, Icon, IconButton, Text } from '@/ui';

import { SourceMark } from './FeedCard';

export type { SubscriptionHealth, SubscriptionItem };

function explanation(health: SubscriptionHealth): string {
  return health.kind === 'failing'
    ? 'Reader 会自动重试；一直失败时可以检查地址，或取消订阅。'
    : '下次扫描不会补上更早的内容；需要的话去原网站看。';
}

export function SubscriptionRow({
  item,
  expanded = false,
  onPress,
  onToggleDetail,
  onMenu,
}: {
  item: SubscriptionItem;
  expanded?: boolean;
  onPress?: () => void;
  onToggleDetail?: () => void;
  onMenu?: () => void;
}) {
  const problem = hasProblem(item.health);
  const line = (
    <View className="flex-row items-center gap-1">
      {item.health.kind === 'failing' ? <Icon icon={IconAlertCircle} size="xs" tone="error" /> : null}
      {item.health.kind === 'gap' ? <Icon icon={IconAlertTriangle} size="xs" tone="warning" /> : null}
      {item.health.kind === 'paused' ? <Icon icon={IconPlayerPause} size="xs" /> : null}
      <Text className="flex-1" level="desc" numberOfLines={1} tone={problem ? 'secondary' : 'tertiary'}>
        {healthLine(item.health)}
      </Text>
    </View>
  );
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
          {problem ? (
            <Pressable
              accessibilityHint={expanded ? '收起说明' : '展开说明'}
              accessibilityRole="button"
              className="active:opacity-60"
              hitSlop={8}
              onPress={onToggleDetail}
            >
              {line}
            </Pressable>
          ) : (
            line
          )}
        </View>
        <Icon icon={IconChevronRight} />
        <IconButton icon={IconDotsVertical} label={`${item.name} 的更多操作`} onPress={onMenu} size="sm" />
      </Pressable>
      {expanded && problem ? (
        <View className="pb-4">
          <Callout>
            <Text>{healthLine(item.health)}</Text>
            <Text className="mt-1" level="desc" tone="secondary">
              {explanation(item.health)}
            </Text>
          </Callout>
        </View>
      ) : null}
    </View>
  );
}
