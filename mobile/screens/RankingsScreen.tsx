import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Image,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { Feather, MaterialCommunityIcons } from '@expo/vector-icons';
import type { Session } from '../lib/readerAuth';
import { useQueryClient } from '@tanstack/react-query';
import Reanimated, {
  type ScrollHandlerProcessed,
  type SharedValue,
} from 'react-native-reanimated';

import { BottomSheetModal } from '../components/BottomSheetModal';
import { CategoryPager } from '../components/CategoryPager';
import { PageHeaderContent } from '../components/PageHeader';
import { RankingTitle } from '../components/RankingTitle';
import {
  prefetchRanking,
  rankingVariantKey,
  useRanking,
  type RankingOptions,
} from '../hooks/useRanking';
import { useCollapsingChrome, useChromeStyle } from '../hooks/useCollapsingChrome';
import {
  type RankingItem,
  type RankingKind,
  type RedditRankingSort,
} from '../lib/api';
import { formatCount } from '../domain/formatting';
import { redditCommunitiesFromSources } from '../domain/source';
import { useSources } from '../hooks/useSources';
import { useTranslationPreference } from '../hooks/useTranslationPreference';
import { useReaderRuntime } from '../lib/connection/react';
import { colors, radii } from '../ui/tokens';
import { PAGE_HEADER_HEIGHT, SCREEN_HORIZONTAL_PADDING, SCREEN_LIST_BOTTOM_PADDING } from '../ui/layout';
import { listFeedbackStyles } from '../components/FeedbackState';
import { useTranslation } from '../i18n';
import type { ListPositionRegistry } from '../domain/listMemory';
import { useListPositionMemory } from '../hooks/useListPositionMemory';
import { readerQueryKeys } from '../state/queryClient';
import { protectsQueryKeys, removeObsoleteQueries } from '../state/queryLifecycle';

const RANKING_CHROME_HEIGHT = PAGE_HEADER_HEIGHT + 46;
const RANKING_KINDS: RankingKind[] = ['hacker_news', 'reddit', 'github'];

export function RankingsScreen({
  onProfile,
  active,
  chromeProgress,
  onOpenItem,
  session,
}: {
  active: boolean;
  chromeProgress: SharedValue<number>;
  onOpenItem: (item: RankingItem) => void;
  onProfile?: () => void;
  session: Session;
}) {
  const [kind, setKind] = useState<RankingKind>('hacker_news');
  const [redditCommunity, setRedditCommunity] = useState<string | null>(null);
  const [redditSort, setRedditSort] = useState<RedditRankingSort>('hot');
  const [visibleCounts, setVisibleCounts] = useState<ReadonlyMap<string, number>>(new Map());
  const positionRegistry = useRef<ListPositionRegistry>(new Map()).current;
  if (!active) return null;
  const currentVariant = rankingVariantKey(kind, kind === 'reddit' && redditCommunity
    ? { subreddit: redditCommunity, sort: redditSort }
    : undefined);
  return <RankingsScreenContent
    active
    chromeProgress={chromeProgress}
    kind={kind}
    onChangeKind={setKind}
    onChangeRedditCommunity={setRedditCommunity}
    onChangeRedditSort={setRedditSort}
    onChangeVisibleCount={(count) => setVisibleCounts((current) => {
      if ((current.get(currentVariant) ?? 20) === count) return current;
      const next = new Map(current);
      next.delete(currentVariant);
      next.set(currentVariant, count);
      while (next.size > 8) next.delete(next.keys().next().value as string);
      return next;
    })}
    onOpenItem={onOpenItem}
    onProfile={onProfile}
    positionRegistry={positionRegistry}
    redditCommunity={redditCommunity}
    redditSort={redditSort}
    session={session}
    visibleCount={visibleCounts.get(currentVariant) ?? 20}
  />;
}

