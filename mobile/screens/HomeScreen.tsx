import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { MaterialCommunityIcons } from '@expo/vector-icons';
import type { Session } from '../lib/readerAuth';
import { useQueryClient } from '@tanstack/react-query';
import Reanimated, {
  type ScrollHandlerProcessed,
  type SharedValue,
} from 'react-native-reanimated';

import { setSavedContent, type FeedItem, type SourceListItem } from '../lib/api';
import { captureRuntimeContext, isRuntimeContextCurrent } from '../lib/connection';
import { ChannelPickerModal } from '../components/ChannelPickerModal';
import { Chip } from '../components/Chip';
import { CategoryPager } from '../components/CategoryPager';
import { FeedCardRow } from '../components/FeedCardRow';
import { EmptyState, ErrorState, listFeedbackStyles, LoadingBlock } from '../components/FeedbackState';
import { PageHeaderContent } from '../components/PageHeader';
import { SourceAvatar } from '../components/SourceAvatar';
import { TitleTranslationNotice } from '../components/TitleTranslationNotice';
import { useChromeStyle, useCollapsingChrome } from '../hooks/useCollapsingChrome';
import { prefetchInboxFeed, useInboxFeed } from '../hooks/useInboxFeed';
import { useSources } from '../hooks/useSources';
import { useTranslationPreference } from '../hooks/useTranslationPreference';
import { feedScopeCacheKey, folderFeedScope, sourceFeedScope, type FeedScope } from '../domain/feed';
import { folderLabel, sourceFolder } from '../domain/folders';
import { useSourceFolders } from '../hooks/useSourceFolders';
import { sourceDisplayName, sourceHealthLabel, sourceSecondaryLabel } from '../domain/source';
import { invalidateAfterSavedMutation } from '../state/invalidation';
import { beginSavedMutation } from '../state/savedMutation';
import { setCachedFeedSavedState } from '../state/cacheUpdates';
import { colors, radii, spacing } from '../ui/tokens';
import { PAGE_HEADER_HEIGHT, SCREEN_HORIZONTAL_PADDING, SCREEN_LIST_BOTTOM_PADDING } from '../ui/layout';
import { useReaderRuntime } from '../lib/connection/react';
import { i18n, useTranslation } from '../i18n';
import type { ListPositionRegistry } from '../domain/listMemory';
import { useListPositionMemory } from '../hooks/useListPositionMemory';
import { readerQueryKeys } from '../state/queryClient';
import { protectsQueryKeys, removeObsoleteQueries, trimInactiveInfiniteQueryWhenIdle } from '../state/queryLifecycle';

const HOME_FILTER_BAR_HEIGHT = 46;
const HOME_CHROME_HEIGHT = PAGE_HEADER_HEIGHT + HOME_FILTER_BAR_HEIGHT;

export function HomeScreen({
  active,
  chromeProgress,
  onClearSource,
  onOpenArticle,
  onProfile,
  onSelectSourceId,
  selectedSourceId,
  session,
}: {
  onProfile?: () => void;
  active: boolean;
  chromeProgress: SharedValue<number>;
  onClearSource: () => void;
  onOpenArticle: (item: FeedItem) => void;
  onSelectSourceId: (sourceId: string | null) => void;
  selectedSourceId: string | null;
  session: Session;
}) {
  const [selectedFolder, setSelectedFolder] = useState<string | null>(null);
  const positionRegistry = useRef<ListPositionRegistry>(new Map()).current;
  if (!active) return null;
  return <HomeScreenContent
    active
    chromeProgress={chromeProgress}
    onClearSource={onClearSource}
    onOpenArticle={onOpenArticle}
    onProfile={onProfile}
    onSelectFolder={setSelectedFolder}
    onSelectSourceId={onSelectSourceId}
    positionRegistry={positionRegistry}
    selectedFolder={selectedFolder}
    selectedSourceId={selectedSourceId}
    session={session}
  />;
}

