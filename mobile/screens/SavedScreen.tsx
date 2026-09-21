import { useEffect, useRef, useState } from 'react';
import {
  StyleSheet,
  Text,
  View,
} from 'react-native';
import type { Session } from '../lib/readerAuth';
import Reanimated, {
  type SharedValue,
} from 'react-native-reanimated';

import { CategoryPager } from '../components/CategoryPager';
import { EmptyState, ErrorState, listFeedbackStyles, LoadingBlock } from '../components/FeedbackState';
import {
  FOLDER_FILTER_CONTENT_GAP,
  FOLDER_FILTER_CONTROLS_HEIGHT,
  FolderFilterControls,
} from '../components/FolderFilterControls';
import { PageHeaderContent } from '../components/PageHeader';
import { SavedCard } from '../components/FeedCards';
import { TitleTranslationNotice } from '../components/TitleTranslationNotice';
import { useChromeStyle, useCollapsingChrome } from '../hooks/useCollapsingChrome';
import { useSavedContent } from '../hooks/useSavedContent';
import { useSourceFolders } from '../hooks/useSourceFolders';
import { useDebouncedValue } from '../hooks/useDebouncedValue';
import { UNCATEGORIZED_FOLDER, sourceFolder } from '../domain/folders';
import type { FeedItem } from '../lib/api';
import { PAGE_HEADER_HEIGHT, SCREEN_HORIZONTAL_PADDING, SCREEN_LIST_BOTTOM_PADDING } from '../ui/layout';
import { colors, spacing } from '../ui/tokens';
import { useTranslation } from '../i18n';
import type { ListPositionRegistry } from '../domain/listMemory';
import { useListPositionMemory } from '../hooks/useListPositionMemory';
import { useQueryClient } from '@tanstack/react-query';
import { useReaderRuntime } from '../lib/connection/react';
import { readerQueryKeys } from '../state/queryClient';
import { protectsQueryKeys, removeObsoleteQueries, trimInactiveInfiniteQueryWhenIdle } from '../state/queryLifecycle';

const SAVED_HEADER_HEIGHT = PAGE_HEADER_HEIGHT;
const SAVED_CONTROLS_HEIGHT = FOLDER_FILTER_CONTROLS_HEIGHT;

export function SavedScreen({ onProfile,
  active,
  chromeProgress,
  onOpenArticle,
  session,
}: {
  onProfile?: () => void;
  active: boolean;
  chromeProgress: SharedValue<number>;
  onOpenArticle: (item: FeedItem) => void;
  session: Session;
}) {
  const [selectedFolder, setSelectedFolder] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const positionRegistry = useRef<ListPositionRegistry>(new Map()).current;
  if (!active) return null;
  return <SavedScreenContent
    active
    chromeProgress={chromeProgress}
    onChangeQuery={setQuery}
    onOpenArticle={onOpenArticle}
    onProfile={onProfile}
    onSelectFolder={setSelectedFolder}
    positionRegistry={positionRegistry}
    query={query}
    selectedFolder={selectedFolder}
    session={session}
  />;
}

