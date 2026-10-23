import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AppState,
  ActivityIndicator,
  Image,
  Pressable,
  ScrollView,
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
import { ProductHuntDatePicker } from '../components/ProductHuntDatePicker';
import { CategoryPager } from '../components/CategoryPager';
import { PageHeaderContent } from '../components/PageHeader';
import { XRankingContent } from '../components/XRankingContent';
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
  type GithubRankingPeriod,
  type RedditRankingSort,
} from '../lib/api';
import { formatCount, relativeTime } from '../domain/formatting';
import { isProductHuntDate, productHuntToday, shiftProductHuntDate } from '../domain/productHuntDate';
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
const RANKING_KINDS: RankingKind[] = ['hacker_news', 'reddit', 'github', 'product_hunt', 'techtwitter'];
const EMPTY_RANKING_ITEMS: RankingItem[] = [];
const GITHUB_PERIODS: GithubRankingPeriod[] = ['daily', 'weekly', 'monthly'];
const PRODUCT_HUNT_RECENT_DATES = 8;

function recentProductHuntDates(current: string[], date: string): string[] {
  if (current[current.length - 1] === date) return current;
  return [...current.filter((day) => day !== date), date].slice(-PRODUCT_HUNT_RECENT_DATES);
}

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
  const [githubPeriod, setGithubPeriod] = useState<GithubRankingPeriod>('weekly');
  const [productHuntTodayDate, setProductHuntTodayDate] = useState(productHuntToday);
  // null follows the LA calendar day as it rolls over; an explicit date stays selected across tabs.
  const [selectedProductHuntDate, setSelectedProductHuntDate] = useState<string | null>(null);
  const productHuntDate = selectedProductHuntDate ?? productHuntTodayDate;
  const [recentDates, setRecentDates] = useState<string[]>(() => [productHuntTodayDate]);
  useEffect(() => {
    if (!active) return;
    const updateToday = () => setProductHuntTodayDate(productHuntToday());
    updateToday();
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'active') updateToday();
    });
    const timer = setInterval(() => {
      if (!AppState.currentState || AppState.currentState === 'active') updateToday();
    }, 30_000);
    return () => { subscription.remove(); clearInterval(timer); };
  }, [active]);
  useEffect(() => {
    setRecentDates((current) => recentProductHuntDates(current, productHuntDate));
  }, [productHuntDate]);
  const changeProductHuntDate = (date: string) => {
    if (!isProductHuntDate(date, productHuntTodayDate)) return;
    setSelectedProductHuntDate(date === productHuntTodayDate ? null : date);
    setRecentDates((current) => recentProductHuntDates(current, date));
  };
  const [visibleCounts, setVisibleCounts] = useState<ReadonlyMap<string, number>>(new Map());
  const positionRegistry = useRef<ListPositionRegistry>(new Map()).current;
  const currentVariant = rankingVariantKey(kind, kind === 'reddit' && redditCommunity
    ? { subreddit: redditCommunity, sort: redditSort }
    : kind === 'github' ? { period: githubPeriod } : kind === 'product_hunt' ? { date: productHuntDate } : undefined);
  return <RankingsScreenContent
    active={active}
    chromeProgress={chromeProgress}
    kind={kind}
    onChangeKind={setKind}
    onChangeRedditCommunity={setRedditCommunity}
    onChangeRedditSort={setRedditSort}
    onChangeGithubPeriod={setGithubPeriod}
    productHuntDate={productHuntDate}
    productHuntTodayDate={productHuntTodayDate}
    recentProductHuntDates={recentDates}
    onChangeProductHuntDate={changeProductHuntDate}
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
    githubPeriod={githubPeriod}
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
  onChangeGithubPeriod,
  productHuntDate,
  productHuntTodayDate,
  recentProductHuntDates,
  onChangeProductHuntDate,
  onChangeVisibleCount,
  onOpenItem,
  positionRegistry,
  githubPeriod,
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
  productHuntDate: string;
  productHuntTodayDate: string;
  recentProductHuntDates: readonly string[];
  onChangeProductHuntDate: (date: string) => void;
  onChangeGithubPeriod: (period: GithubRankingPeriod) => void;
  onChangeVisibleCount: (count: number) => void;
  onOpenItem: (item: RankingItem) => void;
  onProfile?: () => void;
  positionRegistry: ListPositionRegistry;
  githubPeriod: GithubRankingPeriod;
  redditCommunity: string | null;
  redditSort: RedditRankingSort;
  session: Session;
  visibleCount: number;
}) {
  const { t } = useTranslation('feed');
  const [showCommunityPicker, setShowCommunityPicker] = useState(false);
  const [draftCommunity, setDraftCommunity] = useState<string | null>(null);
  const [draftSort, setDraftSort] = useState<RedditRankingSort>('hot');
  const [showDatePicker, setShowDatePicker] = useState(false);
  const [showGithubPeriods, setShowGithubPeriods] = useState(false);
  const [showProductHuntInfo, setShowProductHuntInfo] = useState(false);
  const readerClient = useQueryClient();
  const [visibleXKeys, setVisibleXKeys] = useState<ReadonlySet<string>>(new Set());
  useEffect(() => {
    if (kind !== 'techtwitter') setVisibleXKeys((current) => current.size ? new Set() : current);
  }, [kind]);
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
  const githubOptions = useMemo<RankingOptions>(() => ({ period: githubPeriod }), [githubPeriod]);
  const productHuntOptions = useMemo<RankingOptions>(() => ({ date: productHuntDate }), [productHuntDate]);
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
    githubOptions,
    active && kind === 'github',
    visibleCount,
    translation.enabled,
    translation.targetLocale,
    translation.effectiveEngineId,
    translation.effectiveEngineFingerprint,
  );
  const productHuntQuery = useRanking(
    session,
    'product_hunt',
    productHuntOptions,
    active && kind === 'product_hunt',
    visibleCount,
    translation.enabled,
    translation.targetLocale,
    translation.effectiveEngineId,
    translation.effectiveEngineFingerprint,
  );
  const xQuery = useRanking(
    session,
    'techtwitter',
    undefined,
    active && kind === 'techtwitter',
    visibleCount,
    translation.enabled,
    translation.targetLocale,
    translation.effectiveEngineId,
    translation.effectiveEngineFingerprint,
    visibleXKeys,
  );
  const rankingQueries = { hacker_news: hackerQuery, reddit: redditQuery, github: githubQuery, product_hunt: productHuntQuery, techtwitter: xQuery };
  const rankingKeys = useMemo(() => [
    ...RANKING_KINDS.filter((targetKind) => targetKind !== 'github' && targetKind !== 'product_hunt').map((targetKind) => rankingVariantKey(targetKind, targetKind === 'reddit' ? redditOptions : undefined)),
    ...GITHUB_PERIODS.map((period) => rankingVariantKey('github', { period })),
    ...recentProductHuntDates.map((date) => rankingVariantKey('product_hunt', { date })),
    rankingVariantKey('product_hunt', productHuntOptions),
  ].map((variant) => readerQueryKeys.rankings(
    session.user.id, variant, translation.targetLocale, translation.enabled,
    translation.effectiveEngineFingerprint, runtime?.identity.server_id,
  )), [productHuntOptions, recentProductHuntDates, redditOptions, runtime?.identity.server_id, session.user.id, translation.effectiveEngineFingerprint, translation.enabled, translation.targetLocale]);

  useEffect(() => {
    void removeObsoleteQueries({
      isProtected: protectsQueryKeys(rankingKeys),
      prefix: readerQueryKeys.rankingsPrefix(session.user.id, runtime?.identity.server_id),
      queryClient: readerClient,
    });
    return () => {
      queueMicrotask(() => void removeObsoleteQueries({
        isProtected: protectsQueryKeys(rankingKeys),
        prefix: readerQueryKeys.rankingsPrefix(session.user.id, runtime?.identity.server_id),
        queryClient: readerClient,
      }));
    };
  }, [rankingKeys, readerClient, runtime?.identity.server_id, session.user.id]);

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
          targetKind === 'reddit' ? redditOptions : targetKind === 'github' ? githubOptions : targetKind === 'product_hunt' ? productHuntOptions : undefined,
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
    githubOptions,
    productHuntOptions,
    redditCommunity,
    redditOptions,
    runtime,
    session,
    translation.effectiveEngineFingerprint,
    translation.enabled,
    translation.targetLocale,
  ]);

  const { onScroll, revealChrome } = useCollapsingChrome(active, chromeProgress, kind === 'github' ? `github:${githubPeriod}` : kind === 'product_hunt' ? rankingVariantKey(kind, productHuntOptions) : kind, { height: RANKING_CHROME_HEIGHT });
  const headerStyle = useChromeStyle(chromeProgress, RANKING_CHROME_HEIGHT);

  function loadMoreIfNeeded(targetKind: RankingKind) {
    const count = rankingQueries[targetKind].ranking?.items.length ?? 0;
    if (targetKind === kind && visibleCount < count) {
      onChangeVisibleCount(Math.min(visibleCount + 20, count));
    }
  }

  function renderRankingPage(targetKind: RankingKind) {
    return <RankingPage
      key={targetKind === 'github' ? `github:${githubPeriod}` : targetKind === 'product_hunt' ? rankingVariantKey(targetKind, productHuntOptions) : targetKind}
      active={active && targetKind === kind}
      kind={targetKind}
      githubPeriod={githubPeriod}
      onOpenGithubPeriods={() => setShowGithubPeriods(true)}
      productHuntDate={productHuntDate}
      productHuntTodayDate={productHuntTodayDate}
      productHuntOptions={productHuntOptions}
      onChangeProductHuntDate={onChangeProductHuntDate}
      onOpenDatePicker={() => setShowDatePicker(true)}
      onOpenProductHuntInfo={() => setShowProductHuntInfo(true)}
      onLoadMore={() => loadMoreIfNeeded(targetKind)}
      onVisibleItems={(items) => {
        if (!active || targetKind !== 'techtwitter' || kind !== 'techtwitter') return;
        const keys = new Set(items.map((item) => item.translation_key));
        setVisibleXKeys((current) => current.size === keys.size && [...keys].every((key) => current.has(key)) ? current : keys);
      }}
      onOpenItem={onOpenItem}
      onOpenRedditFilters={() => { setDraftCommunity(redditCommunity); setDraftSort(redditSort); setShowCommunityPicker(true); }}
      onScroll={onScroll}
      positionRegistry={positionRegistry}
      query={rankingQueries[targetKind]}
      githubOptions={githubOptions}
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
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.rankingTabs}>
          <RankingTab active={kind === 'hacker_news'} label="Hacker News" onPress={() => { revealChrome(); onChangeKind('hacker_news'); }} />
          <RankingTab active={kind === 'reddit'} label="Reddit" onPress={() => { revealChrome(); onChangeKind('reddit'); }} />
          <RankingTab active={kind === 'github'} label="GitHub" onPress={() => { revealChrome(); onChangeKind('github'); }} />
          <RankingTab active={kind === 'product_hunt'} label="Product Hunt" onPress={() => { revealChrome(); onChangeKind('product_hunt'); }} />
          <RankingTab active={kind === 'techtwitter'} label={t('xTrendingTab')} onPress={() => { revealChrome(); onChangeKind('techtwitter'); }} />
        </ScrollView>
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
      <BottomSheetModal onClose={() => setShowGithubPeriods(false)} visible={showGithubPeriods}>
        <View style={styles.filterSheet}>
          <View style={styles.sheetHeading}>
            <Text style={styles.modalTitle}>{t('githubChoosePeriod')}</Text>
            <Pressable accessibilityLabel={t('closeRankingFilters')} accessibilityRole="button" onPress={() => setShowGithubPeriods(false)} style={styles.closeButton}><Feather name="x" size={22} color={colors.textMuted} /></Pressable>
          </View>
          {GITHUB_PERIODS.map((period) => <Pressable
            accessibilityLabel={t(period === 'daily' ? 'githubDaily' : period === 'weekly' ? 'githubWeekly' : 'githubMonthly')}
            accessibilityRole="radio"
            accessibilityState={{ checked: githubPeriod === period }}
            key={period}
            onPress={() => { onChangeGithubPeriod(period); setShowGithubPeriods(false); }}
            style={styles.communityChoice}
          >
            <Text style={styles.communityChoiceText}>{t(period === 'daily' ? 'githubDaily' : period === 'weekly' ? 'githubWeekly' : 'githubMonthly')}</Text>
            <View style={[styles.radio, githubPeriod === period && styles.radioSelected]} />
          </Pressable>)}
        </View>
      </BottomSheetModal>
      <ProductHuntDatePicker
        onClose={() => setShowDatePicker(false)}
        onSelectDate={onChangeProductHuntDate}
        selectedDate={productHuntDate}
        today={productHuntTodayDate}
        visible={showDatePicker}
      />
      <BottomSheetModal onClose={() => setShowProductHuntInfo(false)} visible={showProductHuntInfo}>
        <View style={styles.filterSheet}>
          <View style={styles.sheetHeading}>
            <Text style={styles.modalTitle}>{t('productHuntInfo')}</Text>
            <Pressable accessibilityLabel={t('productHuntCloseInfo')} accessibilityRole="button" onPress={() => setShowProductHuntInfo(false)} style={styles.closeButton}><Feather name="x" size={22} color={colors.textMuted} /></Pressable>
          </View>
          <Text style={styles.productHuntInfoText}>{t('productHuntFetchCaveat')}</Text>
        </View>
      </BottomSheetModal>
    </View>
  );
}