function RankingsScreenContent({
  onProfile,
  active,
  chromeProgress,
  kind,
  onChangeKind,
  onChangeRedditCommunity,
  onChangeRedditSort,
  onChangeVisibleCount,
  onOpenItem,
  positionRegistry,
  redditCommunity,
  redditSort,
  session,
  visibleCount,
}: {
  active: boolean;
  chromeProgress: SharedValue<number>;
  kind: RankingKind;
  onChangeKind: (kind: RankingKind) => void;
  onChangeRedditCommunity: (community: string | null) => void;
  onChangeRedditSort: (sort: RedditRankingSort) => void;
  onChangeVisibleCount: (count: number) => void;
  onOpenItem: (item: RankingItem) => void;
  onProfile?: () => void;
  positionRegistry: ListPositionRegistry;
  redditCommunity: string | null;
  redditSort: RedditRankingSort;
  session: Session;
  visibleCount: number;
}) {
  const { t } = useTranslation('feed');
  const [showCommunityPicker, setShowCommunityPicker] = useState(false);
  const [draftCommunity, setDraftCommunity] = useState<string | null>(null);
  const [draftSort, setDraftSort] = useState<RedditRankingSort>('hot');
  const readerClient = useQueryClient();
  const runtime = useReaderRuntime();
  const sourcesQuery = useSources(session, active);
  const translation = useTranslationPreference(session, active);
  const redditCommunities = useMemo(
    () => redditCommunitiesFromSources(sourcesQuery.items),
    [sourcesQuery.items],
  );
  const redditCommunitiesResolved = sourcesQuery.isSuccess;
  const redditOptions = useMemo<RankingOptions | undefined>(
    () => redditCommunity ? { subreddit: redditCommunity, sort: redditSort } : undefined,
    [redditCommunity, redditSort],
  );
  const hackerQuery = useRanking(
    session,
    'hacker_news',
    undefined,
    active && kind === 'hacker_news',
    visibleCount,
    translation.enabled,
    translation.targetLocale,
    translation.effectiveEngineId,
    translation.effectiveEngineFingerprint,
  );
  const redditQuery = useRanking(
    session,
    'reddit',
    redditOptions,
    active && kind === 'reddit',
    visibleCount,
    translation.enabled,
    translation.targetLocale,
    translation.effectiveEngineId,
    translation.effectiveEngineFingerprint,
  );
  const githubQuery = useRanking(
    session,
    'github',
    undefined,
    active && kind === 'github',
    visibleCount,
    translation.enabled,
    translation.targetLocale,
    translation.effectiveEngineId,
    translation.effectiveEngineFingerprint,
  );
  const rankingQueries = { hacker_news: hackerQuery, reddit: redditQuery, github: githubQuery };
  const rankingKeys = useMemo(() => RANKING_KINDS.map((targetKind) => readerQueryKeys.rankings(
    session.user.id,
    rankingVariantKey(targetKind, targetKind === 'reddit' ? redditOptions : undefined),
    translation.targetLocale,
    translation.enabled,
    translation.effectiveEngineFingerprint,
    runtime?.identity.server_id,
  )), [redditOptions, runtime?.identity.server_id, session.user.id, translation.effectiveEngineFingerprint, translation.enabled, translation.targetLocale]);

  useEffect(() => {
    void removeObsoleteQueries({
      isProtected: protectsQueryKeys(rankingKeys),
      prefix: readerQueryKeys.rankingsPrefix(session.user.id, runtime?.identity.server_id),
      queryClient: readerClient,
    });
    return () => {
      const currentKey = readerQueryKeys.rankings(
        session.user.id,
        rankingVariantKey(kind, kind === 'reddit' ? redditOptions : undefined),
        translation.targetLocale,
        translation.enabled,
        translation.effectiveEngineFingerprint,
        runtime?.identity.server_id,
      );
      queueMicrotask(() => void removeObsoleteQueries({
        isProtected: protectsQueryKeys([currentKey]),
        prefix: readerQueryKeys.rankingsPrefix(session.user.id, runtime?.identity.server_id),
        queryClient: readerClient,
      }));
    };
  }, [kind, rankingKeys, readerClient, redditOptions, runtime?.identity.server_id, session.user.id, translation.effectiveEngineFingerprint, translation.enabled, translation.targetLocale]);

  useEffect(() => {
    const next = redditCommunity && redditCommunities.includes(redditCommunity)
      ? redditCommunity
      : redditCommunities[0] ?? null;
    if (next !== redditCommunity) onChangeRedditCommunity(next);
  }, [onChangeRedditCommunity, redditCommunities, redditCommunity]);

  useEffect(() => {
    if (!active) return;
    const currentIndex = RANKING_KINDS.indexOf(kind);
    [RANKING_KINDS[currentIndex - 1], RANKING_KINDS[currentIndex + 1]]
      .filter(
        (targetKind): targetKind is RankingKind =>
          targetKind !== undefined &&
          (targetKind !== 'reddit' || (redditCommunitiesResolved && Boolean(redditCommunity))),
      )
      .forEach((targetKind) =>
        void prefetchRanking(
          readerClient,
          session,
          targetKind,
          targetKind === 'reddit' ? redditOptions : undefined,
          runtime?.identity.server_id,
          runtime,
          translation.targetLocale,
          translation.enabled,
          translation.effectiveEngineFingerprint,
        ),
      );
  }, [
    active,
    kind,
    readerClient,
    redditCommunitiesResolved,
    redditCommunity,
    redditOptions,
    runtime,
    session,
    translation.effectiveEngineFingerprint,
    translation.enabled,
    translation.targetLocale,
  ]);

  const { onScroll, revealChrome } = useCollapsingChrome(active, chromeProgress, kind, { height: RANKING_CHROME_HEIGHT });
  const headerStyle = useChromeStyle(chromeProgress, RANKING_CHROME_HEIGHT);

  function loadMoreIfNeeded(targetKind: RankingKind) {
    const count = rankingQueries[targetKind].ranking?.items.length ?? 0;
    if (targetKind === kind && visibleCount < count) {
      onChangeVisibleCount(Math.min(visibleCount + 20, count));
    }
  }

  function renderRankingPage(targetKind: RankingKind) {
    return <RankingPage
      active={active && targetKind === kind}
      kind={targetKind}
      onLoadMore={() => loadMoreIfNeeded(targetKind)}
      onOpenItem={onOpenItem}
      onOpenRedditFilters={() => { setDraftCommunity(redditCommunity); setDraftSort(redditSort); setShowCommunityPicker(true); }}
      onScroll={onScroll}
      positionRegistry={positionRegistry}
      query={rankingQueries[targetKind]}
      redditCommunity={redditCommunity}
      redditOptions={redditOptions}
      redditSort={redditSort}
      sourcesError={sourcesQuery.error ? sourcesQuery.message : ''}
      sourcesPending={sourcesQuery.isPending}
      visibleCount={targetKind === kind ? visibleCount : 20}
    />;
  }

  function refreshCurrentRanking() {
    if (kind === 'reddit' && !redditCommunity) return;
    void rankingQueries[kind].refresh().catch(() => undefined);
  }

  return (
    <View style={styles.motionPage}>
      <Reanimated.View style={[styles.rankingChromeLayer, headerStyle]}>
        <View style={styles.motionPageHeader}>
          <PageHeaderContent
            compact
            title={t('rankings')}
            session={session}
            onProfile={onProfile}
            headingRight={(
              <Pressable
                accessibilityLabel={t('refreshRankings')}
                accessibilityRole="button"
                accessibilityState={{ busy: rankingQueries[kind].isFetching }}
                disabled={rankingQueries[kind].isFetching || (kind === 'reddit' && !redditCommunity)}
                onPress={refreshCurrentRanking}
                style={{ width: 40, height: 40, alignItems: 'center', justifyContent: 'center' }}
              >
                <Image source={require('../assets/icons/refresh-cw.png')} style={{ width: 19, height: 19 }} />
              </Pressable>
            )}
          />
        </View>
        <View style={styles.rankingTabs}>
          <RankingTab active={kind === 'hacker_news'} label="Hacker News" onPress={() => { revealChrome(); onChangeKind('hacker_news'); }} />
          <RankingTab active={kind === 'reddit'} label="Reddit" onPress={() => { revealChrome(); onChangeKind('reddit'); }} />
          <RankingTab active={kind === 'github'} label="GitHub" onPress={() => { revealChrome(); onChangeKind('github'); }} />
        </View>
      </Reanimated.View>
      <CategoryPager
        onPageTransitionStart={revealChrome}
        onSelect={(nextKind) => { revealChrome(); onChangeKind(nextKind); }}
        options={RANKING_KINDS}
        pageStyle={styles.categoryPage}
        renderPage={renderRankingPage}
        selected={kind}
        style={styles.categoryPager}
      />
      <BottomSheetModal onClose={() => setShowCommunityPicker(false)} visible={showCommunityPicker}>
        <View style={styles.sheetHeading}><Text style={styles.modalTitle}>{t('adjustRankings')}</Text><Pressable accessibilityLabel={t('closeRankingFilters')} onPress={() => setShowCommunityPicker(false)} style={styles.closeButton}><Feather name="x" size={22} color={colors.textMuted} /></Pressable></View>
        <Text style={styles.modalHint}>{t('subscribedCommunities')}</Text>
        {redditCommunities.map((community) => <Pressable accessibilityRole="radio" accessibilityState={{ checked: draftCommunity === community }} key={community.toLowerCase()} onPress={() => setDraftCommunity(community)} style={styles.communityChoice}>
          <Text style={styles.communityChoiceText}>r / {community}</Text><View style={[styles.radio, draftCommunity === community && styles.radioSelected]} />
        </Pressable>)}
        <Text style={styles.modalHint}>{t('sortOrder')}</Text>
        {(['hot', 'top', 'rising'] as const).map((sort) => <Pressable accessibilityRole="radio" accessibilityState={{ checked: draftSort === sort }} key={sort} onPress={() => setDraftSort(sort)} style={styles.communityChoice}>
          <Text style={styles.communityChoiceText}>{t(sort === 'hot' ? 'sortHotChoice' : sort === 'top' ? 'sortTopChoice' : 'sortRisingChoice')}</Text><View style={[styles.radio, draftSort === sort && styles.radioSelected]} />
        </Pressable>)}
        <Pressable accessibilityRole="button" disabled={!draftCommunity || !redditCommunities.includes(draftCommunity)} onPress={() => { if (!draftCommunity || !redditCommunities.includes(draftCommunity)) return; onChangeRedditCommunity(draftCommunity); onChangeRedditSort(draftSort); setShowCommunityPicker(false); }} style={styles.applyButton}><Text style={styles.applyText}>{t('viewRankings')}</Text></Pressable>
      </BottomSheetModal>
    </View>
  );
}

