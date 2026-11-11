/**
 * 榜单 (PRODUCT §3.2), old app layout (device screenshots) at P25 values: READER header with a
 * refresh button, board tabs, then one context line — the variant (GitHub period, Product Hunt
 * day, Reddit community · sort) with the 调整 button when the board has options (P23 R2-A), and
 * the freshness / paused state (P17) — then the ranked list.
 */
import { IconAdjustmentsHorizontal, IconRefresh } from '@tabler/icons-react-native';
import { FlatList, View } from 'react-native';

import { BoardEntry, type BoardEntryItem } from '@/components/BoardEntry';
import { Button, EmptyState, IconButton, Input, PageHeader, SectionHeading, Skeleton, Tabs, Text } from '@/ui';

export type BoardKey = 'hn' | 'github' | 'product_hunt' | 'reddit' | 'techtwitter';

export const boardTabs: { key: BoardKey; label: string }[] = [
  { key: 'hn', label: 'Hacker News' },
  { key: 'reddit', label: 'Reddit' },
  { key: 'github', label: 'GitHub' },
  { key: 'techtwitter', label: 'X 技术热门' },
  { key: 'product_hunt', label: 'Product Hunt' },
];

const hasOptions: Record<BoardKey, boolean> = { hn: false, techtwitter: false, github: true, product_hunt: true, reddit: true };

export type Freshness =
  | { kind: 'fresh'; age: string }
  | { kind: 'stale'; age: string }
  | { kind: 'paused'; reason: string; resumesIn: string };

export type BoardState =
  | { kind: 'loading' }
  | { kind: 'unavailable'; reason: string }
  | { kind: 'ready'; entries: BoardEntryItem[]; freshness: Freshness };

function freshnessText(freshness: Freshness) {
  if (freshness.kind === 'fresh') return `更新于 ${freshness.age}`;
  if (freshness.kind === 'stale') return `${freshness.age}的数据，刷新失败`;
  return `暂停中：${freshness.reason}，${freshness.resumesIn}后恢复`;
}

function ContextLine({ board, variantLabel, freshness, onOpenOptions }: { board: BoardKey; variantLabel?: string; freshness?: Freshness; onOpenOptions?: () => void }) {
  // One Text with nested spans, so the label and the smaller status share a baseline.
  return (
    <View className="min-h-11 flex-row items-center gap-2 px-6">
      <Text className="flex-1" level="ui-strong" numberOfLines={1}>
        {variantLabel ?? ''}
        {freshness ? (
          <Text level="caption" tone={freshness.kind === 'fresh' ? 'tertiary' : 'secondary'}>
            {variantLabel ? '  ·  ' : ''}
            {freshnessText(freshness)}
          </Text>
        ) : null}
      </Text>
      {hasOptions[board] ? <IconButton icon={IconAdjustmentsHorizontal} label="调整榜单" onPress={onOpenOptions} /> : null}
    </View>
  );
}

export function BoardsView({
  user,
  board,
  variantLabel,
  state,
  refreshing = false,
  onSelectBoard,
  onOpenOptions,
  onRefresh,
}: {
  user: { name: string; avatarUrl?: string | null };
  board: BoardKey;
  /** e.g. "r / codex · 热门", "周榜"; boards without options have none */
  variantLabel?: string;
  state: BoardState;
  refreshing?: boolean;
  onSelectBoard?: (board: BoardKey) => void;
  onOpenOptions?: () => void;
  onRefresh?: () => void;
}) {
  return (
    <View className="flex-1 bg-canvas">
      <PageHeader aside={<IconButton disabled={refreshing} icon={IconRefresh} label="刷新" onPress={onRefresh} />} title="榜单" user={user} />
      <Tabs bordered onSelect={(key) => onSelectBoard?.(key)} options={boardTabs} selected={board} />
      {state.kind === 'loading' ? (
        <>
          <ContextLine board={board} onOpenOptions={onOpenOptions} variantLabel={variantLabel} />
          <View className="gap-8 px-6 py-4">
            {[0, 1, 2, 3].map((index) => (
              <Skeleton key={index} lines={2} />
            ))}
          </View>
        </>
      ) : state.kind === 'unavailable' ? (
        <EmptyState description={`${state.reason}。Reader 每小时自动重试。`} title="这个榜单暂不可用" />
      ) : (
        <FlatList
          ListEmptyComponent={<EmptyState title="这个榜单现在是空的" />}
          ListHeaderComponent={<ContextLine board={board} freshness={state.freshness} onOpenOptions={onOpenOptions} variantLabel={variantLabel} />}
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
              <SectionHeading inset="sheet" title="已订阅的社区" />
              <Tabs inset="sheet" onSelect={() => undefined} options={subscribedCommunities.map((name) => ({ key: name, label: `r/${name}` }))} selected={subreddit ?? null} />
            </View>
          ) : null}
        </>
      ) : null}
      <View className="-mx-4">
        <SectionHeading inset="sheet" title={board === 'reddit' ? '排序' : board === 'product_hunt' ? '日期（洛杉矶时间）' : '周期'} />
        <Tabs inset="sheet" onSelect={(key) => onVariant?.(key)} options={choices[board]} selected={variant} />
      </View>
      {board === 'reddit' ? <Button className="mt-2" large title="查看" variant="primary" /> : null}
    </View>
  );
}
