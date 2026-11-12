/**
 * The reader page's web view as a state machine (react-guidelines §2): which view is shown, the
 * page it shows, and its load state. Pure: runs under `node --test`.
 *
 *   switch(view)    → that view, fresh load (the WebView remounts: `key` = view + url)
 *   show-extracted  → reader mode with the article the page itself gave (no full body on the server)
 *   open-page(url)  → the original-page view at another address (a link tapped in reader mode, or
 *                     the HN discussion); the view it came from is remembered
 *   back            → from such an address, back to the view it came from (Android back at its
 *                     first page, or the discussion tool again)
 *   load-start      → loading; an earlier error is cleared (a later navigation may succeed)
 *   progress(p)     → loading p
 *   load-end        → loaded
 *   failed(message) → failed with that message, until the next load starts
 *   history(back)   → whether the page can go back (Android back goes back inside it first)
 *   retry           → a reload of the same document (the route calls WebView.reload())
 */
import type { ExtractedArticle } from './web-translation-host.ts';

export type ReaderView = 'page' | 'reader' | 'player';

export type ReaderPageState = {
  view: ReaderView;
  articleUrl: string;
  /** what the 'page' view loads */
  pageUrl: string;
  load: { status: 'loading'; progress: number } | { status: 'loaded' } | { status: 'failed'; message: string };
  canGoBack: boolean;
  /** where `back` returns from another address */
  origin: ReaderView;
  /** reader mode's article when it came from the page (kept while switching views) */
  extracted: ExtractedArticle | null;
};

export type ReaderPageEvent =
  | { type: 'switch'; view: ReaderView }
  | { type: 'show-extracted'; article: ExtractedArticle }
  | { type: 'open-page'; url: string }
  | { type: 'back' }
  | { type: 'load-start' }
  | { type: 'progress'; progress: number }
  | { type: 'load-end' }
  | { type: 'failed'; message: string }
  | { type: 'history'; canGoBack: boolean }
  | { type: 'retry' };

export function initialReaderPage(view: ReaderView, articleUrl: string): ReaderPageState {
  return {
    view,
    articleUrl,
    pageUrl: articleUrl,
    load: { status: 'loading', progress: 0 },
    canGoBack: false,
    origin: view,
    extracted: null,
  };
}

export function readerPageReducer(state: ReaderPageState, event: ReaderPageEvent): ReaderPageState {
  switch (event.type) {
    case 'switch':
      // The tools switch between the article's own views: its original page, not a link opened from it.
      return event.view === state.view
        ? state
        : { ...state, view: event.view, pageUrl: state.articleUrl, load: { status: 'loading', progress: 0 }, canGoBack: false };
    case 'show-extracted':
      return {
        ...state,
        view: 'reader',
        pageUrl: state.articleUrl,
        load: { status: 'loading', progress: 0 },
        canGoBack: false,
        extracted: event.article,
      };
    case 'open-page': {
      // From one other address to the next, back still returns to where the first one was opened.
      const elsewhere = state.view === 'page' && state.pageUrl !== state.articleUrl;
      return {
        ...state,
        view: 'page',
        pageUrl: event.url,
        origin: elsewhere ? state.origin : state.view,
        load: { status: 'loading', progress: 0 },
        canGoBack: false,
      };
    }
    case 'back':
      return state.view === 'page' && state.pageUrl !== state.articleUrl
        ? { ...state, view: state.origin, pageUrl: state.articleUrl, load: { status: 'loading', progress: 0 }, canGoBack: false }
        : state;
    case 'load-start':
      return { ...state, load: { status: 'loading', progress: state.load.status === 'loading' ? state.load.progress : 0 } };
    case 'progress':
      // A late progress tick after the page finished or failed does not reopen loading.
      return state.load.status === 'loading' ? { ...state, load: { status: 'loading', progress: event.progress } } : state;
    case 'load-end':
      // A failed load also ends: keep the failure.
      return state.load.status === 'failed' ? state : { ...state, load: { status: 'loaded' } };
    case 'failed':
      return { ...state, load: { status: 'failed', message: event.message } };
    case 'history':
      return event.canGoBack === state.canGoBack ? state : { ...state, canGoBack: event.canGoBack };
    case 'retry':
      return { ...state, load: { status: 'loading', progress: 0 } };
  }
}

/** Whether Android back stays inside the page (its history, or back from another address to where it was opened). */
export function backStaysInside(state: ReaderPageState): 'history' | 'origin' | null {
  if (state.view !== 'page') return null;
  if (state.canGoBack) return 'history';
  return state.pageUrl !== state.articleUrl ? 'origin' : null;
}

/** The WebView remounts when this changes: a different document, not a navigation. */
export function documentKey(state: ReaderPageState): string {
  if (state.view === 'page') return `page:${state.pageUrl}`;
  return state.view === 'reader' && state.extracted ? `reader:${state.extracted.url}` : state.view;
}