function HomeScreenContent({
  active,
  chromeProgress,
  onClearSource,
  onOpenArticle,
  onProfile,
  onSelectFolder,
  onSelectSourceId,
  positionRegistry,
  selectedFolder,
  selectedSourceId,
  session,
}: {
  onProfile?: () => void;
  active: boolean;
  chromeProgress: SharedValue<number>;
  onClearSource: () => void;
  onOpenArticle: (item: FeedItem) => void;
  onSelectFolder: (folder: string | null) => void;
  onSelectSourceId: (sourceId: string | null) => void;
  positionRegistry: ListPositionRegistry;
  selectedFolder: string | null;
  selectedSourceId: string | null;
  session: Session;
}) {
  const { t } = useTranslation('feed');
  const [showChannelPicker, setShowChannelPicker] = useState(false);
  const readerClient = useQueryClient();
  const runtime = useReaderRuntime();
  const sourcesQuery = useSources(session, active);
  const translationPreference = useTranslationPreference(session, active);
  const sources = sourcesQuery.items;
  const folders = useSourceFolders(sources);
  const folderOptions = useMemo(() => [null, ...folders], [folders]);
  const selectedSource = useMemo(
    () => sources.find((source) => source.source_id === selectedSourceId) ?? null,
    [selectedSourceId, sources],
  );
  const protectedFeedKeys = useMemo(() => {
    if (translationPreference.isPending) return [];
    const scopes = selectedSource
      ? [sourceFeedScope(selectedSource.source_id)]
      : folderOptions
          .filter((_, index) => Math.abs(index - folderOptions.indexOf(selectedFolder)) <= 1)
          .map(folderFeedScope);
    return scopes.map((scope) => readerQueryKeys.feed(
      session.user.id,
      feedScopeCacheKey(scope),
      translationPreference.targetLocale,
      runtime?.identity.server_id,
    ));
  }, [folderOptions, runtime?.identity.server_id, selectedFolder, selectedSource, session.user.id, translationPreference.isPending, translationPreference.targetLocale]);

  useEffect(() => {
    if (!protectedFeedKeys.length) return;
    void removeObsoleteQueries({
      isProtected: protectsQueryKeys(protectedFeedKeys),
      prefix: readerQueryKeys.feedPrefix(session.user.id, runtime?.identity.server_id),
      queryClient: readerClient,
    });
    return () => {
      const currentScope = selectedSource
        ? sourceFeedScope(selectedSource.source_id)
        : folderFeedScope(selectedFolder);
      const currentKey = readerQueryKeys.feed(
        session.user.id,
        feedScopeCacheKey(currentScope),
        translationPreference.targetLocale,
        runtime?.identity.server_id,
      );
      queueMicrotask(() => void removeObsoleteQueries({
        isProtected: protectsQueryKeys([currentKey]),
        prefix: readerQueryKeys.feedPrefix(session.user.id, runtime?.identity.server_id),
        queryClient: readerClient,
      }));
    };
  }, [protectedFeedKeys, readerClient, runtime?.identity.server_id, selectedFolder, selectedSource, session.user.id, translationPreference.targetLocale]);

  const toggleSaved = useCallback(async (item: FeedItem) => {
    const context = captureRuntimeContext(runtime);
    if (!context) return;
    const release = beginSavedMutation(readerClient, session.user.id, context.serverId, item.content_id);
    if (!release) return;
    let committed = false;
    const nextSaved = !item.is_saved;
    setCachedFeedSavedState(readerClient, session.user.id, context.serverId, item.content_id, nextSaved, context);
    try {
      await setSavedContent(session, item.content_id, nextSaved, context.runtime);
      committed = true;
      if (!isRuntimeContextCurrent(context)) return;
      await invalidateAfterSavedMutation(readerClient, session.user.id, context.serverId, context);
    } catch (error) {
      if (!isRuntimeContextCurrent(context)) return;
      if (committed) return; // A failed refetch does not roll back a committed write.
      setCachedFeedSavedState(readerClient, session.user.id, context.serverId, item.content_id, item.is_saved, context);
      Alert.alert(i18n.t('feed:savedUpdateFailed'), error instanceof Error ? error.message : i18n.t('feed:retryLater'));
    } finally {
      release();
    }
  }, [readerClient, runtime, session]);

  useEffect(() => {
    if (selectedFolder !== null && !folders.includes(selectedFolder)) onSelectFolder(null);
  }, [folders, onSelectFolder, selectedFolder]);

  useEffect(() => {
    if (sourcesQuery.isSuccess && selectedSourceId !== null && selectedSource === null) {
      onClearSource();
    }
  }, [onClearSource, selectedSource, selectedSourceId, sourcesQuery.isSuccess]);

  useEffect(() => {
    if (!active || selectedSourceId !== null || translationPreference.isPending) return;
    const index = folderOptions.indexOf(selectedFolder);
    [folderOptions[index - 1], folderOptions[index + 1]]
        .filter((folder): folder is string | null => folder !== undefined)
      .forEach((folder) => void prefetchInboxFeed(
        readerClient,
        session,
        folderFeedScope(folder),
        translationPreference.targetLocale,
        runtime?.identity.server_id,
        runtime,
      ));
  }, [active, folderOptions, readerClient, runtime, selectedFolder, selectedSourceId, session, translationPreference.isPending, translationPreference.targetLocale]);

  const { onScroll, revealChrome } = useCollapsingChrome(
    active,
    chromeProgress,
    selectedSourceId ?? selectedFolder,
    { height: HOME_CHROME_HEIGHT },
  );
  const headerStyle = useChromeStyle(chromeProgress, HOME_CHROME_HEIGHT);

  return (
    <View style={styles.motionPage}>
      <Reanimated.View style={[styles.homeChromeLayer, headerStyle]}>
        <HomeHeader session={session} onProfile={onProfile} />
        <View style={styles.filterBar}>
          {selectedSource ? (
            <Pressable
              accessibilityLabel={t('clearChannelFilter', { name: sourceDisplayName(selectedSource) })}
              accessibilityRole="button"
              onPress={() => { revealChrome(); onClearSource(); }}
              style={({ pressed }) => [styles.selectedChannelChip, pressed && styles.filterPressed]}
            >
              <SourceAvatar accessToken={session.access_token} size={28} source={selectedSource} />
              <Text numberOfLines={1} style={styles.selectedChannelText}>{sourceDisplayName(selectedSource)}</Text>
              <MaterialCommunityIcons color={colors.surface} name="close" size={17} />
            </Pressable>
          ) : (
            <ScrollView
              contentContainerStyle={styles.filterRow}
              horizontal
              showsHorizontalScrollIndicator={false}
              style={styles.chipScroller}
            >
              <Chip active={selectedFolder === null} label={t('all')} onPress={() => { revealChrome(); onSelectFolder(null); }} />
              {folders.map((folder) => (
                <Chip
                  active={selectedFolder === folder}
                  key={folder}
                  label={folderLabel(folder)}
                  onPress={() => { revealChrome(); onSelectFolder(folder); }}
                />
              ))}
            </ScrollView>
          )}
          <Pressable
            accessibilityLabel={t(selectedSource ? 'switchChannel' : 'filterChannel')}
            accessibilityRole="button"
            onPress={() => { revealChrome(); setShowChannelPicker(true); }}
            style={({ pressed }) => [styles.channelFilterButton, pressed && styles.filterPressed]}
          >
            <MaterialCommunityIcons color={colors.textStrong} name="tune-variant" size={18} />
          </Pressable>
        </View>
      </Reanimated.View>
      {selectedSource ? (
        <InboxFeedPage
          active={active && !translationPreference.isPending}
          avatarAccessToken={session.access_token}
          onOpenArticle={onOpenArticle}
          onScroll={onScroll}
          onToggleSave={toggleSaved}
          positionRegistry={positionRegistry}
          scope={sourceFeedScope(selectedSource.source_id)}
          session={session}
          source={selectedSource}
          translationEngineId={translationPreference.effectiveEngineId}
          translationEngineFingerprint={translationPreference.effectiveEngineFingerprint}
          translationEnabled={translationPreference.enabled}
          translationLocale={translationPreference.targetLocale}
        />
      ) : (
        <CategoryPager
          onPageTransitionStart={revealChrome}
          onSelect={(folder) => { revealChrome(); onSelectFolder(folder); }}
          options={folderOptions}
          pageStyle={styles.categoryPage}
          renderPage={(folder) => (
            <InboxFeedPage
              active={active && !translationPreference.isPending && selectedFolder === folder}
              avatarAccessToken={session.access_token}
              onOpenArticle={onOpenArticle}
              onScroll={onScroll}
              onToggleSave={toggleSaved}
              positionRegistry={positionRegistry}
              scope={folderFeedScope(folder)}
              session={session}
              source={null}
              translationEngineId={translationPreference.effectiveEngineId}
              translationEngineFingerprint={translationPreference.effectiveEngineFingerprint}
              translationEnabled={translationPreference.enabled}
              translationLocale={translationPreference.targetLocale}
            />
          )}
          selected={selectedFolder}
          style={styles.categoryPager}
        />
      )}
      <ChannelPickerModal
        accessToken={session.access_token}
        onClose={() => setShowChannelPicker(false)}
        onSelect={(source) => { revealChrome(); onSelectSourceId(source?.source_id ?? null); }}
        preferredFolder={selectedFolder}
        selectedSourceId={selectedSourceId}
        sources={sources}
        visible={showChannelPicker}
      />
    </View>
  );
}

