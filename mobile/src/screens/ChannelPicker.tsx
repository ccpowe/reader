/**
 * Content of the 选择频道 sheet (PRODUCT §3.1), old app layout (device screenshot): search, the
 * number of new items with 全部标为已读 (every subscription), then subscriptions by folder — icon,
 * name, address, new count (tap it to mark that one read) and a checkbox for whether it shows in
 * 全部. Tapping a row shows that subscription alone; the sheet's X or a tap outside closes it.
 * Title and subtitle are the sheet's. Presentational.
 */
import { Pressable, ScrollView, View } from 'react-native';

import { SourceMark, type SourceKind } from '@/components/FeedCard';
import { Button, Checkbox, EmptyState, Message, SearchField, SectionHeading, SolidCount, Text } from '@/ui';
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
  onToggleHome,
  onMarkRead,
  onMarkAllRead,
  markingAll = false,
  error,
}: {
  groups: { key: string; label: string; rows: ChannelRow[] }[];
  query?: string;
  /** the subscription shown alone, if any */
  channel?: string | null;
  onSearch?: (query: string) => void;
  onSelect?: (id: string) => void;
  onToggleHome?: (id: string, include: boolean) => void;
  /** clear one subscription's new-content count without opening it */
  onMarkRead?: (id: string) => void;
  /** clear every subscription's new-content count */
  onMarkAllRead?: () => void;
  markingAll?: boolean;
  /** why marking read failed, shown here (a toast would sit behind the sheet) */
  error?: string | null;
}) {
  const totalNew = groups.reduce((sum, group) => sum + group.rows.reduce((n, row) => n + row.newCount, 0), 0);
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
      <View className="mx-4 mt-3 h-11 flex-row items-center justify-between" testID="align-inset">
        <Text level="desc" tone="secondary">
          {totalNew > 0 ? t().today.newTotal(totalNew) : t().today.noNew}
        </Text>
        <Button disabled={totalNew === 0} loading={markingAll} onPress={onMarkAllRead} title={t().today.markAllRead} variant="ghost" />
      </View>
      {error ? (
        <View className="mx-4 mb-2">
          <Message kind="danger">{error}</Message>
        </View>
      ) : null}
      <ScrollView keyboardShouldPersistTaps="handled">
        {visible.length === 0 ? <EmptyState title={t().today.noChannelMatch} /> : null}
        {visible.map((group) => (
          <View key={group.key}>
            <SectionHeading inset="sheet" title={group.label} />
            {group.rows.map((row) => (
              // Three separate targets (screen readers cannot reach controls nested in a button):
              // the channel itself, its count (mark read) and whether it shows in 全部.
              <View className="flex-row items-center gap-3 pr-4" key={row.id}>
                <Pressable
                  accessibilityRole="button"
                  accessibilityState={{ selected: row.id === channel }}
                  className="min-w-0 flex-1 flex-row items-center gap-3 py-2 pl-4 active:bg-pressed"
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
                </Pressable>
                {row.newCount > 0 ? (
                  <Pressable
                    accessibilityLabel={t().today.markRead(row.name)}
                    accessibilityRole="button"
                    hitSlop={8}
                    onPress={() => onMarkRead?.(row.id)}
                  >
                    <SolidCount value={row.newCount} />
                  </Pressable>
                ) : null}
                <Checkbox checked={row.includeInHome} label={t().today.showInAll(row.name)} onChange={(next) => onToggleHome?.(row.id, next)} />
              </View>
            ))}
          </View>
        ))}
      </ScrollView>
    </View>
  );
}
