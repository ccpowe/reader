/**
 * 收藏 (PRODUCT §3.4), old app layout (device screenshot) at P25 values: search (P17), folder
 * tabs, then the list (the route passes a FeedList: "最近收藏" / "搜索结果", newest saved first).
 * Presentational frame.
 */
import { useState, type ReactNode } from 'react';
import { View } from 'react-native';

import { CollapsingScreen } from '@/components/CollapsingChrome';
import { Message, PageHeader, SearchField, Tabs } from '@/ui';
import { t } from '@/lib/i18n';

export function SavedView({
  user,
  onPressUser,
  banner,
  folders,
  folder,
  query = '',
  onSearch,
  onSelectFolder,
  children,
}: {
  user?: { name: string; avatarUrl?: string | null };
  /** the header's avatar: opens 我的 */
  onPressUser?: () => void;
  banner?: string | null;
  /** folder tabs after 全部: key ('' = 未分类) and label */
  folders: { key: string; label: string }[];
  /** null = 全部 */
  folder: string | null;
  query?: string;
  onSearch?: (query: string) => void;
  onSelectFolder?: (folder: string | null) => void;
  children: ReactNode;
}) {
  const [searching, setSearching] = useState(false);
  // The header, search and folder tabs slide away while the list scrolls (old app, PRODUCT §2),
  // except while the search field has the keyboard.
  return (
    <CollapsingScreen
      locked={searching}
      header={
        <>
          {banner ? (
            <Message bar kind="warning">
              {banner}
            </Message>
          ) : null}
          <PageHeader title={t().saved.title} onPressUser={onPressUser} user={user} />
          <View className="pb-1 pt-2">
            <SearchField
              autoCapitalize="none"
              autoCorrect={false}
              onBlur={() => setSearching(false)}
              onChangeText={onSearch}
              onFocus={() => setSearching(true)}
              placeholder={t().saved.search}
              returnKeyType="search"
              value={query}
            />
          </View>
          {folders.length > 0 ? (
            <Tabs<string | null>
              bordered
              onSelect={(key) => onSelectFolder?.(key)}
              options={[{ key: null, label: t().common.all }, ...folders]}
              selected={folder}
            />
          ) : null}
        </>
      }
      revealKey={folder ?? ''}
      tab="saved"
    >
      {children}
    </CollapsingScreen>
  );
}