const rankingItemId = (item: RankingItem) => item.translation_key;

function RankingPage({
  active,
  kind,
  onLoadMore,
  onOpenItem,
  onOpenRedditFilters,
  onScroll,
  positionRegistry,
  query,
  redditCommunity,
  redditOptions,
  redditSort,
  sourcesError,
  sourcesPending,
  visibleCount,
}: {
  active: boolean;
  kind: RankingKind;
  onLoadMore: () => void;
  onOpenItem: (item: RankingItem) => void;
  onOpenRedditFilters: () => void;
  onScroll: ScrollHandlerProcessed;
  positionRegistry: ListPositionRegistry;
  query: ReturnType<typeof useRanking>;
  redditCommunity: string | null;
  redditOptions: RankingOptions | undefined;
  redditSort: RedditRankingSort;
  sourcesError: string;
  sourcesPending: boolean;
  visibleCount: number;
}) {
  const { t } = useTranslation('feed');
  const items = query.ranking?.items ?? [];
  const shownItems = items.slice(0, visibleCount);
  const memoryKey = rankingVariantKey(kind, kind === 'reddit' ? redditOptions : undefined);
  const position = useListPositionMemory({
    active,
    itemId: rankingItemId,
    items: shownItems,
    memoryKey,
    onScroll,
    registry: positionRegistry,
  });
  const message = kind === 'reddit' && !redditCommunity
    ? t('redditSubscriptionRequired')
    : kind === 'reddit' && sourcesError
      ? sourcesError
      : query.message;
  const feedback = query.loading || (kind === 'reddit' && sourcesPending)
    ? <LoadingBlock />
    : message ? <EmptyState icon="chart-box-outline" message={message} /> : null;
  return <Reanimated.FlatList
    contentContainerStyle={[styles.rankingList, items.length === 0 && listFeedbackStyles.content]}
    data={shownItems}
    keyExtractor={(item) => `${kind}-${item.rank}-${item.url}`}
    onEndReached={onLoadMore}
    onEndReachedThreshold={0.5}
    onMomentumScrollEnd={position.onMomentumScrollEnd}
    onScroll={active ? position.onScroll : undefined}
    onScrollBeginDrag={position.onScrollBeginDrag}
    onScrollEndDrag={position.onScrollEndDrag}
    onScrollToIndexFailed={position.onScrollToIndexFailed}
    onViewableItemsChanged={position.onViewableItemsChanged}
    ref={position.listRef}
    renderItem={({ item }) => <RankingCard
      item={item}
      kind={kind}
      onOpen={() => onOpenItem({ ...item, ranking_context: { kind, ...(kind === 'reddit' ? { subreddit: redditCommunity ?? undefined, sort: redditSort, time_filter: 'week' as const } : {}) } })}
      onRetryTitle={() => { void query.retryTitleTranslation(item); }}
      titleRetrying={query.isTitleRetrying(item)}
      titleTimedOut={query.timedOutSegmentIds.has(`${item.translation_key}:title`)}
    />}
    scrollEventThrottle={16}
    showsVerticalScrollIndicator={false}
    ListHeaderComponent={<>
      {kind === 'reddit' && redditCommunity ? <Pressable accessibilityLabel={t('switchRedditChannel')} accessibilityRole="button" onPress={onOpenRedditFilters} style={styles.rankingIntro}>
        <Text numberOfLines={1} style={styles.rankingTitle}>r / {redditCommunity}</Text>
        <Text style={styles.rankingSubtitle}>· {t(redditSort === 'top' ? 'sortTop' : redditSort === 'rising' ? 'sortRising' : 'sortHot')}</Text>
        <Feather color={colors.textMuted} name="sliders" size={18} />
      </Pressable> : null}
      {items.length > 0 ? feedback : null}
    </>}
    ListEmptyComponent={<View style={listFeedbackStyles.state}>{feedback}</View>}
  />;
}

