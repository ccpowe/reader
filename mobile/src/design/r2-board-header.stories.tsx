/**
 * R2 榜单页的顶部（SLICE.md）。A = 现状（榜单标签 + "调整"弹层）；B = 版本直接摆出来；
 * C = 标题即选择器。每个方案都放四种数据状态：新鲜 / 已过期 / 暂停中 / 暂不可用。
 */
import type { Meta, StoryObj } from '@storybook/react-native';
import { ChevronDown, SlidersHorizontal } from 'lucide-react-native';
import { useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';

import { cn } from '@/lib/cn';

import { Chip, DesignNote, Divider, IconButton, SaveButton, Thumb, useColor } from './primitives';
import { boards, boardStatuses, type BoardData, type BoardStatus } from './sample';

type Variant = 'A' | 'B' | 'C';

function statusText(status: BoardStatus): { text: string; tone: 'normal' | 'warning' | 'danger' } {
  switch (status.kind) {
    case 'fresh':
      return { text: `更新于 ${status.age}`, tone: 'normal' };
    case 'stale':
      return { text: `${status.age}的数据（刷新失败）`, tone: 'warning' };
    case 'paused':
      return { text: `暂停中：${status.reason}，${status.resumesIn}后恢复`, tone: 'warning' };
    case 'unavailable':
      return { text: `暂不可用：${status.reason}`, tone: 'danger' };
  }
}

function StatusLine({ status, inline = false }: { status: BoardStatus; inline?: boolean }) {
  const { text, tone } = statusText(status);
  return (
    <Text
      className={cn(
        'text-xs',
        tone === 'normal' ? 'text-subtle-foreground' : tone === 'warning' ? 'text-warning' : 'text-danger',
        inline ? '' : 'px-4 py-2',
      )}
      numberOfLines={1}
    >
      {text}
    </Text>
  );
}

function Entries({ board }: { board: BoardData }) {
  return (
    <View>
      {board.entries.map((entry) => (
        <View key={entry.rank}>
          <View className="flex-row gap-3 px-4 py-3 active:bg-pressed">
            <Text className="w-5 pt-0.5 text-sm text-subtle-foreground">{entry.rank}</Text>
            <View className="min-w-0 flex-1 gap-1">
              <Text className="text-base font-semibold text-foreground" numberOfLines={3}>
                {entry.title}
              </Text>
              {entry.description ? (
                <Text className="text-sm text-muted-foreground" numberOfLines={2}>
                  {entry.description}
                </Text>
              ) : null}
              <Text className="text-xs text-subtle-foreground">
                {[...(entry.tags ?? []).map((tag) => `#${tag}`), ...entry.meta].join(' · ')}
              </Text>
            </View>
            {entry.imageUrl ? <Thumb size={56} uri={entry.imageUrl} /> : null}
            <SaveButton saved={entry.isSaved} />
          </View>
          <Divider />
        </View>
      ))}
    </View>
  );
}

function BoardTabs({ selected, onSelect }: { selected: string; onSelect: (key: BoardData['key']) => void }) {
  return (
    <ScrollView contentContainerClassName="gap-4 px-4" horizontal showsHorizontalScrollIndicator={false}>
      {boards.map((board) => (
        <Pressable className="py-2" key={board.key} onPress={() => onSelect(board.key)}>
          <Text
            className={cn(
              'text-base',
              selected === board.key ? 'font-semibold text-foreground' : 'text-muted-foreground',
            )}
          >
            {board.label}
          </Text>
          <View className={cn('mt-1 h-0.5 rounded-full', selected === board.key ? 'bg-foreground' : 'bg-transparent')} />
        </Pressable>
      ))}
    </ScrollView>
  );
}

function VariantRow({ board, status }: { board: BoardData; status: BoardStatus }) {
  const muted = useColor('muted-foreground');
  return (
    <View className="flex-row items-center gap-2 px-4 py-2">
      {board.key === 'reddit' ? (
        <Pressable className="flex-row items-center gap-1 rounded-full border border-border px-3 py-1.5 active:bg-pressed">
          <Text className="text-sm text-foreground">r/python</Text>
          <ChevronDown color={muted} size={14} />
        </Pressable>
      ) : null}
      {board.variants.map((variant) => (
        <Chip active={variant.key === board.selectedVariant} key={variant.key} label={variant.label} />
      ))}
      <View className="min-w-0 flex-1 items-end">
        <StatusLine inline status={status} />
      </View>
    </View>
  );
}

function Header({ variant, board, status, onSelect }: {
  variant: Variant;
  board: BoardData;
  status: BoardStatus;
  onSelect: (key: BoardData['key']) => void;
}) {
  const muted = useColor('muted-foreground');
  if (variant === 'C') {
    const current = board.variants.find((item) => item.key === board.selectedVariant);
    return (
      <View className="border-b border-border">
        <Pressable className="flex-row items-center gap-1 px-4 pb-1 pt-3 active:opacity-70">
          <Text className="text-xl font-semibold text-foreground">{board.label}</Text>
          <ChevronDown color={muted} size={20} />
        </Pressable>
        <View className="flex-row items-center gap-2 px-4 pb-2">
          {current || board.key === 'reddit' ? (
            <Text className="text-xs text-muted-foreground">
              {[board.key === 'reddit' ? 'r/python' : null, current?.label].filter(Boolean).join(' · ')} ·
            </Text>
          ) : null}
          <StatusLine inline status={status} />
        </View>
      </View>
    );
  }
  return (
    <View className="border-b border-border">
      <View className="flex-row items-center pr-2 pt-2">
        <Text className="flex-1 px-4 text-xl font-semibold text-foreground">榜单</Text>
        {variant === 'A' ? <IconButton icon={SlidersHorizontal} label="调整榜单" /> : null}
      </View>
      <BoardTabs onSelect={onSelect} selected={board.key} />
      {variant === 'B' && (board.variants.length > 0 || board.key === 'reddit') ? (
        <VariantRow board={board} status={status} />
      ) : null}
    </View>
  );
}

function BoardScreen({ variant, title, note }: { variant: Variant; title: string; note: string }) {
  const [key, setKey] = useState<BoardData['key']>('github');
  const [statusIndex, setStatusIndex] = useState(0);
  const board = boards.find((item) => item.key === key)!;
  const status = key === 'techtwitter' ? boardStatuses[3] : boardStatuses[statusIndex];
  const showsInlineStatus = variant === 'C' || (variant === 'B' && (board.variants.length > 0 || board.key === 'reddit'));
  return (
    <View className="flex-1 bg-background">
      <DesignNote title={title}>{note}</DesignNote>
      <View className="flex-row flex-wrap gap-2 border-b border-dashed border-border bg-muted px-4 py-2">
        <Text className="text-xs text-muted-foreground">切换数据状态：</Text>
        {boardStatuses.slice(0, 3).map((item, index) => (
          <Pressable key={item.kind} onPress={() => setStatusIndex(index)}>
            <Text className={cn('text-xs', index === statusIndex ? 'font-semibold text-foreground' : 'text-muted-foreground')}>
              {['新鲜', '已过期', '暂停中'][index]}
            </Text>
          </Pressable>
        ))}
        <Text className="text-xs text-muted-foreground">（TechTwitter = 暂不可用）</Text>
      </View>
      <Header board={board} onSelect={setKey} status={status} variant={variant} />
      <ScrollView>
        {showsInlineStatus ? null : <StatusLine status={status} />}
        {status.kind === 'unavailable' ? (
          <View className="items-center gap-2 px-8 py-16">
            <Text className="text-base text-foreground">TechTwitter 暂不可用</Text>
            <Text className="text-center text-sm text-muted-foreground">
              对方开启了浏览器验证，服务器暂时抓不到；Reader 每小时自动重试。
            </Text>
          </View>
        ) : (
          <Entries board={board} />
        )}
      </ScrollView>
    </View>
  );
}

const meta: Meta = { title: 'R2 榜单页的顶部', parameters: { fullScreen: true } };
export default meta;
type Story = StoryObj;

export const A: Story = {
  name: 'R2-A 现状',
  render: () => (
    <BoardScreen
      note="榜单标签可切换；右上角「调整」打开弹层选周期 / 日期 / 社区 / 排序；新旧提示在列表最上方。"
      title="R2-A 现状"
      variant="A"
    />
  ),
};

export const B: Story = {
  name: 'R2-B 版本直接摆出来',
  render: () => (
    <BoardScreen
      note="标签下面多一行：当前榜单的版本按钮（GitHub 日周月、PH 今天/昨天、Reddit 社区 + 排序），新旧提示在这一行右端。"
      title="R2-B 版本直接摆出来"
      variant="B"
    />
  ),
};

export const C: Story = {
  name: 'R2-C 标题即选择器',
  render: () => (
    <BoardScreen
      note="页头标题就是当前榜单名，点开弹层选榜单和版本（本原型只做外观，点了不会弹出）；下面一行是版本和新旧。"
      title="R2-C 标题即选择器"
      variant="C"
    />
  ),
};
