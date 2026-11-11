/** The quietpaper building blocks as React Native components (src/ui), for style review. */
import type { Meta, StoryObj } from '@storybook/react-native';
import { IconBookmark, IconPlus, IconRefresh } from '@tabler/icons-react-native';
import { ScrollView, View } from 'react-native';

import { AttentionCount, Avatar, Badge, Button, Callout, Count, Dot, EmptyState, IconButton, Input, Message, SearchField, SectionHeading, Skeleton, Switch, Tabs, Tag, Text, Toast } from '@/ui';

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <View className="gap-2 pb-4">
      <SectionHeading strong title={title} />
      <View className="gap-2 px-4">{children}</View>
    </View>
  );
}

const meta: Meta = { title: '组件', parameters: { fullScreen: true } };
export default meta;
type Story = StoryObj;

export const Gallery: Story = {
  name: '组件 · 样板',
  render: () => (
    <ScrollView className="flex-1 bg-canvas">
      <Section title="文字层级">
        <Text level="h2">页面标题 h2</Text>
        <Text level="h3">卡片标题 h3</Text>
        <Text level="body-strong">正文加粗 body 600</Text>
        <Text level="body">正文 body 16 / 24</Text>
        <Text>界面 ui 14 / 20</Text>
        <Text level="desc" tone="secondary">
          说明 desc 13 / 18（secondary）
        </Text>
        <Text level="caption" tone="tertiary">
          元信息 caption 12 / 16（tertiary）
        </Text>
      </Section>
      <Section title="按钮">
        <View className="flex-row flex-wrap gap-2">
          <Button title="主操作" variant="primary" />
          <Button title="次级强调" variant="soft" />
          <Button title="普通" />
          <Button icon={IconRefresh} title="刷新" variant="ghost" />
          <Button on title="开启中" variant="ghost" />
          <Button disabled title="不可用" variant="primary" />
          <Button loading title="保存中" />
        </View>
        <Button large title="大按钮（表单）" variant="primary" />
        <View className="flex-row gap-2">
          <IconButton icon={IconPlus} label="添加" />
          <IconButton icon={IconBookmark} label="收藏" on />
          <IconButton disabled icon={IconRefresh} label="刷新" />
        </View>
      </Section>
      <Section title="标签 · 计数 · 徽标">
        <View className="flex-row flex-wrap items-center gap-2">
          <Tag>RSS</Tag>
          <Tag status>运行中</Tag>
          <Count value={12} />
          <AttentionCount value={2} />
          <Badge>共 7 条</Badge>
          <View className="flex-row items-center gap-1">
            <Dot tone="green" />
            <Text level="caption" tone="secondary">
              正常
            </Text>
          </View>
          <Avatar name="Simon" size={32} />
        </View>
      </Section>
      <Section title="分段切换">
        <View className="-mx-4">
          <Tabs onSelect={() => undefined} options={[{ key: 'a', label: '全部' }, { key: 'b', label: 'AI' }, { key: 'c', label: '新闻' }]} selected="b" />
        </View>
      </Section>
      <Section title="提示">
        <Message>普通提示</Message>
        <Message kind="success">已添加</Message>
        <Message kind="warning">可能有遗漏</Message>
        <Message action={<Button title="重试" variant="ghost" />} kind="danger">
          加载失败，显示的是之前的内容。
        </Message>
        <Callout>
          <Text>Callout：需要强调的一组内容，没有边框和阴影。</Text>
        </Callout>
        <Toast action={<Button title="撤销" variant="ghost" />} message="已取消收藏" />
      </Section>
      <Section title="输入">
        <Input label="服务器地址" placeholder="https://reader.example.com" />
        <Input error="连不上这个地址。" label="出错" placeholder="…" />
        <View className="-mx-4">
          <SearchField placeholder="搜索" />
        </View>
        <View className="flex-row items-center gap-3">
          <Switch label="开" value />
          <Switch label="关" value={false} />
        </View>
      </Section>
      <Section title="加载与空状态">
        <Skeleton lines={3} />
        <EmptyState description="添加订阅后，新内容会出现在这里。" title="还没有新内容" />
      </Section>
    </ScrollView>
  ),
};
