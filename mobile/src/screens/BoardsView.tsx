/**
 * 榜单 (PRODUCT §3.2, layout P23 R2-A): board tabs, an "调整" sheet for the variant
 * (GitHub period, Product Hunt today / yesterday, Reddit community + sort), a freshness line
 * above the list, and the paused / unavailable states (P17).
 */
import { IconAdjustmentsHorizontal, IconRefresh } from '@tabler/icons-react-native';
import { FlatList, View } from 'react-native';

import { BoardEntry, type BoardEntryItem } from '@/components/BoardEntry';
import { Button, Divider, EmptyState, IconButton, Input, PageHeader, SectionHeading, Skeleton, Tabs, Text } from '@/ui';

export type BoardKey = 'hn' | 'github' | 'product_hunt' | 'reddit' | 'techtwitter';

export const boardTabs: { key: BoardKey; label: string }[] = [
  { key: 'hn', label: 'Hacker News' },
  { key: 'github', label: 'GitHub' },
  { key: 'product_hunt', label: 'Product Hunt' },
  { key: 'reddit', label: 'Reddit' },
  { key: 'techtwitter', label: 'TechTwitter' },
];

export type Freshness =
  | { kind: 'fresh'; age: string }
  | { kind: 'stale'; age: string }
  | { kind: 'paused'; reason: string; resumesIn: string };

export type BoardState =
  | { kind: 'loading' }
  | { kind: 'unavailable'; reason: string }
  | { kind: 'ready'; entries: BoardEntryItem[]; freshness: Freshness; variantLabel?: string };

function FreshnessLine({ freshness, variantLabel }: { freshness: Freshness; variantLabel?: string }) {
  const parts = [variantLabel];
  if (freshness.kind === 'fresh') parts.push(`更新于 ${freshness.age}`);
  if (freshness.kind === 'stale') parts.push(`${freshness.age}的数据，刷新失败`);
  if (freshness.kind === 'paused') parts.push(`暂停中：${freshness.reason}，${freshness.resumesIn}后恢复`);
  return (
    <Text className="px-4 py-2" level="caption" numberOfLines={1} tone={freshness.kind === 'fresh' ? 'tertiary' : 'secondary'}>
      {parts.filter(Boolean).join(' · ')}
    </Text>
  );
}

export function BoardsView({
  board,
  state,
  onSelectBoard,
  onOpenOptions,
  onRefresh,
}: {
  board: BoardKey;
  state: BoardState;
  onSelectBoard?: (board: BoardKey) => void;
  onOpenOptions?: () => void;
  onRefresh?: () => void;
}) {
  return (
    <View className="flex-1 bg-canvas">
      <PageHeader
        actions={
          <>
            <IconButton icon={IconRefresh} label="刷新" onPress={onRefresh} />
            <IconButton icon={IconAdjustmentsHorizontal} label="调整榜单" onPress={onOpenOptions} />
          </>
        }
        title="榜单"
      />
      <View className="pb-2">
        <Tabs onSelect={(key) => onSelectBoard?.(key)} options={boardTabs} selected={board} />
      </View>
      <Divider />
      {state.kind === 'loading' ? (
        <View className="gap-6 p-4">
          {[0, 1, 2, 3].map((index) => (
            <Skeleton key={index} lines={2} />
          ))}
        </View>
      ) : state.kind === 'unavailable' ? (
        <EmptyState description={`${state.reason}。Reader 每小时自动重试。`} title="这个榜单暂不可用" />
      ) : (
        <FlatList
          ItemSeparatorComponent={() => <Divider inset />}
          ListEmptyComponent={<EmptyState title="这个榜单现在是空的" />}
          ListHeaderComponent={<FreshnessLine freshness={state.freshness} variantLabel={state.variantLabel} />}
          data={state.entries}
          keyExtractor={(entry) => String(entry.rank)}
          renderItem={({ item }) => <BoardEntry entry={item} />}
        />
      )}
    </View>
  );
}

/** Content of the "调整" sheet for the current board. */
export function BoardOptions({
  board,
  variant,
  subreddit,
  subscribedCommunities = [],
  onVariant,
}: {
  board: BoardKey;
  variant: string;
  subreddit?: string;
  subscribedCommunities?: string[];
  onVariant?: (variant: string) => void;
}) {
  const choices: Record<BoardKey, { key: string; label: string }[]> = {
    hn: [],
    techtwitter: [],
    github: [
      { key: 'daily', label: '日榜' },
      { key: 'weekly', label: '周榜' },
      { key: 'monthly', label: '月榜' },
    ],
    product_hunt: [
      { key: 'today', label: '今天' },
      { key: 'yesterday', label: '昨天' },
    ],
    reddit: [
      { key: 'hot', label: '热门' },
      { key: 'rising', label: '上升' },
      { key: 'top-day', label: 'Top · 日' },
      { key: 'top-week', label: 'Top · 周' },
      { key: 'top-month', label: 'Top · 月' },
      { key: 'top-year', label: 'Top · 年' },
    ],
  };
  return (
    <View className="gap-2">
      {board === 'reddit' ? (
        <>
          <Input autoCapitalize="none" defaultValue={subreddit} label="社区" placeholder="r/python" />
          {subscribedCommunities.length > 0 ? (
            <View className="-mx-4">
              <SectionHeading title="已订阅的社区" />
              <Tabs onSelect={() => undefined} options={subscribedCommunities.map((name) => ({ key: name, label: `r/${name}` }))} selected={subreddit ?? null} />
            </View>
          ) : null}
        </>
      ) : null}
      {choices[board].length > 0 ? (
        <View className="-mx-4">
          <SectionHeading strong title={board === 'reddit' ? '排序' : board === 'product_hunt' ? '日期（洛杉矶时间）' : '周期'} />
          <Tabs onSelect={(key) => onVariant?.(key)} options={choices[board]} selected={variant} />
        </View>
      ) : (
        <Text tone="secondary">这个榜单没有可调整的选项。</Text>
      )}
      {board === 'reddit' ? <Button className="mt-2" large title="查看" variant="primary" /> : null}
    </View>
  );
}
