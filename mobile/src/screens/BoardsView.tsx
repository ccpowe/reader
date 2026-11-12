/**
 * 榜单 (PRODUCT §3.2), old app layout (device screenshots) at P25 values: READER header with a
 * refresh button, board tabs, then the board pager (`children`). Each page is a BoardList: one
 * context line — the version (GitHub period, Product Hunt day, Reddit community · sort) with the
 * 调整 button when the board has options (P23 R2-A), and the freshness / paused state (P17) — then
 * the ranked list. Presentational: data and callbacks come from the route.
 */
import { IconAdjustmentsHorizontal, IconRefresh } from '@tabler/icons-react-native';
import { useState, type ReactNode } from 'react';
import { FlatList, RefreshControl, View } from 'react-native';
import Animated from 'react-native-reanimated';
import { useCSSVariable } from 'uniwind';

import { BoardEntry, type BoardEntryItem } from '@/components/BoardEntry';
import { BOARDS, VARIANTS, communityName, type BoardKey, type BoardView, type Freshness } from '@/lib/boards';
import { Button, EmptyState, IconButton, Input, Message, PageHeader, SectionHeading, Skeleton, Tabs, Text } from '@/ui';
import { delayLoading, duration } from '@/ui/theme';

export type { BoardKey, Freshness };

/** A row and the address it opens. */
export type BoardListRow = BoardEntryItem & { url: string };

export type BoardState =
  | { kind: 'loading' }
  | { kind: 'unavailable'; reason: string }
  | { kind: 'failed'; message: string }
  /** Reddit without a community chosen */
  | { kind: 'no-community' }
  | { kind: 'ready'; rows: BoardListRow[]; freshness: Freshness | null; refreshFailed?: string | null };

const appearLate = {
  animationName: { from: { opacity: 0 }, to: { opacity: 1 } },
  animationDuration: `${duration.fast}ms`,
  animationDelay: `${delayLoading}ms`,
  animationFillMode: 'both',
} as const;

function freshnessText(freshness: Freshness) {
  if (freshness.kind === 'fresh') return `更新于 ${freshness.age}`;
  if (freshness.kind === 'stale') return `${freshness.age}的数据，刷新失败`;
  return `暂停中：${freshness.reason}，${freshness.resumesIn}后恢复`;
}

function ContextLine({
  label,
  freshness,
  onOpenOptions,
}: {
  label?: string;
  freshness?: Freshness | null;
  onOpenOptions?: () => void;
}) {
  if (!label && !freshness && !onOpenOptions) return null;
  // One Text with nested spans, so the label and the smaller status share a baseline.
  return (
    <View className="min-h-11 flex-row items-center gap-2 px-6">
      <Text className="flex-1" level="ui-strong" numberOfLines={1}>
        {label ?? ''}
        {freshness ? (
          <Text level="caption" tone={freshness.kind === 'fresh' ? 'tertiary' : 'secondary'}>
            {label ? '  ·  ' : ''}
            {freshnessText(freshness)}
          </Text>
        ) : null}
      </Text>
      {onOpenOptions ? <IconButton icon={IconAdjustmentsHorizontal} label="调整榜单" onPress={onOpenOptions} /> : null}
    </View>
  );
}

export function BoardsView({
  user,
  board,
  refreshing = false,
  banner,
  children,
  onSelectBoard,
  onRefresh,
}: {
  user?: { name: string; avatarUrl?: string | null };
  board: BoardKey;
  refreshing?: boolean;
  banner?: string | null;
  children: ReactNode;
  onSelectBoard?: (board: BoardKey) => void;
  onRefresh?: () => void;
}) {
  return (
    <View className="flex-1 bg-canvas">
      {banner ? (
        <Message bar kind="warning">
          {banner}
        </Message>
      ) : null}
      <PageHeader
        aside={<IconButton disabled={refreshing} icon={IconRefresh} label="刷新" onPress={onRefresh} />}
        title="榜单"
        user={user}
      />
      <Tabs bordered onSelect={(key) => onSelectBoard?.(key)} options={BOARDS} selected={board} />
      <View className="flex-1">{children}</View>
    </View>
  );
}