function SavedScreenContent({ onProfile,
  active,
  chromeProgress,
  onChangeQuery,
  onOpenArticle,
  onSelectFolder,
  positionRegistry,
  query,
  selectedFolder,
  session,
}: {
  onProfile?: () => void;
  active: boolean;
  chromeProgress: SharedValue<number>;
  onChangeQuery: (query: string) => void;
  onOpenArticle: (item: FeedItem) => void;
  onSelectFolder: (folder: string | null) => void;
  positionRegistry: ListPositionRegistry;
  query: string;
  selectedFolder: string | null;
  session: Session;
}) {
  const { t } = useTranslation('feed');
  const [searchFocused, setSearchFocused] = useState(false);
  const debouncedQuery = useDebouncedValue(query.trim(), 300);
  const { items, loading, message, queryKey, removeSaved, savedQuery, sources, titleTranslation } = useSavedContent(session, active, debouncedQuery);
  const queryClient = useQueryClient();
  const runtime = useReaderRuntime();
  const folderBySourceId = new Map(sources.map((source) => [source.source_id, sourceFolder(source)]));
  const folders = useSourceFolders(sources);
  const folderOptions = [null, ...folders];

  function visibleItemsForFolder(folder: string | null) {
    return items.filter((item) => {
      const itemFolder = folderBySourceId.get(item.source_id) ?? UNCATEGORIZED_FOLDER;
      return folder === null || itemFolder === folder;
    });
  }

  useEffect(() => {
    if (selectedFolder !== null && !folders.includes(selectedFolder)) onSelectFolder(null);
  }, [folders, onSelectFolder, selectedFolder]);

  useEffect(() => {
    void removeObsoleteQueries({
      isProtected: protectsQueryKeys([queryKey]),
      prefix: readerQueryKeys.savedPrefix(session.user.id, runtime?.identity.server_id),
      queryClient,
    });
    return () => {
      const memoryKey = savedMemoryKey(debouncedQuery, selectedFolder);
      queueMicrotask(() => {
        trimInactiveInfiniteQueryWhenIdle<FeedItem>({
          anchorId: positionRegistry.get(memoryKey)?.firstVisibleId ?? null,
          getItemId: savedItemId,
          queryClient,
          queryKey,
        });
        void removeObsoleteQueries({
          isProtected: protectsQueryKeys([queryKey]),
          prefix: readerQueryKeys.savedPrefix(session.user.id, runtime?.identity.server_id),
          queryClient,
        });
      });
    };
  }, [debouncedQuery, positionRegistry, queryClient, queryKey, runtime?.identity.server_id, selectedFolder, session.user.id]);

  const { onScroll, revealChrome } = useCollapsingChrome(active, chromeProgress, selectedFolder, {
    height: SAVED_HEADER_HEIGHT + SAVED_CONTROLS_HEIGHT, locked: searchFocused,
  });
  const headerStyle = useChromeStyle(chromeProgress, SAVED_HEADER_HEIGHT + SAVED_CONTROLS_HEIGHT);

  return (
    <View style={styles.savedPage}>
      <Reanimated.View pointerEvents="box-none" style={[styles.savedHeaderLayer, headerStyle]}>
        <View style={styles.savedHeader}>
          <PageHeaderContent compact title={t('saved')} session={session} onProfile={onProfile} />
        </View>
        <View style={styles.savedControlsLayer}>
          <FolderFilterControls
            active={active}
            onSearchFocusChange={setSearchFocused}
            folders={folders}
            onChangeQuery={onChangeQuery}
            onSelectFolder={(folder) => { revealChrome(); onSelectFolder(folder); }}
            query={query}
            searchAccessibilityLabel={t('searchSaved')}
            searchPlaceholder={t('searchSaved')}
            selectedFolder={selectedFolder}
          />
        </View>
      </Reanimated.View>
      <View style={styles.gestureArea}>
        <TitleTranslationNotice
          contentIds={titleTranslation.timedOutContentIds}
          message={titleTranslation.timeoutMessage}
          onRetry={titleTranslation.retryTimedOut}
        />
        <CategoryPager
          onPageTransitionStart={revealChrome}
          onSelect={(folder) => { revealChrome(); onSelectFolder(folder); }}
          options={folderOptions}
          pageStyle={styles.categoryPage}
          selected={selectedFolder}
          style={styles.categoryPager}
          renderPage={(folder) => {
            const visibleItems = visibleItemsForFolder(folder);
            return <SavedListPage
              active={active && selectedFolder === folder}
              avatarAccessToken={session.access_token}
              debouncedQuery={debouncedQuery}
              items={items}
              loading={loading}
              message={message}
              onOpenArticle={onOpenArticle}
              onRemove={removeSaved}
              onScroll={onScroll}
              positionRegistry={positionRegistry}
              savedQuery={savedQuery}
              selectedFolder={folder}
              visibleItems={visibleItems}
            />;
          }}
        />
      </View>
    </View>
  );
}

const savedItemId = (item: FeedItem) => item.content_id;
const savedMemoryKey = (query: string, folder: string | null) => `saved:${query}\u0000${folder ?? ''}`;

