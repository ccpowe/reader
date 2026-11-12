/**
 * Content of the 选择频道 sheet (PRODUCT §3.1), old app layout (device screenshot): search, a
 * "返回分类内容" choice that clears a single-channel view, then subscriptions by folder — icon,
 * name, address, new count, and a checkbox for whether it shows in 全部. Tapping a row shows that
 * subscription alone. Title and subtitle are the sheet's. Presentational.
 */
import { IconCircleCheckFilled, IconLayoutGrid } from '@tabler/icons-react-native';
import { Pressable, ScrollView, View } from 'react-native';

import { SourceMark, type SourceKind } from '@/components/FeedCard';
import { Checkbox, EmptyState, Icon, SearchField, SectionHeading, SolidCount, Text } from '@/ui';
import { t } from '@/lib/i18n';

export type ChannelRow = {
  id: string;
  name: string;
  kind: SourceKind;
  address: string;
  newCount: number;
  avatarUrl?: string | null;
  includeInHome: boolean;
};

export function ChannelPicker({
  groups,
  query = '',
  channel,
  onSearch,
  onSelect,
  onClear,
  onToggleHome,
}: {
  groups: { key: string; label: string; rows: ChannelRow[] }[];
  query?: string;
  /** the subscription shown alone, if any */
  channel?: string | null;
  onSearch?: (query: string) => void;
  onSelect?: (id: string) => void;
  onClear?: () => void;
  onToggleHome?: (id: string, include: boolean) => void;
}) {
  const needle = query.trim().toLowerCase();
  const visible = groups
    .map((group) => ({
      ...group,
      rows: needle
        ? group.rows.filter((row) => `${row.name} ${row.address} ${group.label}`.toLowerCase().includes(needle))
        : group.rows,
    }))
    .filter((group) => group.rows.length > 0);
  return (
    <View className="-mx-4" style={{ maxHeight: 560 }}>
      <SearchField inset="sheet" onChangeText={onSearch} placeholder={t().today.searchChannels} value={query} />
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ selected: !channel }}
        className="mx-4 mt-3 flex-row items-center gap-3 rounded-xl bg-tint p-3 active:bg-pressed"
        onPress={onClear}
        testID="align-inset"
      >
        <View className="h-11 w-11 items-center justify-center rounded-xl bg-sunken">
          <Icon icon={IconLayoutGrid} size="toolbar" tone="strong" />
        </View>
        <View className="flex-1">
          <Text level="body-strong">{t().today.backToFolders}</Text>
          <Text level="desc" tone="secondary">
            {t().today.clearChannel}
          </Text>
        </View>
        {channel ? null : <Icon icon={IconCircleCheckFilled} size="toolbar" tone="strong" />}
      </Pressable>
      <ScrollView keyboardShouldPersistTaps="handled">
        {visible.length === 0 ? <EmptyState title={t().today.noChannelMatch} /> : null}
        {visible.map((group) => (
          <View key={group.key}>
            <SectionHeading inset="sheet" title={group.label} />
            {group.rows.map((row) => (
              <Pressable
                accessibilityRole="button"
                accessibilityState={{ selected: row.id === channel }}
                className="flex-row items-center gap-3 px-4 py-2 active:bg-pressed"
                key={row.id}
                onPress={() => onSelect?.(row.id)}
              >
                <SourceMark avatarUrl={row.avatarUrl} kind={row.kind} size={44} />
                <View className="min-w-0 flex-1">
                  <Text level="body-strong" numberOfLines={1}>
                    {row.name}
                  </Text>
                  <Text level="desc" numberOfLines={1} tone="secondary">
                    {row.address}
                  </Text>
                </View>
                {row.newCount > 0 ? <SolidCount value={row.newCount} /> : null}
                <Checkbox checked={row.includeInHome} label={t().today.showInAll(row.name)} onChange={(next) => onToggleHome?.(row.id, next)} />
              </Pressable>
            ))}
          </View>
        ))}
      </ScrollView>
    </View>
  );
}
