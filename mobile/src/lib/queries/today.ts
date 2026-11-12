/**
 * Today's server data (react-guidelines §4): one definition per resource, and the commands that
 * change it. Commands update the cache themselves (react-guidelines §4.6); pages only call them.
 */
import { infiniteQueryOptions, queryOptions, type InfiniteData } from '@tanstack/react-query';

import { isApiError } from '../api/errors';
import type { ArticleDetail, MeResponse, PageFeedItem, SubscriptionResponse } from '../api/schema.gen';
import { queryClient, session } from '../app-session';
import { patchItems, scopeParams, type FeedScope } from '../feed';
import { key } from '../query';

const PAGE_SIZE = 30;

export function meQuery(identity: string) {
  return queryOptions({
    queryKey: key(identity, 'me'),
    queryFn: ({ signal }) => session.api().request<MeResponse>('/me', { signal }),
    staleTime: 5 * 60_000,
  });
}

export function subscriptionsQuery(identity: string) {
  return queryOptions({
    queryKey: key(identity, 'subscriptions'),
    queryFn: ({ signal }) => session.api().request<SubscriptionResponse[]>('/subscriptions', { signal }),
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

type SubscriptionPatch = Partial<Pick<SubscriptionResponse, 'include_in_home' | 'new_count'>>;

/** Change only the given fields, so concurrent commands on one subscription do not undo each other. */
function patchSubscription(identity: string, id: string, patch: SubscriptionPatch) {
  queryClient.setQueryData<SubscriptionResponse[]>(key(identity, 'subscriptions'), (list) =>
    list?.map((s) => (s.id === id ? { ...s, ...patch } : s)),
  );
}

/** One change per subscription at a time; later ones wait (no overlapping PATCH, no lost update). */
const queues = new Map<string, Promise<unknown>>();
function serially<T>(id: string, task: () => Promise<T>): Promise<T> {
  const run = (queues.get(id) ?? Promise.resolve()).catch(() => undefined).then(task);
  queues.set(id, run);
  void run.finally(() => queues.get(id) === run && queues.delete(id)).catch(() => undefined);
  return run;
}

/** Show a subscription in 全部 or not (the picker's checkbox); 全部 reloads after. */
const wanted = new Map<string, boolean>();

export function setIncludeInHome(identity: string, subscription: SubscriptionResponse, include: boolean): Promise<void> {
  const list = { queryKey: key(identity, 'subscriptions') };
  // The cache shows what the user last asked for; requests run in order and never write it back,
  // so an earlier request finishing late cannot undo a later tap.
  wanted.set(subscription.id, include);
  patchSubscription(identity, subscription.id, { include_in_home: include });
  return serially(subscription.id, async () => {
    await queryClient.cancelQueries(list);
    try {
      await session.api().request<SubscriptionResponse>(`/subscriptions/${subscription.id}`, {
        method: 'PATCH',
        body: { include_in_home: include },
      });
      void queryClient.invalidateQueries({ queryKey: key(identity, 'feed', { kind: 'home' }) });
    } catch (error) {
      if (definite(error)) {
        if (wanted.get(subscription.id) === include) patchSubscription(identity, subscription.id, { include_in_home: !include });
        throw error;
      }
      void queryClient.invalidateQueries(list);
      throw new UnknownOutcome();
    }
  });
}

/** Opening one subscription clears its new-content count up to what it has now (old app). */
export function markViewed(identity: string, subscription: SubscriptionResponse): Promise<void> {
  if (subscription.new_count <= 0 || subscription.latest_sequence <= 0) return Promise.resolve();
  return serially(subscription.id, async () => {
    const updated = await session.api().request<SubscriptionResponse>(`/subscriptions/${subscription.id}/viewed`, {
      method: 'POST',
      body: { sequence: subscription.latest_sequence },
    });
    patchSubscription(identity, subscription.id, { new_count: updated.new_count });
  });
}
