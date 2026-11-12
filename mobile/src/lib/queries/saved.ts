/**
 * 收藏's server data (react-guidelines §4): saved items by search and folder, newest saved first,
 * and unsaving from the list with an undo window (as unsubscribing, P27): the item is hidden at
 * once and unsaved when the window closes; undo sends nothing, so it keeps its place.
 */
import { infiniteQueryOptions, type InfiniteData } from '@tanstack/react-query';

import type { PageSavedItem } from '../api/schema.gen';
import { queryClient, session } from '../app-session';
import { identityOf } from '../connection/session';
import { key } from '../query';
import { savedParams, type SavedScope } from '../saved';
import { pendingKey, pendingUnsaves } from './pending-unsave';
import { setSaved } from './today';

const PAGE_SIZE = 30;

export function savedQuery(identity: string, scope: SavedScope) {
  return infiniteQueryOptions({
    queryKey: key(identity, 'saved', scope),
    queryFn: ({ pageParam, signal }) =>
      session.api().request<PageSavedItem>('/saved', {
        query: { ...savedParams(scope), cursor: pageParam ?? undefined, limit: PAGE_SIZE },
        signal,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    gcTime: 5 * 60_000,
  });
}

/** Items being unsaved from the list, per identity and article (hidden until the lists no longer have them). */
const hidden = new Set<string>();
const listeners = new Set<() => void>();
let version = 0;
function changed() {
  version += 1;
  listeners.forEach((listener) => listener());
}

export const hiddenSaved = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => void listeners.delete(listener);
  },
  getSnapshot: () => version,
  has: (identity: string, id: string) => hidden.has(pendingKey(identity, id)),
};

type SavedData = InfiniteData<PageSavedItem, string | null>;

/**
 * Unsave from 收藏 with an undo window. Returns undo. When the window closes the item is unsaved
 * like a bookmark tap (setSaved: every cached copy follows); `onFailed` hears a refusal or no answer,
 * and the item shows again. One waiting unsave per item: a second call returns the same undo; any
 * later save or unsave of it (setSaved) cancels this one. Signed out or into another account
 * meanwhile: nothing is sent.
 */
export function unsaveLater(
  identity: string,
  articleId: string,
  delayMs: number,
  onFailed: (error: unknown) => void,
): () => void {
  const entry = pendingKey(identity, articleId);
  const existing = pendingUnsaves.get(entry);
  if (existing) return existing;

  let done = false;
  hidden.add(entry);
  changed();
  const show = () => {
    hidden.delete(entry);
    changed();
  };
  const cancel = () => {
    if (done) return;
    done = true;
    clearTimeout(timer);
    pendingUnsaves.delete(entry);
    show();
  };
  const timer = setTimeout(() => {
    done = true;
    pendingUnsaves.delete(entry); // before setSaved, which would otherwise cancel this very unsave
    if (identityOf(session.getSnapshot()) !== identity) return show();
    setSaved(identity, articleId, false).then(
      () => {
        // Out of every cached list before it shows again, whether or not the re-read succeeds.
        queryClient.setQueriesData<SavedData>({ queryKey: [identity, 'saved'] }, (data) =>
          data
            ? {
                ...data,
                pages: data.pages.map((page) => ({ ...page, items: page.items.filter((item) => item.id !== articleId) })),
              }
            : data,
        );
        show();
      },
      (error: unknown) => {
        show();
        onFailed(error);
      },
    );
  }, delayMs);
  pendingUnsaves.set(entry, cancel);
  return cancel;
}
