/**
 * 榜单's server data (react-guidelines §4): one board version, its manual refresh, and the
 * translations the list still lacks.
 */
import { queryOptions } from '@tanstack/react-query';

import { errorCode, isApiError } from '../api/errors';
import type {
  BoardResponse,
  PreferenceResponse,
  SavedFromBoard,
  SaveFromBoardRequest,
  TranslatedItem,
  TranslateRequest,
  TranslateResponse,
} from '../api/schema.gen';
import { queryClient, session } from '../app-session';
import { identityOf } from '../connection/session';
import { missingTranslations, viewParams, type BoardView } from '../boards';
import { key } from '../query';
import { UnknownOutcome } from './today';

export function boardQuery(identity: string, view: BoardView) {
  return queryOptions({
    queryKey: key(identity, 'board', view),
    // The server fetches an expired board on the spot (up to ~10 s), so give it time.
    queryFn: ({ signal }) =>
      session.api().request<BoardResponse>(`/boards/${view.board}`, { query: viewParams(view), signal, timeoutMs: 25_000 }),
    // The server decides when a board is old; asking again soon only returns the same copy.
    staleTime: 60_000,
    // A first load still running (503 board_loading) is asked again; "unavailable" is an answer.
    retry: (failures, error) => {
      if (errorCode(error) === 'board_loading') return failures < 6;
      return failures < 2 && isApiError(error) && (error.problem.kind === 'network' || error.problem.kind === 'timeout');
    },
    retryDelay: (attempt, error) => {
      const problem = isApiError(error) ? error.problem : null;
      if (problem?.kind === 'http' && problem.retryAfter) return problem.retryAfter * 1000;
      return Math.min(1_000 * 2 ** attempt, 8_000);
    },
  });
}

/**
 * Saves and unsaves made in this app run, per identity and entry URL: a board copy fetched before
 * one of them must not show the old state (see refreshBoard).
 */
const savedChanges = new Map<string, { saved: boolean; at: number }>();
const saving = new Set<string>();
const entryKey = (identity: string, url: string) => `${identity}\u0000${url}`;

/** Fetch the board again now (the server allows it once a minute; otherwise it returns the current copy). */
export async function refreshBoard(identity: string, view: BoardView): Promise<void> {
  const started = Date.now();
  const fresh = await session
    .api()
    .request<BoardResponse>(`/boards/${view.board}/refresh`, { method: 'POST', query: viewParams(view), timeoutMs: 25_000 });
  // A save made while this was on its way is newer than the copy's saved state.
  const items = fresh.items.map((item) => {
    const key = entryKey(identity, item.url);
    const change = savedChanges.get(key);
    return change && (change.at >= started || saving.has(key)) && change.saved !== item.is_saved
      ? { ...item, is_saved: change.saved }
      : item;
  });
  queryClient.setQueryData(boardQuery(identity, view).queryKey, { ...fresh, items });
}

export function translationPreferenceQuery(identity: string) {
  return queryOptions({
    queryKey: key(identity, 'translation-preference'),
    queryFn: ({ signal }) => session.api().request<PreferenceResponse>('/me/translation-preference', { signal }),
    staleTime: 5 * 60_000,
  });
}

export type BoardTranslations = { titles: Map<string, string>; descriptions: Map<string, string>; fingerprint: string };

const ROUNDS = 6;

function wait(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener('abort', () => {
      clearTimeout(timer);
      reject(signal.reason);
    });
  });
}

/** Ask for texts until each is finished or failed (pending ones again after a pause), a few rounds at most. */
async function translateAll(purpose: TranslateRequest['purpose'], texts: string[], signal: AbortSignal) {
  const done = new Map<string, string>();
  let fingerprint = '';
  let left = texts;
  for (let round = 0; round < ROUNDS && left.length > 0; round++) {
    if (round > 0) await wait(2_000, signal);
    const pending: string[] = [];
    for (let start = 0; start < left.length; start += 50) {
      const chunk = left.slice(start, start + 50);
      const response = await session.api().request<TranslateResponse>('/translations', {
        method: 'POST',
        body: { purpose, texts: chunk } satisfies TranslateRequest,
        signal,
        timeoutMs: 25_000,
      });
      fingerprint = response.engine_fingerprint;
      response.items.forEach((item: TranslatedItem, index) => {
        if (item.status === 'succeeded' && item.text?.trim()) done.set(chunk[index], item.text.trim());
        else if (item.status === 'pending') pending.push(chunk[index]);
      });
    }
    left = pending;
  }
  return { done, fingerprint };
}