function InboxFeedPage({
  active,
  avatarAccessToken,
  onOpenArticle,
  onScroll,
  onToggleSave,
  positionRegistry,
  scope,
  session,
  source,
  translationEngineId,
  translationEngineFingerprint,
  translationEnabled,
  translationLocale,
}: {
  active: boolean;
  avatarAccessToken: string;
  onOpenArticle: (item: FeedItem) => void;
  onScroll: ScrollHandlerProcessed;
  onToggleSave: (item: FeedItem) => Promise<void>;
  positionRegistry: ListPositionRegistry;
  scope: FeedScope;
  session: Session;
  source: SourceListItem | null;
  translationEngineId: string | null;
  translationEngineFingerprint: string | null;
  translationEnabled: boolean;
  translationLocale: string;
}) {
  const { t } = useTranslation('feed');
  const feed = useInboxFeed(
    session,
    scope,
    active,
    translationLocale,
    translationEngineId,
    translationEnabled,
    translationEngineFingerprint,
  );
  const memoryKey = feedScopeCacheKey(scope);
  const position = useListPositionMemory({
    active,
    itemId: feedItemId,
    items: feed.items,
    memoryKey,
    onScroll,
    registry: positionRegistry,
  });
  const readerClient = useQueryClient();
  const runtime = useReaderRuntime();
  const queryKey = useMemo(
    () => readerQueryKeys.feed(session.user.id, memoryKey, translationLocale, runtime?.identity.server_id),
    [memoryKey, runtime?.identity.server_id, session.user.id, translationLocale],
  );
  useEffect(() => () => {
    queueMicrotask(() => trimInactiveInfiniteQueryWhenIdle<FeedItem>({
        anchorId: positionRegistry.get(memoryKey)?.firstVisibleId ?? null,
        getItemId: feedItemId,
        queryClient: readerClient,
        queryKey,
      }));
  }, [memoryKey, positionRegistry, queryKey, readerClient]);
  return (
    <Reanimated.FlatList
      contentContainerStyle={[styles.inboxList, feed.items.length === 0 && listFeedbackStyles.content]}
      data={feed.items}
      initialNumToRender={8}
      keyExtractor={(item) => item.content_id}
      maxToRenderPerBatch={8}
      onMomentumScrollEnd={position.onMomentumScrollEnd}
      onEndReached={() => { if (active && feed.hasNextPage && !feed.isFetchingNextPage) void feed.fetchNextPage(); }}
      onEndReachedThreshold={0.5}
      onRefresh={() => {
        if (!feed.refreshing && !feed.loading) void feed.refetch();
      }}
      onScroll={active ? position.onScroll : undefined}
      onScrollBeginDrag={position.onScrollBeginDrag}
      onScrollEndDrag={position.onScrollEndDrag}
      onScrollToIndexFailed={position.onScrollToIndexFailed}
      onViewableItemsChanged={position.onViewableItemsChanged}
      ref={position.listRef}
      refreshing={feed.refreshing}
      renderItem={({ item }) => <FeedCardRow
        avatarAccessToken={session.access_token}
        item={item}
        onOpenArticle={onOpenArticle}
        onRetryXTranslation={feed.xTranslation.retry}
        onToggleSave={onToggleSave}
        xTranslation={feed.xTranslation.byContentId.get(item.content_id)}
      />}
      scrollEventThrottle={16}
      showsVerticalScrollIndicator={false}
      windowSize={5}
      ListHeaderComponent={
        <>
          {source ? (
            <View style={styles.channelSummary}>
              <SourceAvatar accessToken={avatarAccessToken} size={50} source={source} />
              <View style={styles.channelSummaryCopy}>
                <Text numberOfLines={1} style={styles.channelSummaryTitle}>{sourceDisplayName(source)}</Text>
                <Text numberOfLines={1} style={styles.channelSummaryMeta}>
                  {[sourceSecondaryLabel(source), folderLabel(sourceFolder(source))].filter(Boolean).join(' · ')}
                </Text>
                <Text style={styles.channelSummaryStatus}>{sourceHealthLabel(source)}</Text>
              </View>
            </View>
          ) : null}
          <TitleTranslationNotice
            contentIds={feed.titleTranslation.timedOutContentIds}
            message={feed.titleTranslation.timeoutMessage}
            onRetry={feed.titleTranslation.retryTimedOut}
          />
          {feed.isError && feed.items.length > 0 ? (
            <View style={styles.feedError}>
              <ErrorState message={feed.message || t('contentLoadFailed')} onRetry={() => feed.refetch()} />
            </View>
          ) : null}
        </>
      }
      ListEmptyComponent={(
        <View style={listFeedbackStyles.state}>
          {feed.loading ? <LoadingBlock /> : feed.isError ? (
            <ErrorState message={feed.message || t('contentLoadFailed')} onRetry={() => feed.refetch()} />
          ) : feed.message ? (
            <EmptyState icon="newspaper-variant-outline" message={feed.message} />
          ) : null}
        </View>
      )}
      ListFooterComponent={feed.isFetchingNextPage ? <LoadingBlock /> : null}
    />
  );
}

