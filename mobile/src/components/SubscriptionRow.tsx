/**
 * A subscription in the list: avatar, name, then either its type or — when scanning has a
 * problem — the reason in that place (P23 R4-B). Tapping a row with a problem shows more.
 */
import { View } from 'react-native';

import { IconAlertCircle, IconAlertTriangle } from '@tabler/icons-react-native';

import { Avatar, Callout, Count, Icon, Row, Text } from '@/ui';

export type SubscriptionHealth =
  | { kind: 'ok' }
  | { kind: 'failing'; reason: string; since: string }
  | { kind: 'gap'; detail: string };

export type SubscriptionItem = {
  id: string;
  name: string;
  type: 'rss' | 'youtube' | 'x' | 'reddit';
  newCount: number;
  health: SubscriptionHealth;
  avatarUrl?: string | null;
};

const typeLabel = { rss: 'RSS', youtube: 'YouTube', x: 'X', reddit: 'Reddit' } as const;

export function healthLine(health: SubscriptionHealth): string | null {
  if (health.kind === 'failing') return `扫描失败 · ${health.reason} · ${health.since}`;
  if (health.kind === 'gap') return `可能有遗漏 · ${health.detail}`;
  return null;
}

export function SubscriptionRow({
  item,
  expanded = false,
  onPress,
}: {
  item: SubscriptionItem;
  expanded?: boolean;
  onPress?: () => void;
}) {
  const problem = healthLine(item.health);
  return (
    <View>
      <Row
        detail={problem ?? typeLabel[item.type]}
        detailIcon={
          item.health.kind === 'failing' ? (
            <Icon icon={IconAlertCircle} size="xs" tone="error" />
          ) : item.health.kind === 'gap' ? (
            <Icon icon={IconAlertTriangle} size="xs" tone="warning" />
          ) : null
        }
        detailTone={problem ? 'secondary' : 'tertiary'}
        label={item.name}
        leading={<Avatar name={item.name} size={32} uri={item.avatarUrl} />}
        onPress={onPress}
        trailing={item.newCount > 0 ? <Count value={item.newCount} /> : null}
      />
      {expanded && problem ? (
        <View className="px-4 pb-3">
          <Callout>
            <Text>{problem}</Text>
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
