import { memo, useCallback, useEffect, useReducer, useState } from 'react';
import { BackHandler, StyleSheet, View } from 'react-native';
import type { Session, ReaderAuthClient } from '../lib/readerAuth';
import { useSharedValue } from 'react-native-reanimated';

import type { FeedItem, RankingItem, SourceListItem } from '../lib/api';
import { createMainNavigationState, rankingFromSaved, reduceMainNavigation } from '../domain/mainNavigation';
import { buildReaderHtml } from '../domain/readerHtml';
import { colors } from '../ui/tokens';
import { ArticleReaderScreen } from '../screens/ArticleReaderScreen';
import { HomeScreen } from '../screens/HomeScreen';
import { ProfileScreen } from '../screens/ProfileScreen';
import { RankingPreviewScreen } from '../screens/RankingPreviewScreen';
import { RankingsScreen } from '../screens/RankingsScreen';
import { SavedScreen } from '../screens/SavedScreen';
import { SourcesScreen } from '../screens/SourcesScreen';
import { MotionReaderTabBar, type ReaderTab } from './ReaderTabBar';

// Keep mounted tabs' local state, queries and subscriptions while letting unchanged tabs
// skip navigation-only renders. Context and each screen's own state still update normally.
const MemoHomeScreen = memo(HomeScreen);
const MemoRankingsScreen = memo(RankingsScreen);
const MemoSourcesScreen = memo(SourcesScreen);
const MemoSavedScreen = memo(SavedScreen);
const MemoProfileScreen = memo(ProfileScreen);