const feedItemId = (item: FeedItem) => item.content_id;

function HomeHeader({ session, onProfile }: { session: Session; onProfile?: () => void }) {
  const { t } = useTranslation('feed');
  const today = new Date();
  const language = i18n.resolvedLanguage ?? i18n.language;
  const parts = new Intl.DateTimeFormat(language, {
    month: language.startsWith('zh') ? 'numeric' : 'short',
    day: 'numeric',
    weekday: language.startsWith('zh') ? 'long' : 'short',
  }).formatToParts(today);
  const date = t('todayDate', {
    month: parts.find((part) => part.type === 'month')?.value,
    day: parts.find((part) => part.type === 'day')?.value,
    weekday: parts.find((part) => part.type === 'weekday')?.value,
  });
  return (
    <View style={styles.homeHeader}>
      <PageHeaderContent
        compact
        title={t('today')}
        session={session}
        onProfile={onProfile}
        headingRight={(
          <Text accessibilityLabel={date} adjustsFontSizeToFit ellipsizeMode="tail" minimumFontScale={0.8} numberOfLines={1} style={styles.headerDate}>
            {date}
          </Text>
        )}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  motionPage: { backgroundColor: colors.background, flex: 1, overflow: 'hidden' },
  categoryPager: { flex: 1 },
  categoryPage: { flex: 1 },
  homeHeader: { paddingHorizontal: SCREEN_HORIZONTAL_PADDING },
  headerDate: { color: '#777b74', flexShrink: 1, fontSize: 12, maxHeight: 44, maxWidth: '100%', minWidth: 0, textAlign: 'right' },
  homeChromeLayer: { backgroundColor: colors.background, height: HOME_CHROME_HEIGHT, left: 0, position: 'absolute', right: 0, top: 0, zIndex: 4 },
  filterBar: { borderBottomWidth: 1, borderBottomColor: colors.border, alignItems: 'center', flexDirection: 'row', gap: spacing.sm, height: HOME_FILTER_BAR_HEIGHT, marginHorizontal: SCREEN_HORIZONTAL_PADDING },
  filterRow: { alignItems: 'center', flexDirection: 'row', gap: 21, paddingRight: spacing.sm },
  chipScroller: { flex: 1, height: 44 },
  channelFilterButton: { alignItems: 'center', justifyContent: 'center', minHeight: 44, width: 44 },
  channelFilterText: { color: colors.textStrong, fontSize: 13, fontWeight: '700' },
  selectedChannelChip: { alignItems: 'center', backgroundColor: colors.textStrong, borderRadius: radii.pill, flex: 1, flexDirection: 'row', gap: spacing.sm, minHeight: 44, overflow: 'hidden', paddingHorizontal: 8, paddingRight: 13 },
  selectedChannelText: { color: colors.surface, flex: 1, fontSize: 13, fontWeight: '700' },
  filterPressed: { opacity: 0.72 },
  inboxList: { gap: 0, paddingBottom: SCREEN_LIST_BOTTOM_PADDING, paddingTop: HOME_CHROME_HEIGHT },
  feedError: { paddingVertical: spacing.xl },
  feedSectionHeader: { alignItems: 'center', flexDirection: 'row', justifyContent: 'space-between', marginBottom: 0, marginTop: 8, paddingHorizontal: SCREEN_HORIZONTAL_PADDING },
  feedSectionHeaderAfterChannel: { marginTop: 22 },
  feedSectionTitle: { color: colors.textStrong, fontSize: 20, fontWeight: '700', letterSpacing: -0.35 },
  channelSummary: { alignItems: 'center', backgroundColor: colors.surface, borderColor: colors.border, borderRadius: radii.md, borderWidth: 1, flexDirection: 'row', gap: spacing.md, marginHorizontal: 20, marginTop: 24, padding: 15 },
  channelSummaryCopy: { flex: 1 },
  channelSummaryTitle: { color: colors.textStrong, fontSize: 17, fontWeight: '700', letterSpacing: -0.2 },
  channelSummaryMeta: { color: colors.textTertiary, fontSize: 12, marginTop: 3 },
  channelSummaryStatus: { color: colors.textSecondary, fontSize: 12, marginTop: 5 },
  refreshLabel: { alignItems: 'center', flexDirection: 'row', gap: 4 },
  refreshLabelDisabled: { opacity: 0.55 },
  refreshLabelPressed: { opacity: 0.72 },
  refreshText: { color: colors.textTertiary, fontSize: 12 },
});
