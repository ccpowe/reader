/**
 * The reader page's web view as a state machine (react-guidelines §2): which view is shown, the
 * page it shows, and its load state. Pure: runs under `node --test`.
 *
 *   switch(view)    → that view. Between the original page and reader mode the page stays loaded
 *                     underneath (old app: back from reader mode is where you were); to and from
 *                     the YouTube player it is a fresh load (the WebView remounts)
 *   show-extracted  → reader mode with the article the page itself gave (no full body on the server)
 *   open-page(url)  → the original-page view at another address (a link tapped in reader mode, or
 *                     the HN discussion); the view it came from is remembered
 *   back            → from such an address, back to the view it came from (Android back at its
 *                     first page, or the discussion tool again)
 *   load-start      → loading; an earlier error is cleared (a later navigation may succeed)
 *   progress(p)     → loading p
 *   load-end        → loaded
 *   failed(message) → failed with that message, until the next load starts
 *   history(back,url) → whether the page can go back (Android back goes back inside it first) and
 *                     the address it shows now (share / open in browser use it)
 *   retry           → a reload of the same document (the route calls WebView.reload())
 */
import type { ExtractedArticle } from './web-translation-host.ts';

export type ReaderView = 'page' | 'reader' | 'player';

export type ReaderPageState = {
  view: ReaderView;
  articleUrl: string;
  /** what the 'page' view loads */
  pageUrl: string;
  /** the address the page shows now (it may have followed links) */
  currentUrl: string;
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
  | { type: 'history'; canGoBack: boolean; url?: string }
  | { type: 'retry' };

export function initialReaderPage(view: ReaderView, articleUrl: string): ReaderPageState {
  return {
    view,
    articleUrl,
    pageUrl: articleUrl,
    currentUrl: articleUrl,
    load: { status: 'loading', progress: 0 },
    canGoBack: false,
    origin: view,
    extracted: null,
  };
}

export function readerPageReducer(state: ReaderPageState, event: ReaderPageEvent): ReaderPageState {
  switch (event.type) {
    case 'switch':
      if (event.view === state.view) return state;
      // Reader mode lies over the original page, which stays as it was (its load, history, place):
      // leaving reader mode is always back to that page, wherever it had gone.
      if (state.view === 'reader' && event.view === 'page') return { ...state, view: 'page' };
      if (state.view === 'page' && event.view === 'reader' && state.pageUrl === state.articleUrl) return { ...state, view: event.view };
      // Otherwise the tools switch to the article's own view: the article's reader mode or player, not
      // a link opened from it.
      return {
        ...state,
        view: event.view,
        pageUrl: state.articleUrl,
        currentUrl: state.articleUrl,
        load: { status: 'loading', progress: 0 },
        canGoBack: false,
      };
    case 'show-extracted':
      // Extracted from the page that stays loaded underneath.
      return { ...state, view: 'reader', extracted: event.article };
    case 'open-page': {
      // From one other address to the next, back still returns to where the first one was opened.
      const elsewhere = state.view === 'page' && state.pageUrl !== state.articleUrl;
      return {
        ...state,
        view: 'page',
        pageUrl: event.url,
        currentUrl: event.url,
        origin: elsewhere ? state.origin : state.view,
        load: { status: 'loading', progress: 0 },
        canGoBack: false,
      };
    }
    case 'back':
      return state.view === 'page' && state.pageUrl !== state.articleUrl
        ? {
            ...state,
            view: state.origin,
            pageUrl: state.articleUrl,
            currentUrl: state.articleUrl,
            load: { status: 'loading', progress: 0 },
            canGoBack: false,
          }
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
    case 'history': {
      const currentUrl = event.url ?? state.currentUrl;
      return event.canGoBack === state.canGoBack && currentUrl === state.currentUrl
        ? state
        : { ...state, canGoBack: event.canGoBack, currentUrl };
    }
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

/** The page WebView remounts when this changes: a different document, not a navigation. */
export function documentKey(state: ReaderPageState): string {
  if (state.view === 'page' || state.view === 'reader') return `page:${state.pageUrl}`;
  return state.view;
}

/** The reader-mode document, over the page. */
export function readerKey(state: ReaderPageState): string {
  return state.extracted ? `reader:${state.extracted.url}` : 'reader';
}

/** What share and open-in-browser use: the page as it is now, or the article reader mode shows. */
export function shareUrl(state: ReaderPageState): string {
  if (state.view === 'page') return state.currentUrl;
  if (state.view === 'reader') return state.extracted?.url ?? state.articleUrl;
  return state.articleUrl;
}
