import { useEffect, useState } from 'react';
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
  const { t } = useTranslation('feed');
  const [searchFocused, setSearchFocused] = useState(false);
  const [selectedFolder, setSelectedFolder] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const debouncedQuery = useDebouncedValue(query.trim(), 300);
  const { items, loading, message, removeSaved, savedQuery, sources, titleTranslation } = useSavedContent(session, active, debouncedQuery);
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
    if (selectedFolder !== null && !folders.includes(selectedFolder)) setSelectedFolder(null);
  }, [folders, selectedFolder]);

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
            onChangeQuery={setQuery}
            onSelectFolder={(folder) => { revealChrome(); setSelectedFolder(folder); }}
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
          onSelect={(folder) => { revealChrome(); setSelectedFolder(folder); }}
          options={folderOptions}
          pageStyle={styles.categoryPage}
          selected={selectedFolder}
          style={styles.categoryPager}
          renderPage={(folder) => {
            const visibleItems = visibleItemsForFolder(folder);
            return (
              <Reanimated.FlatList
                contentContainerStyle={[styles.savedList, visibleItems.length === 0 && listFeedbackStyles.content]}
                data={visibleItems}
                initialNumToRender={8}
                keyExtractor={(item) => item.content_id}
                maxToRenderPerBatch={8}
                onEndReached={() => {
                  if (active && selectedFolder === folder && savedQuery.hasNextPage && !savedQuery.isFetchingNextPage) {
                    void savedQuery.fetchNextPage();
                  }
                }}
                onEndReachedThreshold={0.5}
                onScroll={active && selectedFolder === folder ? onScroll : undefined}
                renderItem={({ item }) => <SavedCard avatarAccessToken={session.access_token} item={item} onOpen={() => onOpenArticle(item)} onRemove={() => void removeSaved(item)} />}
                scrollEventThrottle={16}
                showsVerticalScrollIndicator={false}
                style={styles.savedListScroller}
                windowSize={5}
                ListHeaderComponent={
                  <>
                    {visibleItems.length > 0 && loading ? <LoadingBlock /> : null}
                    {visibleItems.length > 0 && !loading && savedQuery.isError ? <ErrorState message={message || t('savedLoadFailed')} onRetry={() => savedQuery.refetch()} /> : null}
                    {!loading && !savedQuery.isError ? <View style={styles.savedSummary}><Text style={styles.savedSummaryLabel}>{t(debouncedQuery ? 'searchResults' : 'recentSaved')}</Text></View> : null}
                  </>
                }
                ListEmptyComponent={(
                  <View style={listFeedbackStyles.state}>
                    {loading ? <LoadingBlock /> : savedQuery.isError ? (
                      <ErrorState message={message || t('savedLoadFailed')} onRetry={() => savedQuery.refetch()} />
                    ) : (
                      <EmptyState icon="bookmark-outline" message={t(debouncedQuery || items.length ? 'savedNoMatches' : 'savedEmpty')} />
                    )}
                  </View>
                )}
                ListFooterComponent={savedQuery.isFetchingNextPage ? <LoadingBlock /> : null}
              />
            );
          }}
        />
      </View>
    </View>
  );
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