function RankingCard({
  item,
  kind,
  onOpen,
  onRetryTitle,
  titleRetrying,
  titleTimedOut,
}: {
  item: RankingItem;
  kind: RankingKind;
  onOpen: () => void;
  onRetryTitle: () => void;
  titleRetrying: boolean;
  titleTimedOut: boolean;
}) {
  if (kind === 'github') return <GithubRankingCard item={item} onPress={onOpen} onRetryTitle={onRetryTitle} titleRetrying={titleRetrying} titleTimedOut={titleTimedOut} />;
  if (kind === 'reddit') return <RedditRankingCard item={item} onPress={onOpen} onRetryTitle={onRetryTitle} titleRetrying={titleRetrying} titleTimedOut={titleTimedOut} />;
  return <HackerNewsRankingCard item={item} onPress={onOpen} onRetryTitle={onRetryTitle} titleRetrying={titleRetrying} titleTimedOut={titleTimedOut} />;
}

function rankingDomain(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return 'news.ycombinator.com';
  }
}

function HackerNewsRankingCard({ item, onPress, onRetryTitle, titleRetrying, titleTimedOut }: { item: RankingItem; onPress: () => void; onRetryTitle: () => void; titleRetrying: boolean; titleTimedOut: boolean }) {
  const { t } = useTranslation('feed');
  return (
    <Pressable accessibilityLabel={`${item.rank} ${item.translated_title?.trim() || item.title}`} accessibilityRole="button" onPress={onPress} style={styles.hnRow}>
      <Text style={styles.rankNumber}>{String(item.rank).padStart(2, '0')}</Text>
      <View style={styles.rankCopy}>
        <RankingTitle
          numberOfLines={2}
          onRetry={onRetryTitle}
          retrying={titleRetrying}
          status={item.title_translation_status}
          timedOut={titleTimedOut}
          title={item.title}
          titleStyle={styles.hnTitle}
          translatedTitle={item.translated_title}
        />
        <Text style={styles.rankSource}>{rankingDomain(item.url)}</Text>
        <View style={styles.rankMeta}>
          <View style={styles.metric}><Feather color="#83877E" name="triangle" size={12} /><Text style={styles.metricText}>{formatCount(item.score)}</Text></View>
          <Text style={styles.metricText}>{t('commentCount', { count: item.comments ?? undefined, value: formatCount(item.comments) })}</Text>
        </View>
      </View>
    </Pressable>
  );
}