function SavedListPage({
  active,
  avatarAccessToken,
  debouncedQuery,
  items,
  loading,
  message,
  onOpenArticle,
  onRemove,
  onScroll,
  positionRegistry,
  savedQuery,
  selectedFolder,
  visibleItems,
}: {
  active: boolean;
  avatarAccessToken: string;
  debouncedQuery: string;
  items: FeedItem[];
  loading: boolean;
  message: string;
  onOpenArticle: (item: FeedItem) => void;
  onRemove: (item: FeedItem) => Promise<void>;
  onScroll: ReturnType<typeof useCollapsingChrome>['onScroll'];
  positionRegistry: ListPositionRegistry;
  savedQuery: ReturnType<typeof useSavedContent>['savedQuery'];
  selectedFolder: string | null;
  visibleItems: FeedItem[];
}) {
  const { t } = useTranslation('feed');
  const position = useListPositionMemory({
    active,
    itemId: savedItemId,
    items: visibleItems,
    memoryKey: savedMemoryKey(debouncedQuery, selectedFolder),
    onScroll,
    registry: positionRegistry,
  });
  return <Reanimated.FlatList
    contentContainerStyle={[styles.savedList, visibleItems.length === 0 && listFeedbackStyles.content]}
    data={visibleItems}
    initialNumToRender={8}
    keyExtractor={savedItemId}
    maxToRenderPerBatch={8}
    onEndReached={() => {
      if (active && savedQuery.hasNextPage && !savedQuery.isFetchingNextPage) void savedQuery.fetchNextPage();
    }}
    onEndReachedThreshold={0.5}
    onMomentumScrollEnd={position.onMomentumScrollEnd}
    onScroll={active ? position.onScroll : undefined}
    onScrollBeginDrag={position.onScrollBeginDrag}
    onScrollEndDrag={position.onScrollEndDrag}
    onScrollToIndexFailed={position.onScrollToIndexFailed}
    onViewableItemsChanged={position.onViewableItemsChanged}
    ref={position.listRef}
    renderItem={({ item }) => <SavedCard avatarAccessToken={avatarAccessToken} item={item} onOpen={() => onOpenArticle(item)} onRemove={() => void onRemove(item)} />}
    scrollEventThrottle={16}
    showsVerticalScrollIndicator={false}
    style={styles.savedListScroller}
    windowSize={5}
    ListHeaderComponent={<>
      {visibleItems.length > 0 && loading ? <LoadingBlock /> : null}
      {visibleItems.length > 0 && !loading && savedQuery.isError ? <ErrorState message={message || t('savedLoadFailed')} onRetry={() => savedQuery.refetch()} /> : null}
      {!loading && !savedQuery.isError ? <View style={styles.savedSummary}><Text style={styles.savedSummaryLabel}>{t(debouncedQuery ? 'searchResults' : 'recentSaved')}</Text></View> : null}
    </>}
    ListEmptyComponent={<View style={listFeedbackStyles.state}>
      {loading ? <LoadingBlock /> : savedQuery.isError ? <ErrorState message={message || t('savedLoadFailed')} onRetry={() => savedQuery.refetch()} /> : <EmptyState icon="bookmark-outline" message={t(debouncedQuery || items.length ? 'savedNoMatches' : 'savedEmpty')} />}
    </View>}
    ListFooterComponent={savedQuery.isFetchingNextPage ? <LoadingBlock /> : null}
  />;
}

const styles = StyleSheet.create({
  savedPage: { backgroundColor: colors.background, flex: 1, overflow: 'hidden' },
  savedHeaderLayer: { backgroundColor: colors.background, height: SAVED_HEADER_HEIGHT + SAVED_CONTROLS_HEIGHT, left: 0, position: 'absolute', right: 0, top: 0, zIndex: 4 },
  savedHeader: { paddingHorizontal: SCREEN_HORIZONTAL_PADDING },
  savedControlsLayer: { backgroundColor: colors.background, height: SAVED_CONTROLS_HEIGHT },
  gestureArea: { flex: 1 },
  categoryPager: { flex: 1 },
  categoryPage: { flex: 1 },
  savedListScroller: { flex: 1 },
  savedList: { gap: 0, paddingBottom: SCREEN_LIST_BOTTOM_PADDING, paddingTop: SAVED_HEADER_HEIGHT + SAVED_CONTROLS_HEIGHT + FOLDER_FILTER_CONTENT_GAP },
  inlineMessage: { color: colors.textTertiary, fontSize: 14, lineHeight: 20, marginBottom: spacing.md },
  savedSummary: { paddingHorizontal: SCREEN_HORIZONTAL_PADDING, alignItems: 'center', flexDirection: 'row', justifyContent: 'space-between', marginBottom: 2 },
  savedSummaryLabel: { color: colors.textTertiary, fontSize: 11, fontWeight: '600', letterSpacing: 1.1 },
  savedSummaryCount: { color: colors.textTertiary, fontSize: 11, fontWeight: '500' },
});
