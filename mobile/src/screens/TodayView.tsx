/**
 * 今天 (PRODUCT §3.1), old app layout at P25 values: READER header with the date, folder tabs with
 * the channel filter on the right (count badge = subscriptions picked), then the feed, newest
 * first by when Reader saw it (P8). Presentational: data and callbacks come from the route.
 */
import { IconAdjustmentsHorizontal, IconX } from '@tabler/icons-react-native';
import { FlatList, View } from 'react-native';

import { FeedCard, type FeedCardItem } from '@/components/FeedCard';
import { Button, EmptyState, IconButton, Message, PageHeader, Skeleton, SolidCount, Tabs, Text } from '@/ui';

export type TodayState =
  | { kind: 'loading' }
  | { kind: 'no-subscriptions' }
  | { kind: 'ready'; items: FeedCardItem[]; loadFailed?: boolean };

export function TodayView({
  date,
  user,
  folders,
  folder,
  channel,
  channelCount,
  state,
  banner,
  onSelectFolder,
  onOpenChannels,
  onClearChannel,
  onAddSubscription,
  onRetry,
}: {
  /** e.g. "11 月 11 日 · 星期三" */
  date: string;
  user: { name: string; avatarUrl?: string | null };
  folders: string[];
  folder: string | null;
  /** the one subscription being shown, if any */
  channel?: string | null;
  /** how many subscriptions the channel picker has ticked for 全部 */
  channelCount?: number;
  state: TodayState;
  /** view-level problem (disconnected, signed out), one at a time */
  banner?: string | null;
  onSelectFolder?: (folder: string | null) => void;
  onOpenChannels?: () => void;
  onClearChannel?: () => void;
  onAddSubscription?: () => void;
  onRetry?: () => void;
}) {
  const filter = (
    <IconButton
      badge={channelCount ? <SolidCount value={channelCount} /> : null}
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
          options={[{ key: null, label: '全部' }, ...folders.map((name) => ({ key: name, label: name }))]}
          selected={folder}
          trailing={filter}
        />
      )}
      {state.kind === 'loading' ? (
        <View className="gap-8 px-6 py-6">
          {[0, 1, 2, 3].map((index) => (
            <Skeleton key={index} lines={3} />
          ))}
        </View>
      ) : state.kind === 'no-subscriptions' ? (
        <EmptyState
          action={<Button large onPress={onAddSubscription} title="添加订阅" variant="primary" />}
          description="添加 RSS、网站、YouTube、X 或 Reddit，新内容会出现在这里。"
          title="还没有订阅"
        />
      ) : (
        <FlatList
          ListEmptyComponent={<EmptyState description="订阅有新内容时会出现在这里。" title="还没有新内容" />}
          ListHeaderComponent={
            state.loadFailed ? (
              <View className="px-6 pt-4">
                <Message action={<Button onPress={onRetry} title="重试" variant="ghost" />} kind="danger">
                  加载失败，显示的是之前的内容。
                </Message>
              </View>
            ) : undefined
          }
          data={state.items}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => <FeedCard item={item} />}
        />
      )}
    </View>
  );
}
