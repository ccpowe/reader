import { useEffect, useReducer } from 'react';
import { BackHandler, StyleSheet, View } from 'react-native';
import type { Session, ReaderAuthClient } from '../lib/readerAuth';
import { useSharedValue } from 'react-native-reanimated';

import type { FeedItem, RankingItem, SourceListItem } from '../lib/api';
import { createMainNavigationState, reduceMainNavigation } from '../domain/mainNavigation';
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

  function resetMotionChrome() {
    inboxChromeProgress.value = 0;
    rankingsChromeProgress.value = 0;
    sourcesChromeProgress.value = 0;
    savedChromeProgress.value = 0;
    profileChromeProgress.value = 0;
  }

  function selectTab(nextTab: ReaderTab) {
    resetMotionChrome();
    dispatchNavigation({ tab: nextTab, type: 'select_tab' });
  }

  function openArticle(item: FeedItem) {
    if (item.ranking_kind) {
      dispatchNavigation({ type: 'open_ranking_item', item: {
        title: item.title, translated_title: item.translated_title,
        title_translation_status: item.translation_status,
        description: item.excerpt, translated_description: null,
        description_translation_status: null, translation_locale: item.translation_locale,
        translation_key: `saved:${item.content_id}`, url: item.external_url,
        source_label: item.source_name, rank: 0, author: item.author_name,
        score: null, comments: null, language: null, stars: null, forks: null,
        stars_this_period: null, image_urls: item.thumbnail_url ? [item.thumbnail_url] : [],
        saved_content_id: item.content_id, saved_state: item.is_saved,
      } });
      return;
    }
    dispatchNavigation({ item, type: 'open_article' });
  }

  function openRankingItem(item: RankingItem) {
    dispatchNavigation({ item, type: 'open_ranking_item' });
  }

  function openSource(source: SourceListItem) {
    resetMotionChrome();
    dispatchNavigation({ returnTab: 'sources', sourceId: source.source_id, type: 'open_source' });
  }

  function closeSourceFeed() {
    if (sourceFeedReturnTab !== 'inbox') resetMotionChrome();
    dispatchNavigation({ type: 'close_source' });
  }

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
        {visitedTabs.has('inbox') ? <View style={tab === 'inbox' ? styles.tabScreen : styles.tabScreenHidden}><HomeScreen onProfile={() => selectTab('settings')} active={mainSurfaceActive && tab === 'inbox'} chromeProgress={inboxChromeProgress} onClearSource={closeSourceFeed} onOpenArticle={openArticle} onSelectSourceId={(sourceId) => dispatchNavigation(sourceId === null ? { type: 'clear_source_filter' } : { sourceId, type: 'set_source_id' })} selectedSourceId={selectedInboxSourceId} session={session} /></View> : null}
        {visitedTabs.has('explore') ? <View style={tab === 'explore' ? styles.tabScreen : styles.tabScreenHidden}><RankingsScreen onProfile={() => selectTab('settings')} active={mainSurfaceActive && tab === 'explore'} chromeProgress={rankingsChromeProgress} onOpenItem={openRankingItem} session={session} /></View> : null}
        {visitedTabs.has('sources') ? <View style={tab === 'sources' ? styles.tabScreen : styles.tabScreenHidden}><SourcesScreen onProfile={() => selectTab('settings')} active={mainSurfaceActive && tab === 'sources'} chromeProgress={sourcesChromeProgress} onOpenSource={openSource} session={session} /></View> : null}
        {visitedTabs.has('saved') ? <View style={tab === 'saved' ? styles.tabScreen : styles.tabScreenHidden}><SavedScreen onProfile={() => selectTab('settings')} active={mainSurfaceActive && tab === 'saved'} chromeProgress={savedChromeProgress} onOpenArticle={openArticle} session={session} /></View> : null}
        {visitedTabs.has('settings') ? <View style={tab === 'settings' ? styles.tabScreen : styles.tabScreenHidden}><ProfileScreen active={mainSurfaceActive && tab === 'settings'} chromeProgress={profileChromeProgress} onChangeServer={onChangeServer} onLogout={onLogout} session={session} authClient={authClient} /></View> : null}
      </View>
      {!selectedItem && !selectedRankingItem ? <MotionReaderTabBar activeTab={tab} onChange={selectTab} progress={activeChromeProgress} /> : null}
      {selectedItem ? <View style={styles.readerOverlay}><ArticleReaderScreen item={selectedItem} onBack={() => dispatchNavigation({ type: 'close_article' })} renderReaderHtml={buildReaderHtml} session={session} /></View> : null}
      {selectedRankingItem ? <View style={styles.readerOverlay}><RankingPreviewScreen item={selectedRankingItem} onBack={() => dispatchNavigation({ type: 'close_ranking_item' })} session={session} /></View> : null}
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
