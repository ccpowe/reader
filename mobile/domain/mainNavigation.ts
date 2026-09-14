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
