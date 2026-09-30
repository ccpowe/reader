import type { FeedItem, RankingItem } from '../lib/api';
import type { ReaderTab } from '../components/ReaderTabBar';

type SourceFeedReturnTab = 'inbox' | 'sources';

export type MainNavigationState = {
  selectedInboxSourceId: string | null;
  selectedItem: FeedItem | null;
  selectedRankingItem: RankingItem | null;
  sourceFeedReturnTab: SourceFeedReturnTab;
  tab: ReaderTab;
  visitedTabs: ReadonlySet<ReaderTab>;
};

export type MainNavigationAction =
  | { type: 'select_tab'; tab: ReaderTab }
  | { type: 'open_article'; item: FeedItem }
  | { type: 'close_article' }
  | { type: 'open_ranking_item'; item: RankingItem }
  | { type: 'close_ranking_item' }
  | { type: 'open_source'; sourceId: string; returnTab: SourceFeedReturnTab }
  | { type: 'set_source_id'; sourceId: string }
  | { type: 'clear_source_filter' }
  | { type: 'close_source' };

export function rankingFromSaved(item: FeedItem): RankingItem | null {
  if (!item.ranking_kind) return null;
  if (item.ranking_item) {
    return {
      ...item.ranking_item,
      ranking_context: { kind: item.ranking_kind },
      saved_content_id: item.content_id,
      saved_state: item.is_saved,
    };
  }
  return {
    title: item.title, translated_title: item.translated_title,
    title_translation_status: item.translation_status,
    description: item.excerpt, translated_description: null,
    description_translation_status: null, translation_locale: item.translation_locale,
    translation_key: `saved:${item.content_id}`, url: item.external_url,
    source_label: item.source_name, rank: 0, author: item.author_name,
    score: null, comments: null, language: null, stars: null, forks: null,
    stars_this_period: null, image_urls: item.thumbnail_url ? [item.thumbnail_url] : [],
    body_text: null, body_segments: [], author_handle: null, published_at: item.published_at,
    retweet_count: null, bookmark_count: null, video_url: null, video_thumbnail_url: null,
    saved_content_id: item.content_id, saved_state: item.is_saved,
    ranking_context: { kind: item.ranking_kind },
  };
}

export function createMainNavigationState(): MainNavigationState {
  return {
    selectedInboxSourceId: null,
    selectedItem: null,
    selectedRankingItem: null,
    sourceFeedReturnTab: 'inbox',
    tab: 'inbox',
    visitedTabs: new Set(['inbox']),
  };
}

function visitTab(state: MainNavigationState, tab: ReaderTab): ReadonlySet<ReaderTab> {
  return state.visitedTabs.has(tab) ? state.visitedTabs : new Set([...state.visitedTabs, tab]);
}

export function reduceMainNavigation(
  state: MainNavigationState,
  action: MainNavigationAction,
): MainNavigationState {
  switch (action.type) {
    case 'select_tab':
      return { ...state, tab: action.tab, visitedTabs: visitTab(state, action.tab) };
    case 'open_article':
      return { ...state, selectedItem: action.item };
    case 'close_article':
      return { ...state, selectedItem: null };
    case 'open_ranking_item':
      return { ...state, selectedRankingItem: action.item };
    case 'close_ranking_item':
      return { ...state, selectedRankingItem: null };
    case 'open_source':
      return {
        ...state,
        selectedInboxSourceId: action.sourceId,
        sourceFeedReturnTab: action.returnTab,
        tab: 'inbox',
        visitedTabs: visitTab(state, 'inbox'),
      };
    case 'set_source_id':
      return { ...state, selectedInboxSourceId: action.sourceId };
    case 'clear_source_filter':
      return {
        ...state,
        selectedInboxSourceId: null,
        sourceFeedReturnTab: 'inbox',
      };
    case 'close_source': {
      const returnTab = state.sourceFeedReturnTab;
      return {
        ...state,
        selectedInboxSourceId: null,
        sourceFeedReturnTab: 'inbox',
        tab: returnTab,
        visitedTabs: visitTab(state, returnTab),
      };
    }
  }
}
