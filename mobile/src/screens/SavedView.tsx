/**
 * 收藏 (PRODUCT §3.4), old app layout (device screenshot) at P25 values: search (P17), folder
 * tabs, "最近收藏", then the saved items as feed cards, newest saved first.
 */
import { FlatList, View } from 'react-native';

import { FeedCard, type FeedCardItem } from '@/components/FeedCard';
import { EmptyState, PageHeader, SearchField, SectionHeading, Skeleton, Tabs } from '@/ui';

export function SavedView({
  user,
  folders,
  folder,
  query = '',
  items,
  loading = false,
  onSelectFolder,
}: {
  user: { name: string; avatarUrl?: string | null };
  folders: string[];
  folder: string | null;
  query?: string;
  items: FeedCardItem[];
  loading?: boolean;
  onSelectFolder?: (folder: string | null) => void;
}) {
  return (
    <View className="flex-1 bg-canvas">
      <PageHeader title="收藏" user={user} />
      <View className="pb-1 pt-2">
        <SearchField defaultValue={query} placeholder="搜索收藏内容" />
      </View>
      <Tabs
        bordered
        onSelect={(key) => onSelectFolder?.(key)}
        options={[{ key: null, label: '全部' }, ...folders.map((name) => ({ key: name, label: name }))]}
        selected={folder}
      />
      {loading ? (
        <View className="gap-8 px-6 py-6">
          {[0, 1, 2].map((index) => (
            <Skeleton key={index} lines={3} />
          ))}
        </View>
      ) : (
        <FlatList
          ListEmptyComponent={
            query ? (
              <EmptyState title="没有匹配的收藏" />
            ) : (
              <EmptyState description="点卡片右下角的书签即可收藏。" title="还没有收藏" />
            )
          }
          ListHeaderComponent={items.length > 0 ? <SectionHeading className="-mb-3 pt-4" title={query ? '搜索结果' : '最近收藏'} /> : undefined}
          data={items}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => <FeedCard item={item} />}
        />
      )}
    </View>
  );
}
