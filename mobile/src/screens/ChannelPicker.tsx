/**
 * Content of the 选择频道 sheet (PRODUCT §3.1), old app layout (device screenshot): search, a
 * "返回分类内容" choice that clears a single-channel view, then subscriptions by folder — icon,
 * name, address, new count, and a checkbox for whether it shows in 全部. Tapping a row shows that
 * subscription alone. Title and subtitle are the sheet's.
 */
import { IconCircleCheckFilled, IconLayoutGrid } from '@tabler/icons-react-native';
import { Pressable, ScrollView, View } from 'react-native';

import { SourceMark } from '@/components/FeedCard';
import type { SubscriptionItem } from '@/components/SubscriptionRow';
import { Checkbox, Icon, SearchField, SectionHeading, SolidCount, Text } from '@/ui';

export function ChannelPicker({
  folders,
  hidden = [],
  channel,
}: {
  folders: { name: string; items: SubscriptionItem[] }[];
  /** subscriptions unticked, i.e. left out of 全部 */
  hidden?: string[];
  /** the subscription shown alone, if any */
  channel?: string | null;
}) {
  return (
    <View className="-mx-4" style={{ maxHeight: 560 }}>
      <SearchField inset="sheet" placeholder="搜索频道、网站或分类" />
      <Pressable accessibilityRole="button" className="mx-4 mt-3 flex-row items-center gap-3 rounded-xl bg-tint p-3 active:bg-pressed" testID="align-inset">
        <View className="h-11 w-11 items-center justify-center rounded-xl bg-sunken">
          <Icon icon={IconLayoutGrid} size="toolbar" tone="strong" />
        </View>
        <View className="flex-1">
          <Text level="body-strong">返回分类内容</Text>
          <Text level="desc" tone="secondary">
            清除单频道筛选
          </Text>
        </View>
        {channel ? null : <Icon icon={IconCircleCheckFilled} size="toolbar" tone="strong" />}
      </Pressable>
      <ScrollView>
        {folders.map((folder) => (
          <View key={folder.name}>
            <SectionHeading inset="sheet" title={folder.name} />
            {folder.items.map((item) => (
              <Pressable accessibilityRole="button" className="flex-row items-center gap-3 px-4 py-2 active:bg-pressed" key={item.id}>
                <SourceMark avatarUrl={item.avatarUrl} kind={item.kind} size={44} />
                <View className="min-w-0 flex-1">
                  <Text level="body-strong" numberOfLines={1}>
                    {item.name}
                  </Text>
                  <Text level="desc" numberOfLines={1} tone="secondary">
                    {item.address}
                  </Text>
                </View>
                {item.newCount > 0 ? <SolidCount value={item.newCount} /> : null}
                <Checkbox checked={!hidden.includes(item.id)} label={`在全部中显示 ${item.name}`} />
              </Pressable>
            ))}
          </View>
        ))}
      </ScrollView>
    </View>
  );
}
