/**
 * Today's server data (react-guidelines §4): one definition per resource, and the commands that
 * change it. Commands update the cache themselves (react-guidelines §4.6); pages only call them.
 */
import { infiniteQueryOptions, queryOptions, type InfiniteData } from '@tanstack/react-query';

import { isApiError } from '../api/errors';
import type { ArticleDetail, MeResponse, PageFeedItem } from '../api/schema.gen';
import { queryClient, session } from '../app-session';
import { patchItems, scopeParams, type FeedScope } from '../feed';
import { key } from '../query';
import { pendingUnsaves } from './pending-unsave';

const PAGE_SIZE = 30;

export function meQuery(identity: string) {
  return queryOptions({
    queryKey: key(identity, 'me'),
    queryFn: ({ signal }) => session.api().request<MeResponse>('/me', { signal }),
    staleTime: 5 * 60_000,
  });
}

/**
 * One feed (全部, a folder, or one subscription), newest first, cursor pages. Only "load older"
 * exists, so no maxPages (react-guidelines §4 分页); a feed left alone is dropped after gcTime.
 */
export function feedQuery(identity: string, scope: FeedScope) {
  return infiniteQueryOptions({
    queryKey: key(identity, 'feed', scope),
    queryFn: ({ pageParam, signal }) =>
      session.api().request<PageFeedItem>('/feed', {
        query: { ...scopeParams(scope), cursor: pageParam ?? undefined, limit: PAGE_SIZE },
        signal,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    gcTime: 5 * 60_000,
  });
}

type FeedData = InfiniteData<PageFeedItem, string | null>;

/** The write may or may not have happened (no answer): the cache was re-read from the server. */
export class UnknownOutcome extends Error {
  constructor() {
    super('unknown outcome');
    this.name = 'UnknownOutcome';
  }
}

/** A definite "no" from the server (an HTTP error answer), as opposed to no answer at all. */
function definite(error: unknown): boolean {
  return isApiError(error) && error.problem.kind === 'http';
}

const saving = new Set<string>();

/**
 * Save or unsave, shown at once in every cached feed (P19).
 * - Feed fetches in flight are cancelled first, so an older snapshot cannot overwrite the change.
 * - A definite rejection rolls back; no answer re-reads the feeds and throws UnknownOutcome
 *   (react-guidelines §5: never pretend success or failure).
 * - A second tap on the same item while one is in flight is ignored.
 */
export async function setSaved(identity: string, articleId: string, saved: boolean): Promise<void> {
  // A newer choice than an unsave still waiting in 收藏's undo window.
  pendingUnsaves.supersede(identity, articleId);
  if (saving.has(articleId)) return;
  saving.add(articleId);
  const feeds = { queryKey: [identity, 'feed'] };
  const detail = key(identity, 'article', articleId);
  const apply = (value: boolean) => {
    queryClient.setQueriesData<FeedData>(feeds, (data) =>
      data ? { ...data, pages: patchItems(data.pages, articleId, { is_saved: value }) } : data,
    );
    queryClient.setQueryData<ArticleDetail>(detail, (article) => (article ? { ...article, is_saved: value } : article));
  };
  try {
    await Promise.all([queryClient.cancelQueries(feeds), queryClient.cancelQueries({ queryKey: detail })]);
    apply(saved);
    await session.api().request(`/saved/${articleId}`, { method: saved ? 'PUT' : 'DELETE' });
    void queryClient.invalidateQueries({ queryKey: [identity, 'saved'] });
  } catch (error) {
    if (definite(error)) {
      apply(!saved);
      throw error;
    }
    void queryClient.invalidateQueries(feeds);
    void queryClient.invalidateQueries({ queryKey: detail });
    void queryClient.invalidateQueries({ queryKey: [identity, 'saved'] });
    throw new UnknownOutcome();
  } finally {
    saving.delete(articleId);
  }
}