/** One board's list with all its states. */
export function BoardList({
  state,
  label,
  refreshing = false,
  initialOffset = 0,
  onOpenOptions,
  onOpen,
  onRefresh,
  onRetry,
  onOffset,
}: {
  state: BoardState;
  /** e.g. "r/codex · 热门", "周榜" */
  label?: string;
  refreshing?: boolean;
  initialOffset?: number;
  /** boards with versions or a community */
  onOpenOptions?: () => void;
  onOpen?: (row: BoardListRow) => void;
  onRefresh?: () => void;
  onRetry?: () => void;
  onOffset?: (offset: number) => void;
}) {
  const spinner = useCSSVariable('--color-icon-neutral') as string | undefined;
  if (state.kind === 'loading') {
    return (
      <View>
        <ContextLine label={label} onOpenOptions={onOpenOptions} />
        <Animated.View className="gap-8 px-6 py-4" style={appearLate}>
          {[0, 1, 2, 3].map((index) => (
            <Skeleton key={index} lines={2} />
          ))}
        </Animated.View>
      </View>
    );
  }
  if (state.kind === 'unavailable') {
    return (
      <View className="flex-1">
        <ContextLine label={label} onOpenOptions={onOpenOptions} />
        <EmptyState
          action={<Button onPress={onRetry} title="重试" />}
          description={`${state.reason}。`}
          title="这个榜单暂不可用"
        />
      </View>
    );
  }
  if (state.kind === 'failed') {
    return (
      <View className="flex-1">
        <ContextLine label={label} onOpenOptions={onOpenOptions} />
        <EmptyState action={<Button onPress={onRetry} title="重试" />} description={state.message} title="没能加载" />
      </View>
    );
  }
  if (state.kind === 'no-community') {
    return (
      <EmptyState
        action={<Button large onPress={onOpenOptions} title="选择社区" variant="primary" />}
        description="Reddit 榜单按社区看。选一个已订阅的社区，或输入任意社区。"
        title="还没选社区"
      />
    );
  }
  return (
    <FlatList
      ListEmptyComponent={<EmptyState title="这个榜单现在是空的" />}
      ListHeaderComponent={
        <>
          <ContextLine freshness={state.freshness} label={label} onOpenOptions={onOpenOptions} />
          {state.refreshFailed ? (
            <Message bar kind="warning">
              {state.refreshFailed}
            </Message>
          ) : null}
        </>
      }
      contentOffset={{ x: 0, y: initialOffset }}
      data={state.rows}
      keyExtractor={(row) => `${row.rank}:${row.url}`}
      onScroll={(event) => onOffset?.(event.nativeEvent.contentOffset.y)}
      refreshControl={
        onRefresh ? (
          <RefreshControl
            colors={spinner ? [spinner] : undefined}
            onRefresh={onRefresh}
            refreshing={refreshing}
            tintColor={spinner}
          />
        ) : undefined
      }
      renderItem={({ item }) => <BoardEntry entry={item} onOpen={() => onOpen?.(item)} />}
      scrollEventThrottle={64}
    />
  );
}

/**
 * Content of the 调整 sheet. GitHub and Product Hunt: tapping a version applies it. Reddit: a
 * community (subscribed ones as quick choices, or any typed name, P17) and a sort, applied with 查看.
 */
export function BoardOptions({
  view,
  subscribedCommunities = [],
  onApply,
}: {
  view: BoardView;
  subscribedCommunities?: string[];
  onApply?: (view: BoardView) => void;
}) {
  const [typed, setTyped] = useState(view.subreddit ?? '');
  const [sort, setSort] = useState(view.variant);
  const [touched, setTouched] = useState(false);
  const community = communityName(typed);
  const choices = VARIANTS[view.board];

  if (view.board !== 'reddit') {
    return (
      <View className="-mx-4">
        <SectionHeading inset="sheet" title={view.board === 'product_hunt' ? '日期（洛杉矶时间）' : '周期'} />
        <Tabs inset="sheet" onSelect={(key) => onApply?.({ ...view, variant: key })} options={choices} selected={view.variant} />
      </View>
    );
  }
  return (
    <View className="gap-2">
      <Input
        autoCapitalize="none"
        autoCorrect={false}
        error={touched && typed.trim() && !community ? '社区名只能有字母、数字和下划线' : undefined}
        label="社区"
        onBlur={() => setTouched(true)}
        onChangeText={setTyped}
        placeholder="r/python"
        value={typed}
      />
      {subscribedCommunities.length > 0 ? (
        <View className="-mx-4">
          <SectionHeading inset="sheet" title="已订阅的社区" />
          <Tabs
            inset="sheet"
            onSelect={(key) => key && setTyped(key)}
            options={subscribedCommunities.map((name) => ({ key: name, label: `r/${name}` }))}
            selected={community ?? null}
          />
        </View>
      ) : null}
      <View className="-mx-4">
        <SectionHeading inset="sheet" title="排序" />
        <Tabs inset="sheet" onSelect={setSort} options={choices} selected={sort} />
      </View>
      <Button
        className="mt-2"
        disabled={!community}
        large
        onPress={() => community && onApply?.({ ...view, subreddit: community, variant: sort })}
        title="查看"
        variant="primary"
      />
      {!community && !typed.trim() ? (
        <Text level="caption" tone="secondary">
          输入社区名，或点一个已订阅的社区。
        </Text>
      ) : null}
    </View>
  );
}