/**
 * The translations a loaded board lacks (titles and descriptions), asked for once per board copy.
 * The server keeps them, so the next copy of the board comes with them already.
 */
export function boardTranslationsQuery(
  identity: string,
  view: BoardView,
  board: BoardResponse | undefined,
  preference: PreferenceResponse | undefined,
) {
  const missing = board ? missingTranslations(board.items) : { titles: [], descriptions: [] };
  return queryOptions({
    // Another language or engine is another set of translations.
    queryKey: key(
      identity,
      'board-translations',
      view,
      board?.fetched_at ?? null,
      preference?.target_locale ?? null,
      preference?.engine_fingerprint ?? null,
    ),
    enabled: preference?.enabled === true && (missing.titles.length > 0 || missing.descriptions.length > 0),
    queryFn: async ({ signal }): Promise<BoardTranslations> => {
      const [titles, descriptions] = await Promise.all([
        missing.titles.length ? translateAll('board_title', missing.titles, signal) : null,
        missing.descriptions.length ? translateAll('board_description', missing.descriptions, signal) : null,
      ]);
      return {
        titles: titles?.done ?? new Map(),
        descriptions: descriptions?.done ?? new Map(),
        fingerprint: titles?.fingerprint || descriptions?.fingerprint || '',
      };
    },
    staleTime: Infinity,
    // Translation is a nicety: a failure leaves the original titles, without retry storms.
    retry: false,
  });
}

/**
 * Save or unsave a board entry (POST /saved/from-board makes it an article without a source and
 * saves it; the same link gives the same article, which unsaving then removes from /saved).
 * Shown at once in the cached board; a definite "no" rolls back, no answer re-reads (UnknownOutcome).
 */
export async function setBoardSaved(identity: string, view: BoardView, url: string, saved: boolean): Promise<void> {
  const entry = entryKey(identity, url);
  // A second tap while one is on its way is ignored (no overlapping save / unsave).
  if (saving.has(entry)) return;
  saving.add(entry);
  const boards = { queryKey: [identity, 'board'] };
  // Every cached copy that lists this link (another version of the board may too).
  const apply = (value: boolean) => {
    savedChanges.set(entry, { saved: value, at: Date.now() });
    queryClient.setQueriesData<BoardResponse>(boards, (data) =>
      data && data.items?.some((item) => item.url === url)
        ? { ...data, items: data.items.map((item) => (item.url === url ? { ...item, is_saved: value } : item)) }
        : data,
    );
  };
  try {
    await queryClient.cancelQueries(boards);
    apply(saved);
    const api = session.api();
    const { article_id } = await api.request<SavedFromBoard>('/saved/from-board', {
      method: 'POST',
      body: { board: view.board, variant: view.variant || null, subreddit: view.subreddit, url } satisfies SaveFromBoardRequest,
    });
    if (!saved) {
      // Signed out or into another account meanwhile: this account's unsave must not run as theirs.
      if (identityOf(session.getSnapshot()) !== identity) return;
      await api.request(`/saved/${article_id}`, { method: 'DELETE' });
    }
    void queryClient.invalidateQueries({ queryKey: [identity, 'saved'] });
  } catch (error) {
    if (isApiError(error) && error.problem.kind === 'http') {
      apply(!saved);
      throw error;
    }
    void queryClient.invalidateQueries(boards);
    void queryClient.invalidateQueries({ queryKey: [identity, 'saved'] });
    throw new UnknownOutcome();
  } finally {
    saving.delete(entry);
  }
}
