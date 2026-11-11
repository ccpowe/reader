/**
 * 今天 (PRODUCT §3.1), old app layout at P25 values: READER header with the date, folder tabs with
 * the channel filter on the right (badge = new items on 全部), or — when one subscription is shown
 * alone — a bar naming it with a way back. The feed itself is the `children` (one list, or the
 * folder pager). Presentational: data and callbacks come from the route.
 */
import { IconAdjustmentsHorizontal, IconX } from '@tabler/icons-react-native';
import type { ReactNode } from 'react';
import { View } from 'react-native';

import { IconButton, Message, PageHeader, SolidCount, Tabs, Text } from '@/ui';

export function TodayView({
  date,
  user,
  folders,
  folder,
  channel,
  newCount,
  banner,
  children,
  onSelectFolder,
  onOpenChannels,
  onClearChannel,
}: {
  /** e.g. "11 月 11 日 · 星期三" */
  date: string;
  user?: { name: string; avatarUrl?: string | null };
  /** tabs after 全部: key and label */
  folders: { key: string; label: string }[];
  /** null = 全部 */
  folder: string | null;
  /** the one subscription being shown, if any */
  channel?: string | null;
  /** new items across the subscriptions on 全部 */
  newCount?: number;
  /** view-level problem (no network), one at a time */
  banner?: string | null;
  children: ReactNode;
  onSelectFolder?: (folder: string | null) => void;
  onOpenChannels?: () => void;
  onClearChannel?: () => void;
}) {
  const filter = (
    <IconButton
      badge={newCount ? <SolidCount value={newCount} /> : null}
      icon={IconAdjustmentsHorizontal}
      label="选择频道"
      onPress={onOpenChannels}
    />
  );
  return (
    <View className="flex-1 bg-canvas">
      {banner ? (
        <Message bar kind="warning">
          {banner}
        </Message>
      ) : null}
      <PageHeader aside={date} title="今日" user={user} />
      {channel ? (
        <View className="h-11 flex-row items-center gap-2 border-b border-subtle px-6">
          <Text className="flex-1" numberOfLines={1} tone="secondary">
            只看「{channel}」
          </Text>
          <IconButton icon={IconX} label="返回分类内容" onPress={onClearChannel} size="sm" />
        </View>
      ) : (
        <Tabs
          bordered
          onSelect={(key) => onSelectFolder?.(key)}
          options={[{ key: null, label: '全部' }, ...folders]}
          selected={folder}
          trailing={filter}
        />
      )}
      <View className="flex-1">{children}</View>
    </View>
  );
}

/** The date line in the header: "11 月 11 日 · 星期三". */
export function todayLabel(now: Date): string {
  const weekdays = ['日', '一', '二', '三', '四', '五', '六'];
  return `${now.getMonth() + 1} 月 ${now.getDate()} 日 · 星期${weekdays[now.getDay()]}`;
}
