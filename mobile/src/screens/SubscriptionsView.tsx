/**
 * 订阅 (PRODUCT §3.3), old app layout (device screenshots) at P25 values: search, "我的分类" with
 * the total, a card per folder; opening a folder lists its subscriptions with health lines (P23
 * R4-B). The floating + adds a subscription. Searching lists matching subscriptions directly.
 * Unsubscribing asks first (PRODUCT; overrides the style pack's "act + Undo" default).
 */
import { IconChevronLeft, IconPlus } from '@tabler/icons-react-native';
import { Pressable, ScrollView, View } from 'react-native';

import { FolderCard } from '@/components/FolderCard';
import { SubscriptionRow, type SubscriptionItem } from '@/components/SubscriptionRow';
import { Button, EmptyState, Fab, Icon, Input, Message, PageHeader, SearchField, SectionHeading, Tabs, Text } from '@/ui';

type Folder = { name: string; items: SubscriptionItem[] };

function FolderBar({ name, count, onBack }: { name: string; count: number; onBack?: () => void }) {
  return (
    <View className="h-14 flex-row items-center px-6">
      <Pressable accessibilityRole="button" className="h-11 flex-row items-center gap-1 active:opacity-60" onPress={onBack}>
        <Icon icon={IconChevronLeft} />
        <Text level="ui-strong">全部分类</Text>
      </Pressable>
      <View className="flex-1" />
      <Text level="caption" numberOfLines={1} tone="tertiary">
        {name} · {count}
      </Text>
    </View>
  );
}

export function SubscriptionsView({
  user,
  folders,
  openFolder,
  expandedId,
  query = '',
  onAdd,
  onOpenFolder,
  onCloseFolder,
  onToggleRow,
}: {
  user: { name: string; avatarUrl?: string | null };
  folders: Folder[];
  /** the folder being shown; none = the folder cards */
  openFolder?: string | null;
  expandedId?: string | null;
  query?: string;
  onAdd?: () => void;
  onOpenFolder?: (name: string) => void;
  onCloseFolder?: () => void;
  onToggleRow?: (id: string) => void;
}) {
  const total = folders.reduce((sum, folder) => sum + folder.items.length, 0);
  const needle = query.trim().toLowerCase();
  const matches = needle
    ? folders.flatMap((folder) => folder.items).filter((item) => `${item.name} ${item.address}`.toLowerCase().includes(needle))
    : [];
  const current = openFolder ? folders.find((folder) => folder.name === openFolder) : undefined;
  const rows = (items: SubscriptionItem[]) =>
    items.map((item) => <SubscriptionRow expanded={expandedId === item.id} item={item} key={item.id} onPress={() => onToggleRow?.(item.id)} />);

  return (
    <View className="flex-1 bg-canvas">
      <PageHeader title="订阅" user={user} />
      {total === 0 ? (
        <EmptyState
          action={<Button large onPress={onAdd} title="添加订阅" variant="primary" />}
          description="可以添加 RSS、网站（自动找 RSS）、YouTube 频道、X 账号或 Reddit 社区。"
          title="还没有订阅"
        />
      ) : (
        <ScrollView contentContainerClassName="pb-24">
          <View className="pb-2 pt-2">
            <SearchField defaultValue={query} placeholder="搜索订阅源" />
          </View>
          {needle ? (
            <>
              <SectionHeading aside={`${matches.length} 个`} title="搜索结果" />
              {matches.length === 0 ? <EmptyState title="没有匹配的订阅源" /> : rows(matches)}
            </>
          ) : current ? (
            <>
              <FolderBar count={current.items.length} name={current.name} onBack={onCloseFolder} />
              {rows(current.items)}
            </>
          ) : (
            <>
              <SectionHeading aside={`${total} 个订阅源`} title="我的分类" />
              <View className="gap-3">
                {folders.map((folder) => (
                  <FolderCard items={folder.items} key={folder.name} name={folder.name} onPress={() => onOpenFolder?.(folder.name)} />
                ))}
              </View>
            </>
          )}
        </ScrollView>
      )}
      {total > 0 ? <Fab icon={IconPlus} label="添加订阅" onPress={onAdd} /> : null}
    </View>
  );
}

const types = [
  { key: 'rss', label: 'RSS', placeholder: 'https://example.com/feed.xml' },
  { key: 'website', label: '网站', placeholder: 'https://example.com' },
  { key: 'youtube', label: 'YouTube', placeholder: '频道链接或 @handle' },
  { key: 'x', label: 'X', placeholder: '@handle' },
  { key: 'reddit', label: 'Reddit', placeholder: 'r/python' },
] as const;
export type SubscriptionType = (typeof types)[number]['key'];

/** Content of the "添加订阅" sheet: type, address, folder; one primary action. */
export function AddSubscriptionForm({
  type,
  value = '',
  folder,
  folders,
  error,
  submitting = false,
  onType,
}: {
  type: SubscriptionType;
  value?: string;
  folder: string | null;
  folders: string[];
  error?: string | null;
  submitting?: boolean;
  onType?: (type: SubscriptionType) => void;
}) {
  const current = types.find((item) => item.key === type)!;
  return (
    <View className="gap-3">
      <View className="-mx-4">
        <Tabs inset="sheet" onSelect={(key) => onType?.(key)} options={types.map(({ key, label }) => ({ key, label }))} selected={type} />
      </View>
      <Input
        autoCapitalize="none"
        autoCorrect={false}
        defaultValue={value}
        error={error}
        hint={type === 'website' ? 'Reader 会在网站上找它的 RSS / Atom。' : undefined}
        label="地址"
        placeholder={current.placeholder}
      />
      <View className="-mx-4">
        <SectionHeading inset="sheet" title="分类" />
        <Tabs inset="sheet" onSelect={() => undefined} options={[{ key: null, label: '未分类' }, ...folders.map((name) => ({ key: name, label: name }))]} selected={folder} />
      </View>
      {submitting ? <Message>正在查找并添加……</Message> : null}
      <Button large loading={submitting} title="添加" variant="primary" />
    </View>
  );
}