export function MainAppShell({
  onChangeServer,
  onLogout,
  session,
  authClient,
}: {
  onChangeServer: () => void;
  onLogout: () => Promise<void>;
  session: Session;
  authClient: ReaderAuthClient;
}) {
  const [navigation, dispatchNavigation] = useReducer(
    reduceMainNavigation,
    undefined,
    createMainNavigationState,
  );
  const [channelVisitId, setChannelVisitId] = useState(0);
  const {
    selectedInboxSourceId,
    selectedItem,
    selectedRankingItem,
    sourceFeedReturnTab,
    tab,
    visitedTabs,
  } = navigation;
  const inboxChromeProgress = useSharedValue(0);
  const rankingsChromeProgress = useSharedValue(0);
  const sourcesChromeProgress = useSharedValue(0);
  const savedChromeProgress = useSharedValue(0);
  const profileChromeProgress = useSharedValue(0);

  const resetMotionChrome = useCallback(() => {
    inboxChromeProgress.value = 0;
    rankingsChromeProgress.value = 0;
    sourcesChromeProgress.value = 0;
    savedChromeProgress.value = 0;
    profileChromeProgress.value = 0;
  }, [inboxChromeProgress, rankingsChromeProgress, sourcesChromeProgress, savedChromeProgress, profileChromeProgress]);

  const selectTab = useCallback((nextTab: ReaderTab) => {
    resetMotionChrome();
    if (nextTab === 'inbox' && tab !== 'inbox' && selectedInboxSourceId !== null) {
      setChannelVisitId((current) => current + 1);
    }
    dispatchNavigation({ tab: nextTab, type: 'select_tab' });
  }, [resetMotionChrome, selectedInboxSourceId, tab]);

  const openProfile = useCallback(() => {
    resetMotionChrome();
    dispatchNavigation({ tab: 'settings', type: 'select_tab' });
  }, [resetMotionChrome]);

  const openArticle = useCallback((item: FeedItem) => {
    const ranking = rankingFromSaved(item);
    if (ranking) {
      dispatchNavigation({ type: 'open_ranking_item', item: ranking });
      return;
    }
    dispatchNavigation({ item, type: 'open_article' });
  }, []);

  const openRankingItem = useCallback((item: RankingItem) => {
    dispatchNavigation({ item, type: 'open_ranking_item' });
  }, []);

  const openSource = useCallback((source: SourceListItem) => {
    resetMotionChrome();
    setChannelVisitId((current) => current + 1);
    dispatchNavigation({ returnTab: 'sources', sourceId: source.source_id, type: 'open_source' });
  }, [resetMotionChrome]);

  const closeSourceFeed = useCallback(() => {
    if (sourceFeedReturnTab !== 'inbox') resetMotionChrome();
    dispatchNavigation({ type: 'close_source' });
  }, [resetMotionChrome, sourceFeedReturnTab]);

  const selectSourceId = useCallback((sourceId: string | null) => {
    if (sourceId !== null) setChannelVisitId((current) => current + 1);
    dispatchNavigation(sourceId === null ? { type: 'clear_source_filter' } : { sourceId, type: 'set_source_id' });
  }, []);

  const closeArticle = useCallback(() => dispatchNavigation({ type: 'close_article' }), []);
  const closeRankingItem = useCallback(() => dispatchNavigation({ type: 'close_ranking_item' }), []);

  const activeChromeProgress = tab === 'inbox'
    ? inboxChromeProgress
    : tab === 'explore'
      ? rankingsChromeProgress
      : tab === 'sources'
        ? sourcesChromeProgress
        : tab === 'saved'
          ? savedChromeProgress
          : profileChromeProgress;
  const mainSurfaceActive = selectedItem === null && selectedRankingItem === null;

  useEffect(() => {
    const subscription = BackHandler.addEventListener('hardwareBackPress', () => {
      if (selectedItem) {
        dispatchNavigation({ type: 'close_article' });
        return true;
      }
      if (selectedRankingItem) {
        dispatchNavigation({ type: 'close_ranking_item' });
        return true;
      }
      if (tab === 'inbox' && selectedInboxSourceId) {
        if (sourceFeedReturnTab !== 'inbox') {
          inboxChromeProgress.value = 0;
          rankingsChromeProgress.value = 0;
          sourcesChromeProgress.value = 0;
          savedChromeProgress.value = 0;
          profileChromeProgress.value = 0;
        }
        dispatchNavigation({ type: 'close_source' });
        return true;
      }
      if (tab !== 'inbox') {
        inboxChromeProgress.value = 0;
        rankingsChromeProgress.value = 0;
        sourcesChromeProgress.value = 0;
        savedChromeProgress.value = 0;
        profileChromeProgress.value = 0;
        if (selectedInboxSourceId !== null) setChannelVisitId((current) => current + 1);
        dispatchNavigation({ tab: 'inbox', type: 'select_tab' });
        return true;
      }
      return false;
    });
    return () => subscription.remove();
  }, [inboxChromeProgress, profileChromeProgress, rankingsChromeProgress, savedChromeProgress, selectedInboxSourceId, selectedItem, selectedRankingItem, sourceFeedReturnTab, sourcesChromeProgress, tab]);

  return (
    <View style={styles.shell}>
      <View style={styles.screen}>
        {visitedTabs.has('inbox') ? <View style={tab === 'inbox' ? styles.tabScreen : styles.tabScreenHidden}><MemoHomeScreen channelVisitId={channelVisitId} onProfile={openProfile} active={mainSurfaceActive && tab === 'inbox'} chromeProgress={inboxChromeProgress} onClearSource={closeSourceFeed} onOpenArticle={openArticle} onSelectSourceId={selectSourceId} selectedSourceId={selectedInboxSourceId} session={session} /></View> : null}
        {visitedTabs.has('explore') ? <View style={tab === 'explore' ? styles.tabScreen : styles.tabScreenHidden}><MemoRankingsScreen onProfile={openProfile} active={mainSurfaceActive && tab === 'explore'} chromeProgress={rankingsChromeProgress} onOpenItem={openRankingItem} session={session} /></View> : null}
        {visitedTabs.has('sources') ? <View style={tab === 'sources' ? styles.tabScreen : styles.tabScreenHidden}><MemoSourcesScreen onProfile={openProfile} active={mainSurfaceActive && tab === 'sources'} chromeProgress={sourcesChromeProgress} onOpenSource={openSource} session={session} /></View> : null}
        {visitedTabs.has('saved') ? <View style={tab === 'saved' ? styles.tabScreen : styles.tabScreenHidden}><MemoSavedScreen onProfile={openProfile} active={mainSurfaceActive && tab === 'saved'} chromeProgress={savedChromeProgress} onOpenArticle={openArticle} session={session} /></View> : null}
        {visitedTabs.has('settings') ? <View style={tab === 'settings' ? styles.tabScreen : styles.tabScreenHidden}><MemoProfileScreen active={mainSurfaceActive && tab === 'settings'} chromeProgress={profileChromeProgress} onChangeServer={onChangeServer} onLogout={onLogout} session={session} authClient={authClient} /></View> : null}
      </View>
      {!selectedItem && !selectedRankingItem ? <MotionReaderTabBar activeTab={tab} onChange={selectTab} progress={activeChromeProgress} /> : null}
      {selectedItem ? <View style={styles.readerOverlay}><ArticleReaderScreen item={selectedItem} onBack={closeArticle} renderReaderHtml={buildReaderHtml} session={session} /></View> : null}
      {selectedRankingItem ? <View style={styles.readerOverlay}><RankingPreviewScreen item={selectedRankingItem} onBack={closeRankingItem} session={session} /></View> : null}
    </View>
  );
}

const styles = StyleSheet.create({
  shell: { flex: 1 },
  screen: { flex: 1 },
  tabScreen: { flex: 1 },
  tabScreenHidden: { display: 'none' },
  readerOverlay: { backgroundColor: colors.background, bottom: 0, left: 0, position: 'absolute', right: 0, top: 0, zIndex: 10 },
});
