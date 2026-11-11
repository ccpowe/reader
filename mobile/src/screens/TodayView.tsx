/**
 * 今天 (PRODUCT §3.1): the feed of followed subscriptions, newest first by when Reader saw it
 * (P8). Folder tabs on top, swipe between folders (current behavior, wired later); the channel
 * picker narrows to one subscription. Presentational: data and callbacks come from the route.
 */
import { IconLayoutList, IconX } from '@tabler/icons-react-native';
import { FlatList, View } from 'react-native';

import { FeedCard, type FeedCardItem } from '@/components/FeedCard';
import { Button, Divider, EmptyState, IconButton, Message, PageHeader, Skeleton, Tabs, Text } from '@/ui';

export type TodayState =
  | { kind: 'loading' }
  | { kind: 'no-subscriptions' }
  | { kind: 'ready'; items: FeedCardItem[]; loadFailed?: boolean };

export function TodayView({
  folders,
  folder,
  channel,
  state,
  banner,
  onSelectFolder,
  onOpenChannels,
  onClearChannel,
  onAddSubscription,
  onRetry,
}: {
  folders: string[];
  folder: string | null;
  /** the one subscription being shown, if any */
  channel?: string | null;
  state: TodayState;
  /** view-level problem (disconnected, signed out), one at a time */
  banner?: string | null;
  onSelectFolder?: (folder: string | null) => void;
  onOpenChannels?: () => void;
  onClearChannel?: () => void;
  onAddSubscription?: () => void;
  onRetry?: () => void;
}) {
  return (
    <View className="flex-1 bg-canvas">
      {banner ? (
        <Message bar kind="warning">
          {banner}
        </Message>
      ) : null}
      <PageHeader
        actions={<IconButton icon={IconLayoutList} label="选择频道" onPress={onOpenChannels} />}
        detail={
          channel ? (
            <View className="flex-row items-center gap-1">
              <Text tone="secondary">只看「{channel}」</Text>
              <IconButton icon={IconX} label="清除" onPress={onClearChannel} size="sm" />
            </View>
          ) : null
        }
        title="今天"
      />
      {channel ? null : (
        <View className="pb-2">
          <Tabs
            onSelect={(key) => onSelectFolder?.(key)}
            options={[{ key: null, label: '全部' }, ...folders.map((name) => ({ key: name, label: name }))]}
            selected={folder}
          />
        </View>
      )}
      <Divider />
      {state.kind === 'loading' ? (
        <View className="gap-6 p-4">
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
          ItemSeparatorComponent={() => <Divider />}
          ListEmptyComponent={<EmptyState description="订阅有新内容时会出现在这里。" title="还没有新内容" />}
          ListHeaderComponent={
            state.loadFailed ? (
              <View className="p-4">
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
