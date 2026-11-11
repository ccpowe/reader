/**
 * 订阅 (PRODUCT §3.3): search, subscriptions grouped by folder with health lines (P23 R4-B),
 * add a subscription from the header. Unsubscribing asks first (PRODUCT; overrides the style
 * pack's "act + Undo" default — see design/reports/step5-REPORT.md).
 */
import { IconPlus } from '@tabler/icons-react-native';
import { ScrollView, View } from 'react-native';

import { SubscriptionRow, type SubscriptionItem } from '@/components/SubscriptionRow';
import { Button, Divider, EmptyState, IconButton, Input, Message, PageHeader, SearchField, SectionHeading, Tabs } from '@/ui';

export function SubscriptionsView({
  folders,
  expandedId,
  query = '',
  onAdd,
  onToggleRow,
}: {
  folders: { name: string; items: SubscriptionItem[] }[];
  expandedId?: string | null;
  query?: string;
  onAdd?: () => void;
  onToggleRow?: (id: string) => void;
}) {
  const needle = query.trim().toLowerCase();
  const visible = folders
    .map((folder) => ({ ...folder, items: folder.items.filter((item) => item.name.toLowerCase().includes(needle)) }))
    .filter((folder) => folder.items.length > 0);
  const empty = folders.every((folder) => folder.items.length === 0);
  return (
    <View className="flex-1 bg-canvas">
      <PageHeader actions={<IconButton icon={IconPlus} label="添加订阅" onPress={onAdd} />} title="订阅" />
      {empty ? (
        <EmptyState
          action={<Button large onPress={onAdd} title="添加订阅" variant="primary" />}
          description="可以添加 RSS、网站（自动找 RSS）、YouTube 频道、X 账号或 Reddit 社区。"
          title="还没有订阅"
        />
      ) : (
        <ScrollView>
          <View className="pb-2">
            <SearchField defaultValue={query} placeholder="搜索订阅" />
          </View>
          {visible.length === 0 ? <EmptyState title="没有匹配的订阅" /> : null}
          {visible.map((folder) => (
            <View key={folder.name}>
              <SectionHeading title={folder.name} />
              {folder.items.map((item, index) => (
                <View key={item.id}>
                  {index > 0 ? <Divider inset /> : null}
                  <SubscriptionRow expanded={expandedId === item.id} item={item} onPress={() => onToggleRow?.(item.id)} />
                </View>
              ))}
            </View>
          ))}
        </ScrollView>
      )}
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
        <Tabs onSelect={(key) => onType?.(key)} options={types.map(({ key, label }) => ({ key, label }))} selected={type} />
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
        <SectionHeading strong title="文件夹" />
        <Tabs onSelect={() => undefined} options={[{ key: null, label: '不放进文件夹' }, ...folders.map((name) => ({ key: name, label: name }))]} selected={folder} />
      </View>
      {submitting ? <Message>正在查找并添加……</Message> : null}
      <Button large loading={submitting} title="添加" variant="primary" />
    </View>
  );
}
