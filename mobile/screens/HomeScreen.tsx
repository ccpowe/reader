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

import {
  markSourceSubscriptionViewed,
  setSavedContent,
  type FeedItem,
  type SourceListItem,
  updateSourceSubscriptionHomeInclusion,
} from '../lib/api';
import { captureRuntimeContext, isRuntimeContextCurrent, type ReaderRuntimeContext } from '../lib/connection';
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
import { invalidateAfterSavedMutation, invalidateAfterSourceMutation } from '../state/invalidation';
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
  channelVisitId,
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
  channelVisitId: number;
  chromeProgress: SharedValue<number>;
  onClearSource: () => void;
  onOpenArticle: (item: FeedItem) => void;
  onSelectSourceId: (sourceId: string | null) => void;
  selectedSourceId: string | null;
  session: Session;
}) {
  const [selectedFolder, setSelectedFolder] = useState<string | null>(null);
  const positionRegistry = useRef<ListPositionRegistry>(new Map()).current;
  return <HomeScreenContent
    active={active}
    channelVisitId={channelVisitId}
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
  channelVisitId,
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
  channelVisitId: number;
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
  const [pendingHomeSubscriptionIds, setPendingHomeSubscriptionIds] = useState<ReadonlySet<string>>(() => new Set());
  const homeMutationContexts = useRef(new Map<string, ReturnType<typeof captureRuntimeContext>>());
  const readerClient = useQueryClient();
  const runtime = useReaderRuntime();
  const sourcesQuery = useSources(session, active);
  const refetchSources = sourcesQuery.refetch;
  const translationPreference = useTranslationPreference(session, active);
  const sources = sourcesQuery.items;
  const homeNewCount = useMemo(
    () => sources.reduce((total, source) => source.include_in_home !== false && typeof source.new_count === 'number'
      ? total + Math.max(0, source.new_count)
      : total, 0),
    [sources],
  );
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
    homeMutationContexts.current.clear();
    setPendingHomeSubscriptionIds(new Set());
  }, [runtime?.generation, runtime?.identity.server_id, session.user.id]);

  useEffect(() => {
    if (active) void refetchSources();
  }, [active, refetchSources]);

  const toggleSourceInHome = useCallback(async (source: SourceListItem, includeInHome: boolean) => {
    if (homeMutationContexts.current.has(source.subscription_id)) return;
    const context = captureRuntimeContext(runtime);
    if (!context) return;
    homeMutationContexts.current.set(source.subscription_id, context);
    setPendingHomeSubscriptionIds((current) => new Set(current).add(source.subscription_id));
    try {
      const sourcesKey = readerQueryKeys.sources(session.user.id, context.serverId);
      await readerClient.cancelQueries({ queryKey: sourcesKey });
      if (!isRuntimeContextCurrent(context)) return;
      const updated = await updateSourceSubscriptionHomeInclusion(
        session,
        source.subscription_id,
        includeInHome,
        context.runtime,
      );
      if (!isRuntimeContextCurrent(context)) return;
      await readerClient.cancelQueries({ queryKey: sourcesKey });
      if (!isRuntimeContextCurrent(context)) return;
      readerClient.setQueryData<SourceListItem[]>(
        sourcesKey,
        (current) => current?.map((item) => item.subscription_id === updated.subscription_id
          ? { ...item, include_in_home: updated.include_in_home }
          : item),
      );
      await invalidateAfterSourceMutation(readerClient, session.user.id, context.serverId, context);
    } catch (error) {
      if (isRuntimeContextCurrent(context)) {
        Alert.alert(i18n.t('feed:homeChannelUpdateFailed'), error instanceof Error ? error.message : i18n.t('feed:retryLater'));
      }
    } finally {
      if (homeMutationContexts.current.get(source.subscription_id) === context) {
        homeMutationContexts.current.delete(source.subscription_id);
        setPendingHomeSubscriptionIds((current) => {
          const next = new Set(current);
          next.delete(source.subscription_id);
          return next;
        });
      }
    }
  }, [readerClient, runtime, session]);

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
    <View style={styles.motionPage} testID="home_screen">
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
            accessibilityLabel={`${t(selectedSource ? 'switchChannel' : 'filterChannel')}${homeNewCount > 0 ? `, ${homeNewCount}` : ''}`}
            accessibilityRole="button"
            onPress={() => { revealChrome(); setShowChannelPicker(true); void refetchSources(); }}
            style={({ pressed }) => [styles.channelFilterButton, pressed && styles.filterPressed]}
          >
            <MaterialCommunityIcons color={colors.textStrong} name="tune-variant" size={18} />
            {homeNewCount > 0 ? (
              <View style={styles.channelFilterBadge}>
                <Text numberOfLines={1} style={styles.channelFilterBadgeText}>{homeNewCount > 999 ? '999+' : homeNewCount}</Text>
              </View>
            ) : null}
          </Pressable>
        </View>
      </Reanimated.View>
      {selectedSource ? (
        <InboxFeedPage
          active={active && !translationPreference.isPending}
          avatarAccessToken={session.access_token}
          channelVisitId={channelVisitId}
          onOpenArticle={onOpenArticle}
          onRefreshSources={refetchSources}
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
              channelVisitId={0}
              onOpenArticle={onOpenArticle}
              onRefreshSources={refetchSources}
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
        onToggleHome={(source, includeInHome) => { void toggleSourceInHome(source, includeInHome); }}
        pendingHomeSubscriptionIds={pendingHomeSubscriptionIds}
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
  channelVisitId,
  onOpenArticle,
  onRefreshSources,
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
  channelVisitId: number;
  onOpenArticle: (item: FeedItem) => void;
  onRefreshSources: () => Promise<unknown>;
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
  const refetchFeed = feed.refetch;
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
  const activeChannelVisit = useRef(0);
  const attemptedChannelVisit = useRef(0);
  const confirmedChannelVisit = useRef(0);
  const confirmingChannelVisit = useRef(0);
  const queryKey = useMemo(
    () => readerQueryKeys.feed(session.user.id, memoryKey, translationLocale, runtime?.identity.server_id),
    [memoryKey, runtime?.identity.server_id, session.user.id, translationLocale],
  );
  useEffect(() => {
    activeChannelVisit.current = active && source ? channelVisitId : 0;
    return () => {
      if (activeChannelVisit.current === channelVisitId) activeChannelVisit.current = 0;
    };
  }, [active, channelVisitId, source]);
  const confirmChannelVisit = useCallback(async (
    result: Awaited<ReturnType<typeof refetchFeed>>,
    visitId: number,
    context: ReaderRuntimeContext,
  ) => {
    if (!source || result.isError || activeChannelVisit.current !== visitId ||
      confirmedChannelVisit.current === visitId || confirmingChannelVisit.current === visitId) return;
    const token = result.data?.pages[0]?.channel_update_token;
    if (typeof token !== 'string' || !token) return;
    if (!isRuntimeContextCurrent(context)) return;
    confirmingChannelVisit.current = visitId;
    try {
      const sourcesKey = readerQueryKeys.sources(session.user.id, context.serverId);
      await readerClient.cancelQueries({ queryKey: sourcesKey });
      if (!isRuntimeContextCurrent(context) || activeChannelVisit.current !== visitId) return;
      const updated = await markSourceSubscriptionViewed(
        session,
        source.subscription_id,
        token,
        context.runtime,
      );
      if (!isRuntimeContextCurrent(context)) return;
      await readerClient.cancelQueries({ queryKey: sourcesKey });
      if (!isRuntimeContextCurrent(context)) return;
      readerClient.setQueryData<SourceListItem[]>(
        sourcesKey,
        (current) => current?.map((item) => item.subscription_id === updated.subscription_id
          ? { ...item, new_count: updated.new_count }
          : item),
      );
      confirmedChannelVisit.current = visitId;
    } catch (error) {
      if (isRuntimeContextCurrent(context) && activeChannelVisit.current === visitId) {
        Alert.alert(i18n.t('feed:channelViewedUpdateFailed'), error instanceof Error ? error.message : i18n.t('feed:retryLater'));
      }
    } finally {
      if (confirmingChannelVisit.current === visitId) confirmingChannelVisit.current = 0;
    }
  }, [readerClient, session, source]);
  const refreshFeed = useCallback(async () => {
    const visitId = channelVisitId;
    const context = source ? captureRuntimeContext(runtime) : null;
    const [result] = await Promise.all([refetchFeed(), onRefreshSources().catch(() => undefined)]);
    if (source && context && isRuntimeContextCurrent(context) && activeChannelVisit.current === visitId && visitId > 0) {
      await confirmChannelVisit(result, visitId, context);
    }
    return result;
  }, [channelVisitId, confirmChannelVisit, onRefreshSources, refetchFeed, runtime, source]);

  useEffect(() => {
    if (!active || !source || channelVisitId <= 0 || attemptedChannelVisit.current === channelVisitId) return;
    attemptedChannelVisit.current = channelVisitId;
    void refreshFeed();
  }, [active, channelVisitId, refreshFeed, source]);
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
        if (!feed.refreshing && !feed.loading) void refreshFeed();
      }}
      onScroll={active ? position.onScroll : undefined}
      onScrollBeginDrag={position.onScrollBeginDrag}
      onScrollEndDrag={position.onScrollEndDrag}
      onScrollToIndexFailed={position.onScrollToIndexFailed}
      onViewableItemsChanged={position.onViewableItemsChanged}
      ref={position.listRef}
      refreshing={feed.refreshing}
      renderItem={({ index, item }) => <FeedCardRow
        avatarAccessToken={session.access_token}
        item={item}
        onOpenArticle={onOpenArticle}
        onRetryXTranslation={feed.xTranslation.retry}
        onToggleSave={onToggleSave}
        testID={`feed_item_${memoryKey}_${index}`}
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
              <ErrorState message={feed.message || t('contentLoadFailed')} onRetry={refreshFeed} />
            </View>
          ) : null}
        </>
      }
      ListEmptyComponent={(
        <View style={listFeedbackStyles.state}>
          {feed.loading ? <LoadingBlock /> : feed.isError ? (
            <ErrorState message={feed.message || t('contentLoadFailed')} onRetry={refreshFeed} />
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
  channelFilterButton: { alignItems: 'center', justifyContent: 'center', minHeight: 44, position: 'relative', width: 44 },
  channelFilterBadge: { alignItems: 'center', backgroundColor: colors.textStrong, borderColor: colors.background, borderRadius: radii.pill, borderWidth: 2, justifyContent: 'center', maxWidth: 42, minHeight: 20, minWidth: 20, paddingHorizontal: 4, position: 'absolute', right: -3, top: -2 },
  channelFilterBadgeText: { color: colors.surface, fontSize: 10, fontWeight: '800' },
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
