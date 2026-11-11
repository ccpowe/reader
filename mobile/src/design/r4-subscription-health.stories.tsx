/**
 * R4 订阅列表行的健康提示（SLICE.md）。现状没有健康提示：A = 在现状行上加得最少（小圆点）；
 * B = 行内一行说明；C = 顶部汇总 + 行内图标。点有问题的行（或圆点）会展开原因。
 */
import type { Meta, StoryObj } from '@storybook/react-native';
import { AlertCircle, ChevronRight } from 'lucide-react-native';
import { useState } from 'react';
import { Pressable, Text, View } from 'react-native';

import { cn } from '@/lib/cn';

import { Avatar, Divider, Screen, useColor } from './primitives';
import { subscriptionFolders, type SubscriptionData } from './sample';

type Variant = 'A' | 'B' | 'C';

const typeLabel: Record<SubscriptionData['type'], string> = {
  rss: 'RSS',
  youtube: 'YouTube',
  x: 'X',
  reddit: 'Reddit',
};

function healthText(item: SubscriptionData): string | null {
  if (item.health.kind === 'failing') return `扫描失败 · ${item.health.reason} · ${item.health.since}`;
  if (item.health.kind === 'gap') return `可能有遗漏 · ${item.health.detail}`;
  return null;
}

function Explanation({ item }: { item: SubscriptionData }) {
  return (
    <View className="mx-4 mb-3 rounded-md bg-muted p-3">
      <Text className="text-sm text-foreground">{healthText(item)}</Text>
      <Text className="mt-1 text-xs text-muted-foreground">
        {item.health.kind === 'failing'
          ? 'Reader 会自动重试；一直失败时可以检查地址，或取消订阅。'
          : '下次扫描不会补上更早的内容；需要的话可以去原网站看。'}
      </Text>
    </View>
  );
}

function Row({ item, variant, open, onToggle }: {
  item: SubscriptionData;
  variant: Variant;
  open: boolean;
  onToggle: () => void;
}) {
  const warning = useColor('warning');
  const danger = useColor('danger');
  const problem = item.health.kind !== 'ok';
  const tone = item.health.kind === 'failing' ? danger : warning;
  return (
    <View>
      <Pressable className="flex-row items-center gap-3 px-4 py-3 active:bg-pressed" onPress={problem ? onToggle : undefined}>
        <Avatar name={item.name} size={32} />
        <View className="min-w-0 flex-1">
          <View className="flex-row items-center gap-1.5">
            <Text className="shrink text-base text-foreground" numberOfLines={1}>
              {item.name}
            </Text>
            {problem && variant === 'A' ? (
              <View className="h-2 w-2 rounded-full" style={{ backgroundColor: tone }} />
            ) : null}
            {problem && variant === 'C' ? <AlertCircle color={tone} size={14} /> : null}
          </View>
          {problem && variant === 'B' ? (
            <Text className={cn('text-xs', item.health.kind === 'failing' ? 'text-danger' : 'text-warning')} numberOfLines={1}>
              {healthText(item)}
            </Text>
          ) : (
            <Text className="text-xs text-subtle-foreground">{typeLabel[item.type]}</Text>
          )}
        </View>
        {item.newCount > 0 ? (
          <View className="min-w-6 items-center rounded-full bg-muted px-2 py-0.5">
            <Text className="text-xs text-muted-foreground">{item.newCount}</Text>
          </View>
        ) : null}
      </Pressable>
      {open ? <Explanation item={item} /> : null}
      <Divider />
    </View>
  );
}

function SubscriptionList({ variant }: { variant: Variant }) {
  const [open, setOpen] = useState<string | null>(null);
  const [onlyProblems, setOnlyProblems] = useState(false);
  const muted = useColor('muted-foreground');
  const problems = subscriptionFolders.flatMap((folder) => folder.items).filter((item) => item.health.kind !== 'ok');
  const folders = onlyProblems
    ? [{ name: '有问题的订阅', items: problems }]
    : subscriptionFolders;
  return (
    <View>
      {variant === 'C' ? (
        <Pressable
          className="mx-4 my-2 flex-row items-center gap-2 rounded-md border border-border px-3 py-2.5 active:bg-pressed"
          onPress={() => setOnlyProblems(!onlyProblems)}
        >
          <AlertCircle color={muted} size={16} />
          <Text className="flex-1 text-sm text-foreground">
            {onlyProblems ? '显示全部订阅' : `${problems.length} 个订阅有问题`}
          </Text>
          <ChevronRight color={muted} size={16} />
        </Pressable>
      ) : null}
      {folders.map((folder) => (
        <View key={folder.name}>
          <Text className="px-4 pb-1 pt-4 text-xs font-semibold text-muted-foreground">{folder.name}</Text>
          {folder.items.map((item) => (
            <Row
              item={item}
              key={item.id}
              onToggle={() => setOpen(open === item.id ? null : item.id)}
              open={open === item.id}
              variant={variant}
            />
          ))}
        </View>
      ))}
    </View>
  );
}

const meta: Meta = { title: 'R4 订阅行的健康提示', parameters: { fullScreen: true } };
export default meta;
type Story = StoryObj;

export const A: Story = {
  name: 'R4-A 只加小圆点',
  render: () => (
    <Screen note="现状的订阅行；出问题时名字右边一个小圆点（红 = 扫描失败，黄 = 可能有遗漏），点这一行展开原因。" title="R4-A 只加小圆点">
      <SubscriptionList variant="A" />
    </Screen>
  ),
};

export const B: Story = {
  name: 'R4-B 行内一行说明',
  render: () => (
    <Screen note="出问题的行把「类型」那行小字换成原因，不用点就能看到；点一下看更多说明。" title="R4-B 行内一行说明">
      <SubscriptionList variant="B" />
    </Screen>
  ),
};

export const C: Story = {
  name: 'R4-C 顶部汇总 + 行内图标',
  render: () => (
    <Screen note="列表顶部一条「N 个订阅有问题」，点开只看有问题的；行内一个小图标，点行展开原因。" title="R4-C 顶部汇总 + 行内图标">
      <SubscriptionList variant="C" />
    </Screen>
  ),
};
