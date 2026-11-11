/**
 * R1 今天页的信息卡片（SLICE.md）。A = 现状；B = 紧凑；C = 标题优先。
 * X 卡片在三个方案里都沿用现状的层级（作者行、正文、引用卡片、"共 N 条"）。
 */
import type { Meta, StoryObj } from '@storybook/react-native';
import { Text, View } from 'react-native';

import { cn } from '@/lib/cn';

import { Avatar, Divider, SaveButton, Screen, Thumb } from './primitives';
import { feedItems, type FeedCardData } from './sample';

type Variant = 'A' | 'B' | 'C';

function SourceLine({ item, compact }: { item: FeedCardData; compact?: boolean }) {
  return (
    <View className="min-w-0 flex-1 flex-row items-center gap-1.5">
      {compact ? null : <Avatar name={item.source.name} />}
      <Text className="shrink text-xs text-muted-foreground" numberOfLines={1}>
        {item.source.name}
      </Text>
      <Text className="shrink-0 text-xs text-subtle-foreground" numberOfLines={1}>
        · {item.seenAgo}
      </Text>
    </View>
  );
}

function ThreadBadge({ count, inline = false }: { count: number; inline?: boolean }) {
  return (
    <View className={cn('rounded-full bg-muted px-2 py-0.5', inline ? 'self-center' : 'self-start')}>
      <Text className="text-xs text-muted-foreground">共 {count} 条</Text>
    </View>
  );
}

/** X post body, as today: author row, text, quote card with a border, thread count. */
function XBody({ item, variant }: { item: FeedCardData; variant: Variant }) {
  const x = item.x!;
  return (
    <View className="gap-2">
      <View className="flex-row items-center gap-2">
        <Avatar name={x.author.name} size={variant === 'B' ? 20 : 28} />
        <View className="min-w-0 flex-1">
          <Text className="text-sm font-semibold text-foreground" numberOfLines={1}>
            {x.author.name} <Text className="font-normal text-muted-foreground">@{x.author.handle}</Text>
          </Text>
          {variant === 'B' ? null : <Text className="text-xs text-subtle-foreground">{item.seenAgo}</Text>}
        </View>
        {variant === 'B' && x.threadCount ? <ThreadBadge count={x.threadCount} inline /> : null}
        <SaveButton saved={item.isSaved} />
      </View>
      <Text className="text-base text-foreground" numberOfLines={variant === 'B' ? 3 : 6}>
        {x.text}
      </Text>
      {x.quote ? (
        <View className="gap-1 rounded-md border border-border p-3">
          <Text className="text-xs font-semibold text-foreground">
            {x.quote.author} <Text className="font-normal text-muted-foreground">@{x.quote.handle}</Text>
          </Text>
          <Text className="text-sm text-foreground" numberOfLines={4}>
            {x.quote.text}
          </Text>
        </View>
      ) : null}
      {item.imageUrl && variant !== 'B' ? (
        <View className="h-40 w-full overflow-hidden rounded-md bg-muted" />
      ) : null}
      {variant !== 'B' && x.threadCount ? <ThreadBadge count={x.threadCount} /> : null}
    </View>
  );
}

function ArticleA({ item }: { item: FeedCardData }) {
  return (
    <View className="gap-2">
      <View className="flex-row items-center">
        <SourceLine item={item} />
        <SaveButton saved={item.isSaved} />
      </View>
      <View className="flex-row gap-3">
        <Text className="flex-1 text-base font-semibold text-foreground" numberOfLines={3}>
          {item.title}
        </Text>
        {item.imageUrl ? <Thumb size={72} uri={item.imageUrl} /> : null}
      </View>
      {item.summary ? (
        <Text className="text-sm text-muted-foreground" numberOfLines={2}>
          {item.summary}
        </Text>
      ) : null}
    </View>
  );
}

function ArticleB({ item }: { item: FeedCardData }) {
  return (
    <View className="flex-row items-center gap-3">
      <View className="min-w-0 flex-1 gap-1">
        <SourceLine compact item={item} />
        <Text className="text-base font-semibold text-foreground" numberOfLines={2}>
          {item.title}
        </Text>
      </View>
      {item.imageUrl ? <Thumb size={56} uri={item.imageUrl} /> : null}
      <SaveButton saved={item.isSaved} />
    </View>
  );
}

function ArticleC({ item }: { item: FeedCardData }) {
  return (
    <View className="flex-row gap-3">
      <View className="min-w-0 flex-1 gap-1.5">
        <Text className="text-base font-semibold text-foreground" numberOfLines={3}>
          {item.title}
        </Text>
        {item.summary ? (
          <Text className="text-sm text-muted-foreground" numberOfLines={2}>
            {item.summary}
          </Text>
        ) : null}
        <View className="flex-row items-center">
          <SourceLine item={item} />
          <SaveButton saved={item.isSaved} />
        </View>
      </View>
      {item.imageUrl ? <Thumb size={72} uri={item.imageUrl} /> : null}
    </View>
  );
}

function FeedCard({ item, variant }: { item: FeedCardData; variant: Variant }) {
  return (
    <View className={cn('px-4 active:bg-pressed', variant === 'B' ? 'py-2.5' : 'py-3.5')}>
      {item.x ? (
        <XBody item={item} variant={variant} />
      ) : variant === 'A' ? (
        <ArticleA item={item} />
      ) : variant === 'B' ? (
        <ArticleB item={item} />
      ) : (
        <ArticleC item={item} />
      )}
    </View>
  );
}

function Feed({ variant }: { variant: Variant }) {
  return (
    <View>
      {feedItems.map((item) => (
        <View key={item.id}>
          <FeedCard item={item} variant={variant} />
          <Divider />
        </View>
      ))}
    </View>
  );
}

const meta: Meta = { title: 'R1 今天页的信息卡片', parameters: { fullScreen: true } };
export default meta;
type Story = StoryObj;

export const A: Story = {
  name: 'R1-A 现状',
  render: () => (
    <Screen note="来源行在上，标题 3 行 + 右侧缩略图，摘要 2 行，书签在右上。" title="R1-A 现状">
      <Feed variant="A" />
    </Screen>
  ),
};

export const B: Story = {
  name: 'R1-B 紧凑',
  render: () => (
    <Screen note="一行小字来源，标题 2 行，不显示摘要，小缩略图；一屏能多看约一半。" title="R1-B 紧凑">
      <Feed variant="B" />
    </Screen>
  ),
};

export const C: Story = {
  name: 'R1-C 标题优先',
  render: () => (
    <Screen note="标题在最上面，摘要其次，来源和书签放到最后一行。" title="R1-C 标题优先">
      <Feed variant="C" />
    </Screen>
  ),
};