function RedditRankingCard({ item, onPress, onRetryTitle, titleRetrying, titleTimedOut }: { item: RankingItem; onPress: () => void; onRetryTitle: () => void; titleRetrying: boolean; titleTimedOut: boolean }) {
  return (
    <Pressable accessibilityLabel={`${item.rank} ${item.translated_title?.trim() || item.title}`} accessibilityRole="button" onPress={onPress} style={[styles.redditRow, item.rank === 1 && { paddingTop: 8 }]}>
      <Text style={styles.rankNumber}>{String(item.rank).padStart(2, '0')}</Text>
      <View style={styles.rankCopy}>
        <RankingTitle
          numberOfLines={3}
          onRetry={onRetryTitle}
          retrying={titleRetrying}
          status={item.title_translation_status}
          timedOut={titleTimedOut}
          title={item.title}
          titleStyle={styles.redditTitle}
          translatedTitle={item.translated_title}
        />
        {item.description ? <Text numberOfLines={2} style={styles.redditDescription}>{item.translated_description?.trim() || item.description}</Text> : null}
        {item.author ? <Text style={styles.rankAuthor}>u/{item.author.replace(/^\/?u\//, '')}</Text> : null}
      </View>
      {Array.isArray(item.image_urls) && item.image_urls[0] ? <Image source={{ uri: item.image_urls[0] }} style={styles.redditThumb} /> : null}
    </Pressable>
  );
}

function GithubRankingCard({ item, onPress, onRetryTitle, titleRetrying, titleTimedOut }: { item: RankingItem; onPress: () => void; onRetryTitle: () => void; titleRetrying: boolean; titleTimedOut: boolean }) {
  useTranslation('feed');
  return (
    <Pressable accessibilityLabel={`${item.rank} ${item.translated_title?.trim() || item.title}`} accessibilityRole="button" onPress={onPress} style={styles.githubRow}>
      <Text style={styles.rankNumber}>{String(item.rank).padStart(2, '0')}</Text>
      <View style={styles.rankCopy}>
        <RankingTitle
          numberOfLines={1}
          onRetry={onRetryTitle}
          retrying={titleRetrying}
          status={item.title_translation_status}
          timedOut={titleTimedOut}
          title={item.title}
          titleStyle={styles.githubRepo}
          translatedTitle={item.translated_title}
        />
      {item.description ? <Text numberOfLines={3} style={styles.githubDescription}>{item.translated_description?.trim() || item.description}</Text> : null}
      <View style={styles.githubFooter}>
        <View style={styles.avatarStack}>
          {(Array.isArray(item.image_urls) ? item.image_urls : []).slice(0, 3).map((url, index) => (
            <Image key={url} source={{ uri: url }} style={[styles.contributorAvatar, { marginLeft: index ? -7 : 0 }]} />
          ))}
        </View>
        {item.language ? <Text numberOfLines={1} style={styles.githubMeta}>{item.language}</Text> : null}
        {item.stars != null ? <View style={styles.metric}><Feather name="star" size={12} color={colors.textMuted} /><Text style={styles.metricText}>{formatCount(item.stars)}</Text></View> : null}
        {item.forks != null ? <View style={styles.metric}><Feather name="git-branch" size={12} color={colors.textMuted} /><Text style={styles.metricText}>{formatCount(item.forks)}</Text></View> : null}
      </View>
      </View>
    </Pressable>
  );
}

function RankingTab({ active, label, onPress }: { active: boolean; label: string; onPress: () => void }) {
  return <Pressable accessibilityLabel={label} accessibilityRole="tab" accessibilityState={{ selected: active }} onPress={onPress} style={[styles.rankingTab, active && styles.rankingTabActive]}><Text style={[styles.rankingTabText, active && styles.rankingTabTextActive]}>{label}</Text></Pressable>;
}

function EmptyState({ icon, message }: { icon: keyof typeof MaterialCommunityIcons.glyphMap; message: string }) {
  return <View style={styles.empty}><View style={styles.emptyIcon}><MaterialCommunityIcons color="#64748b" name={icon} size={30} /></View><Text style={styles.emptyText}>{message}</Text></View>;
}

function LoadingBlock() {
  return <View style={styles.loading}><ActivityIndicator color="#111111" /></View>;
}

const styles = StyleSheet.create({
  motionPage: { backgroundColor: colors.background, flex: 1, overflow: 'hidden' },
  categoryPager: { flex: 1 },
  categoryPage: { flex: 1 },
  motionPageHeader: { paddingHorizontal: SCREEN_HORIZONTAL_PADDING },
  rankingChromeLayer: { backgroundColor: colors.background, height: RANKING_CHROME_HEIGHT, left: 0, position: 'absolute', right: 0, top: 0, zIndex: 4 },
  rankingTabs: { flexDirection: 'row', gap: 24, borderBottomWidth: 1, borderBottomColor: colors.border, marginHorizontal: SCREEN_HORIZONTAL_PADDING },
  rankingTab: { alignItems: 'center', borderBottomColor: 'transparent', borderBottomWidth: 3, height: 45, paddingTop: 10, paddingBottom: 11, justifyContent: 'center' },
  rankingTabActive: { borderBottomColor: colors.textStrong }, rankingTabText: { color: '#777B74', fontSize: 13, lineHeight: 20.8 }, rankingTabTextActive: { color: colors.textStrong, fontWeight: '700' },
  rankingList: { gap: 0, paddingBottom: SCREEN_LIST_BOTTOM_PADDING, paddingHorizontal: SCREEN_HORIZONTAL_PADDING, paddingTop: RANKING_CHROME_HEIGHT },
  rankingIntro: { alignItems: 'center', flexDirection: 'row', gap: 8, minHeight: 40, marginBottom: 0 },
  rankingTitle: { color: colors.textStrong, fontSize: 14, fontWeight: '600', flexShrink: 1 },
  rankingSubtitle: { color: colors.textMuted, fontSize: 12, flex: 1 },
  hnRow: { borderBottomColor: colors.border, borderBottomWidth: 1, flexDirection: 'row', gap: 13, paddingVertical: 20 },
  rankNumber: { color: colors.textStrong, fontSize: 18, lineHeight: 27.2, fontVariant: ['tabular-nums'], fontWeight: '500', width: 28 },

  rankCopy: { flex: 1, minWidth: 0 },
  hnTitle: { color: colors.textPrimary, fontSize: 17, fontWeight: '700', lineHeight: 27.2 },
  rankSource: { color: '#777B74', fontSize: 10, lineHeight: 16, marginTop: 7 },
  rankMeta: { alignItems: 'center', flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 9 },
  rankAuthor: { color: colors.textMuted, fontSize: 12, marginTop: 4 },
  redditRow: { borderBottomColor: colors.border, borderBottomWidth: 1, flexDirection: 'row', gap: 13, paddingVertical: 20 },
  redditTitle: { color: colors.textPrimary, fontSize: 17, fontWeight: '700', lineHeight: 27.2 },
  redditDescription: { color: colors.textTertiary, fontSize: 13, lineHeight: 18, marginTop: 6 },
  redditThumb: { backgroundColor: colors.imagePlaceholder, borderRadius: radii.sm, height: 86, marginTop: 3, width: 86 },
  githubRow: { borderBottomColor: colors.border, borderBottomWidth: 1, flexDirection: 'row', gap: 13, paddingVertical: 20 },
  githubRepo: { color: colors.textPrimary, fontSize: 17, fontWeight: '700', lineHeight: 27.2 },
  githubDescription: { color: colors.textTertiary, fontSize: 13, lineHeight: 19, marginTop: 8 },
  githubFooter: { alignItems: 'center', flexDirection: 'row', gap: 8, marginTop: 10 },
  avatarStack: { flexDirection: 'row', minWidth: 22 },
  contributorAvatar: { backgroundColor: colors.imagePlaceholder, borderColor: colors.surface, borderRadius: 11, borderWidth: 1, height: 22, width: 22 },
  metric: { alignItems: 'center', flexDirection: 'row', gap: 4 }, metricText: { color: '#83877E', fontSize: 10, lineHeight: 16 },
  githubMeta: { color: colors.textTertiary, flexShrink: 1, fontSize: 11 },
  sheetHeading: { alignItems: 'center', flexDirection: 'row', justifyContent: 'space-between' }, closeButton: { width: 44, height: 44, alignItems: 'center', justifyContent: 'center' },
  modalTitle: { color: colors.textStrong, fontSize: 21, fontWeight: '700' }, modalHint: { color: colors.textMuted, fontSize: 11, marginTop: 18, marginBottom: 6 },
  communityChoice: { alignItems: 'center', borderBottomColor: colors.border, borderBottomWidth: 1, flexDirection: 'row', gap: 12, minHeight: 48 }, communityChoiceText: { color: colors.textPrimary, flex: 1, fontSize: 14 },
  radio: { width: 18, height: 18, borderRadius: 9, borderWidth: 1, borderColor: colors.borderStrong }, radioSelected: { borderWidth: 5, borderColor: colors.textStrong },
  applyButton: { backgroundColor: colors.textStrong, alignItems: 'center', justifyContent: 'center', minHeight: 48, marginTop: 24, borderRadius: 11 }, applyText: { color: colors.surface, fontSize: 14, fontWeight: '600' },
  empty: { alignItems: 'center', flex: 1, justifyContent: 'center', paddingHorizontal: 32 },
  emptyIcon: { alignItems: 'center', backgroundColor: colors.surfaceSubtle, borderRadius: 30, height: 60, justifyContent: 'center', marginBottom: 14, width: 60 },
  emptyText: { color: colors.textTertiary, fontSize: 15, lineHeight: 23, textAlign: 'center' },
  loading: { alignItems: 'center', paddingVertical: 28 },
});