const rankingItemId = (item: RankingItem) => item.translation_key;

function RankingPage({
  active,
  githubPeriod,
  githubOptions,
  productHuntDate,
  productHuntTodayDate,
  productHuntOptions,
  onChangeProductHuntDate,
  onOpenDatePicker,
  onOpenGithubPeriods,
  onOpenProductHuntInfo,
  kind,
  onLoadMore,
  onVisibleItems,
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
  githubPeriod: GithubRankingPeriod;
  githubOptions: RankingOptions;
  onOpenGithubPeriods: () => void;
  productHuntDate: string;
  productHuntTodayDate: string;
  productHuntOptions: RankingOptions;
  onChangeProductHuntDate: (date: string) => void;
  onOpenDatePicker: () => void;
  onOpenProductHuntInfo: () => void;
  kind: RankingKind;
  onLoadMore: () => void;
  onVisibleItems: (items: readonly RankingItem[]) => void;
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
  const { t, i18n } = useTranslation('feed');
  const items = query.ranking?.items ?? EMPTY_RANKING_ITEMS;
  const shownItems = useMemo(() => items.slice(0, visibleCount), [items, visibleCount]);
  const memoryKey = rankingVariantKey(kind, kind === 'reddit' ? redditOptions : kind === 'github' ? githubOptions : kind === 'product_hunt' ? productHuntOptions : undefined);
  const yesterday = shiftProductHuntDate(productHuntTodayDate, -1);
  const canGoPreviousDay = productHuntDate > '0001-01-01';
  const nextDate = shiftProductHuntDate(productHuntDate, 1);
  const displayDate = useMemo(() => {
    const date = new Date(`${productHuntDate}T12:00:00Z`);
    return new Intl.DateTimeFormat(i18n.language, {
      month: 'short', day: 'numeric',
      ...(productHuntDate.slice(0, 4) !== productHuntTodayDate.slice(0, 4) ? { year: 'numeric' as const } : {}),
      timeZone: 'UTC',
    }).format(date);
  }, [i18n.language, productHuntDate, productHuntTodayDate]);
  const fetchedAt = query.ranking?.fetched_at;
  const fetchedTime = useMemo(() => fetchedAt
    ? new Intl.DateTimeFormat(i18n.language, {
      year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
      timeZone: 'America/Los_Angeles',
    }).format(new Date(fetchedAt))
    : null, [fetchedAt, i18n.language]);
  const position = useListPositionMemory({
    active,
    itemId: rankingItemId,
    items: shownItems,
    memoryKey,
    onScroll,
    onViewableItems: onVisibleItems,
    registry: positionRegistry,
  });
  const {
    failedSegmentIds, isTitleRetrying, retryBodyTranslation, retryTitleTranslation,
    timedOutSegmentIds, translating, translationEnabled,
  } = query;
  const renderItem = useCallback(({ item }: { item: RankingItem }) => {
    const translationRetryNeeded = kind === 'techtwitter' && Boolean(item.body_segments?.some(
      (segment) => timedOutSegmentIds.has(segment.segment_id) || failedSegmentIds.has(segment.segment_id),
    ));
    const retryVisible = kind === 'techtwitter' && translationEnabled && Boolean(item.body_text)
      && (translationRetryNeeded || Boolean(item.body_segments?.some((segment) => segment.translation_status === 'failed')));
    return <RankingCard
      item={item}
      kind={kind}
      githubPeriod={githubPeriod}
      onOpenItem={onOpenItem}
      onRetryBody={retryBodyTranslation}
      onRetryTitle={retryTitleTranslation}
      redditCommunity={redditCommunity}
      redditSort={redditSort}
      translationEnabled={kind === 'techtwitter' && translationEnabled}
      translationBusy={retryVisible && translating}
      translationRetryNeeded={translationRetryNeeded}
      titleRetrying={isTitleRetrying(item)}
      titleTimedOut={timedOutSegmentIds.has(`${item.translation_key}:title`)}
    />;
  }, [failedSegmentIds, githubPeriod, isTitleRetrying, kind, onOpenItem, redditCommunity, redditSort,
    retryBodyTranslation, retryTitleTranslation, timedOutSegmentIds, translating, translationEnabled]);
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
    renderItem={renderItem}
    scrollEventThrottle={16}
    showsVerticalScrollIndicator={false}
    ListHeaderComponent={<>
      {kind === 'github' ? <View style={styles.filterToolbar}>
        <Text numberOfLines={1} style={styles.filterTitle}>{t('githubTrendingRepositories')}</Text>
        <Pressable
          accessibilityLabel={t('githubChoosePeriod')}
          accessibilityRole="button"
          onPress={onOpenGithubPeriods}
          style={styles.periodMenuButton}
        >
          <Text style={styles.periodMenuText}>{t(githubPeriod === 'daily' ? 'githubDaily' : githubPeriod === 'weekly' ? 'githubWeekly' : 'githubMonthly')}</Text>
          <Feather color={colors.textMuted} name="chevron-down" size={16} />
        </Pressable>
      </View> : null}
      {kind === 'product_hunt' ? <>
        <View style={styles.filterToolbar}>
          <Pressable accessibilityLabel={t('productHuntChooseDate')} accessibilityRole="button" onPress={onOpenDatePicker} style={styles.dateMenuButton}>
            <Text numberOfLines={1} style={styles.dateMenuText}>
              {productHuntDate === productHuntTodayDate ? `${t('productHuntToday')} · ` : productHuntDate === yesterday ? `${t('productHuntYesterday')} · ` : ''}{displayDate}
            </Text>
            <Feather color={colors.textMuted} name="chevron-down" size={16} />
          </Pressable>
          <View style={styles.dateNavigation}>
            <Pressable accessibilityLabel={t('productHuntPreviousDay')} accessibilityRole="button" accessibilityState={{ disabled: !canGoPreviousDay }} disabled={!canGoPreviousDay} onPress={() => onChangeProductHuntDate(shiftProductHuntDate(productHuntDate, -1))} style={styles.dateArrow}><Feather color={canGoPreviousDay ? colors.textStrong : colors.textMuted} name="chevron-left" size={20} /></Pressable>
            <Pressable accessibilityLabel={t('productHuntToday')} accessibilityRole="button" onPress={() => onChangeProductHuntDate(productHuntTodayDate)} style={styles.todayButton}><Text style={styles.todayButtonText}>{t('productHuntToday')}</Text></Pressable>
            <Pressable accessibilityLabel={t('productHuntNextDay')} accessibilityRole="button" accessibilityState={{ disabled: nextDate > productHuntTodayDate }} disabled={nextDate > productHuntTodayDate} onPress={() => onChangeProductHuntDate(nextDate)} style={styles.dateArrow}><Feather color={nextDate > productHuntTodayDate ? colors.textMuted : colors.textStrong} name="chevron-right" size={20} /></Pressable>
          </View>
        </View>
        <View style={styles.productHuntMetadata}>
          <Text style={styles.productHuntMeta}>{t('productHuntTimeZone')}{fetchedTime ? ` · ${t('productHuntFetchedAt', { time: fetchedTime })}` : ''}</Text>
          <Pressable accessibilityLabel={t('productHuntInfo')} accessibilityRole="button" onPress={onOpenProductHuntInfo} style={styles.infoButton}><Feather color={colors.textMuted} name="info" size={17} /></Pressable>
        </View>
      </> : null}
      {kind === 'reddit' && redditCommunity ? <Pressable accessibilityLabel={t('switchRedditChannel')} accessibilityRole="button" onPress={onOpenRedditFilters} style={styles.rankingIntro}>
        <Text numberOfLines={1} style={styles.rankingTitle}>r / {redditCommunity}</Text>
        <Text style={styles.rankingSubtitle}>· {t(redditSort === 'top' ? 'sortTop' : redditSort === 'rising' ? 'sortRising' : 'sortHot')}</Text>
        <Feather color={colors.textMuted} name="sliders" size={18} />
      </Pressable> : null}
      {kind === 'techtwitter' && query.ranking ? <Text style={styles.xIntro}>{t('xTopUpdated', { time: relativeTime(query.ranking.fetched_at) })}</Text> : null}
      {items.length > 0 ? feedback : null}
    </>}
    ListEmptyComponent={<View style={listFeedbackStyles.state}>{feedback}</View>}
  />;
}

const RankingCard = memo(function RankingCard({
  item,
  githubPeriod,
  kind,
  onOpenItem,
  onRetryTitle,
  onRetryBody,
  redditCommunity,
  redditSort,
  translationEnabled,
  translationBusy,
  translationRetryNeeded,
  titleRetrying,
  titleTimedOut,
}: {
  item: RankingItem;
  kind: RankingKind;
  githubPeriod: GithubRankingPeriod;
  onOpenItem: (item: RankingItem) => void;
  onRetryTitle: (item: RankingItem) => void | Promise<void>;
  onRetryBody: (item: RankingItem) => void;
  redditCommunity: string | null;
  redditSort: RedditRankingSort;
  translationEnabled: boolean;
  translationBusy: boolean;
  translationRetryNeeded: boolean;
  titleRetrying: boolean;
  titleTimedOut: boolean;
}) {
  const onOpen = useCallback(() => onOpenItem({
    ...item,
    ranking_kind: kind,
    ranking_context: kind === 'product_hunt' ? undefined : {
      kind,
      ...(kind === 'reddit' ? { subreddit: redditCommunity ?? undefined, sort: redditSort, time_filter: 'week' as const } : {}),
      ...(kind === 'github' ? { period: githubPeriod } : {}),
    },
  }), [githubPeriod, item, kind, onOpenItem, redditCommunity, redditSort]);
  const retryTitle = useCallback(() => { void onRetryTitle(item); }, [item, onRetryTitle]);
  const retryBody = useCallback(() => onRetryBody(item), [item, onRetryBody]);
  if (kind === 'techtwitter') return <Pressable accessibilityRole="button" onPress={onOpen} style={styles.xRow}><Text style={styles.rankNumber}>{String(item.rank).padStart(2, '0')}</Text><XRankingContent item={item} translated={translationEnabled} onRetryTranslation={retryBody} translationBusy={translationBusy} translationRetryNeeded={translationRetryNeeded} /></Pressable>;
  if (kind === 'product_hunt') return <ProductHuntRankingCard item={item} onPress={onOpen} onRetryTitle={retryTitle} titleRetrying={titleRetrying} titleTimedOut={titleTimedOut} />;
  if (kind === 'github') return <GithubRankingCard item={item} onPress={onOpen} onRetryTitle={retryTitle} titleRetrying={titleRetrying} titleTimedOut={titleTimedOut} />;
  if (kind === 'reddit') return <RedditRankingCard item={item} onPress={onOpen} onRetryTitle={retryTitle} titleRetrying={titleRetrying} titleTimedOut={titleTimedOut} />;
  return <HackerNewsRankingCard item={item} onPress={onOpen} onRetryTitle={retryTitle} titleRetrying={titleRetrying} titleTimedOut={titleTimedOut} />;
});

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

function ProductHuntRankingCard({ item, onPress, onRetryTitle, titleRetrying, titleTimedOut }: { item: RankingItem; onPress: () => void; onRetryTitle: () => void; titleRetrying: boolean; titleTimedOut: boolean }) {
  const { t } = useTranslation('feed');
  return (
    <Pressable accessibilityLabel={`${item.rank} ${item.translated_title?.trim() || item.title} · Product Hunt`} accessibilityRole="button" onPress={onPress} style={styles.productHuntRow}>
      <Text style={styles.rankNumber}>{String(item.rank).padStart(2, '0')}</Text>
      {item.image_urls?.[0] ? <Image source={{ uri: item.image_urls[0] }} style={styles.productHuntThumb} /> : null}
      <View style={styles.rankCopy}>
        <RankingTitle
          numberOfLines={2}
          onRetry={onRetryTitle}
          retrying={titleRetrying}
          status={item.title_translation_status}
          timedOut={titleTimedOut}
          title={item.title}
          titleStyle={styles.productHuntTitle}
          translatedTitle={item.translated_title}
        />
        {item.description ? <Text numberOfLines={3} style={styles.productHuntDescription}>{item.translated_description?.trim() || item.description}</Text> : null}
        <Text style={styles.rankSource}>{t('productHuntOriginal')}</Text>
        <View style={styles.rankMeta}>
          {item.score != null ? <View style={styles.metric}><Feather color={colors.textMuted} name="arrow-up" size={12} /><Text style={styles.metricText}>{t('productHuntVotes', { value: formatCount(item.score) })}</Text></View> : null}
          {item.comments != null ? <Text style={styles.metricText}>{t('commentCount', { count: item.comments, value: formatCount(item.comments) })}</Text> : null}
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
  rankingTabs: { flexDirection: 'row', gap: 18, borderBottomWidth: 1, borderBottomColor: colors.border, paddingHorizontal: SCREEN_HORIZONTAL_PADDING },
  rankingTab: { alignItems: 'center', borderBottomColor: 'transparent', borderBottomWidth: 3, height: 45, paddingTop: 10, paddingBottom: 11, justifyContent: 'center' },
  rankingTabActive: { borderBottomColor: colors.textStrong }, rankingTabText: { color: '#777B74', fontSize: 13, lineHeight: 20.8 }, rankingTabTextActive: { color: colors.textStrong, fontWeight: '700' },
  rankingList: { gap: 0, paddingBottom: SCREEN_LIST_BOTTOM_PADDING, paddingHorizontal: SCREEN_HORIZONTAL_PADDING, paddingTop: RANKING_CHROME_HEIGHT },
  rankingIntro: { alignItems: 'center', flexDirection: 'row', gap: 8, minHeight: 40, marginBottom: 0 },
  filterToolbar: { alignItems: 'center', flexDirection: 'row', justifyContent: 'space-between', minHeight: 52, gap: 4 },
  filterTitle: { color: colors.textStrong, flexShrink: 1, fontSize: 14, fontWeight: '600' },
  periodMenuButton: { alignItems: 'center', flexDirection: 'row', gap: 4, justifyContent: 'flex-end', minHeight: 44, paddingLeft: 8 },
  periodMenuText: { color: colors.textStrong, fontSize: 13, fontWeight: '600' },
  dateMenuButton: { alignItems: 'center', flex: 1, flexDirection: 'row', gap: 3, minHeight: 44, minWidth: 0 },
  dateMenuText: { color: colors.textStrong, flexShrink: 1, fontSize: 14, fontWeight: '600' },
  dateNavigation: { alignItems: 'center', flexDirection: 'row' },
  dateArrow: { alignItems: 'center', justifyContent: 'center', minHeight: 44, minWidth: 44 },
  todayButton: { alignItems: 'center', justifyContent: 'center', minHeight: 44, minWidth: 44 },
  todayButtonText: { color: colors.textStrong, fontSize: 12, fontWeight: '600' },
  productHuntMetadata: { alignItems: 'center', flexDirection: 'row', minHeight: 32, marginBottom: 5 },
  productHuntMeta: { color: colors.textMuted, flex: 1, fontSize: 11, lineHeight: 16 },
  infoButton: { alignItems: 'center', justifyContent: 'center', minHeight: 44, minWidth: 44 },
  filterSheet: { alignSelf: 'center', maxWidth: 420, width: '100%' },
  productHuntInfoText: { color: colors.textMuted, fontSize: 14, lineHeight: 21, marginTop: 14 },
  rankingTitle: { color: colors.textStrong, fontSize: 14, fontWeight: '600', flexShrink: 1 },
  rankingSubtitle: { color: colors.textMuted, fontSize: 12, flex: 1 },
  xIntro: { color: colors.textMuted, fontSize: 12, paddingVertical: 12 },
  xRow: { borderBottomColor: colors.border, borderBottomWidth: 1, flexDirection: 'row', gap: 13, paddingVertical: 20 },
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
  productHuntRow: { borderBottomColor: colors.border, borderBottomWidth: 1, flexDirection: 'row', gap: 12, paddingVertical: 20 },
  productHuntThumb: { backgroundColor: colors.imagePlaceholder, borderRadius: radii.sm, height: 52, width: 52 },
  productHuntTitle: { color: colors.textPrimary, fontSize: 17, fontWeight: '700', lineHeight: 25 },
  productHuntDescription: { color: colors.textTertiary, fontSize: 13, lineHeight: 19, marginTop: 6 },
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
