/** 收藏 (PRODUCT §3.4): saved items, newest saved first; folder tabs (current) and search (P17). */
import { FlatList, View } from 'react-native';

import { FeedCard, type FeedCardItem } from '@/components/FeedCard';
import { Divider, EmptyState, PageHeader, SearchField, Skeleton, Tabs } from '@/ui';

export function SavedView({
  folders,
  folder,
  query = '',
  items,
  loading = false,
  onSelectFolder,
}: {
  folders: string[];
  folder: string | null;
  query?: string;
  items: FeedCardItem[];
  loading?: boolean;
  onSelectFolder?: (folder: string | null) => void;
}) {
  return (
    <View className="flex-1 bg-canvas">
      <PageHeader title="收藏" />
      <View className="gap-2 pb-2">
        <SearchField defaultValue={query} placeholder="搜索标题和摘要" />
        <Tabs
          onSelect={(key) => onSelectFolder?.(key)}
          options={[{ key: null, label: '全部' }, ...folders.map((name) => ({ key: name, label: name }))]}
          selected={folder}
        />
      </View>
      <Divider />
      {loading ? (
        <View className="gap-6 p-4">
          {[0, 1, 2].map((index) => (
            <Skeleton key={index} lines={3} />
          ))}
        </View>
      ) : (
        <FlatList
          ItemSeparatorComponent={() => <Divider />}
          ListEmptyComponent={
            query ? (
              <EmptyState title="没有匹配的收藏" />
            ) : (
              <EmptyState description="在卡片右上角点书签即可收藏。" title="还没有收藏" />
            )
          }
          data={items}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => <FeedCard item={item} />}
        />
      )}
    </View>
  );
}
