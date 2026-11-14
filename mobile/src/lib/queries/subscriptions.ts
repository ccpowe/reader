/**
 * Subscriptions' server data (react-guidelines §4): the list, and the commands that change it —
 * subscribe, rename / move / show in 全部, mark viewed, and unsubscribe with an undo window (P27).
 * Commands update the cache themselves (§4.6); pages only call them.
 *
 * Commands on one subscription run one at a time (`serially`); each checks, right before it sends,
 * that the account that asked is still the signed-in one, so nothing queued runs as someone else.
 */
import { hashKey, queryOptions } from '@tanstack/react-query';

import { errorCode, isApiError } from '../api/errors';
import type { SubscribeRequest, SubscriptionResponse, UpdateSubscriptionRequest } from '../api/schema.gen';
import { queryClient, session } from '../app-session';
import { identityOf } from '../connection/session';
import { key } from '../query';
import { feedQuery, UnknownOutcome } from './today';

type Edit = Partial<Pick<SubscriptionResponse, 'name' | 'group' | 'include_in_home'>>;
type EditField = keyof Edit;

/** Subscriptions being left: hidden at once, deleted when the undo window closes (unsubscribeLater). */
type Leaving = { timer: ReturnType<typeof setTimeout>; sent: boolean; settled: Promise<void> | null };
const leaving = new Map<string, Leaving>();
/** Deleted in this app run: a list response that started before the delete must not bring them back. */
const gone = new Set<string>();
/** Per subscription: the values last confirmed by the server, and the ones the user last asked for. */
const confirmed = new Map<string, Edit>();
const wanted = new Map<string, Edit>();
/** One command per subscription at a time; later ones wait (no overlapping requests, no lost update). */
const queues = new Map<string, Promise<unknown>>();

const listKey = (identity: string) => key(identity, 'subscriptions');
const isCurrent = (identity: string) => identityOf(session.getSnapshot()) === identity;

export function subscriptionsQuery(identity: string) {
  return queryOptions({
    queryKey: listKey(identity),
    queryFn: async ({ signal }) => {
      const list = await session.api().request<SubscriptionResponse[]>('/subscriptions', { signal });
      return (
        list
          .filter((s) => !leaving.has(s.id) && !gone.has(s.id))
          // A change still on its way is newer than this copy.
          .map((s) => (queues.has(s.id) && wanted.has(s.id) ? { ...s, ...wanted.get(s.id) } : s))
      );
    },
  });
}

/** A definite "no" from the server (an HTTP error answer), as opposed to no answer at all. */
function definite(error: unknown): boolean {
  return isApiError(error) && error.problem.kind === 'http';
}

type Patch = Edit & Partial<Pick<SubscriptionResponse, 'new_count'>>;

/** Change only the given fields, so concurrent commands on one subscription do not undo each other. */
function patchSubscription(identity: string, id: string, patch: Patch) {
  queryClient.setQueryData<SubscriptionResponse[]>(listKey(identity), (list) =>
    list?.map((s) => (s.id === id ? { ...s, ...patch } : s)),
  );
}

function serially<T>(id: string, task: () => Promise<T>): Promise<T> {
  const run = (queues.get(id) ?? Promise.resolve()).catch(() => undefined).then(task);
  queues.set(id, run);
  void run.finally(() => queues.get(id) === run && queues.delete(id)).catch(() => undefined);
  return run;
}

/**
 * Follow a source. The server answers with the existing subscription when it is followed already
 * (`existed`); one being left in its undo window is kept instead (or followed again once its
 * delete has gone out). A definite "no" is thrown as is (the form keeps the input); no answer
 * re-reads the list and throws UnknownOutcome.
 */
export async function subscribe(
  identity: string,
  request: SubscribeRequest,
): Promise<{ subscription: SubscriptionResponse; existed: boolean }> {
  try {
    let subscription = await session.api().request<SubscriptionResponse>('/subscriptions', { method: 'POST', body: request });
    const pending = leaving.get(subscription.id);
    if (pending && !pending.sent) {
      clearTimeout(pending.timer);
      leaving.delete(subscription.id);
    } else if (pending) {
      await pending.settled;
      if (!isCurrent(identity)) throw new UnknownOutcome();
      subscription = await session.api().request<SubscriptionResponse>('/subscriptions', { method: 'POST', body: request });
    }
    let existed = false;
    queryClient.setQueryData<SubscriptionResponse[]>(listKey(identity), (list) => {
      if (!list) return list;
      existed = list.some((s) => s.id === subscription.id);
      return existed ? list : [...list, subscription];
    });
    void queryClient.invalidateQueries({ queryKey: listKey(identity) });
    return { subscription, existed };
  } catch (error) {
    if (definite(error)) throw error;
    void queryClient.invalidateQueries({ queryKey: listKey(identity) });
    throw new UnknownOutcome();
  }
}

/**
 * Rename (name null = the source's own name), move (group null = 未分类) or show in 全部; shown at
 * once. On success the server's values replace the shown ones; on a definite "no" a field goes back
 * to its last confirmed value, unless a later change has asked for something else since.
 */
export function editSubscription(identity: string, subscription: SubscriptionResponse, change: Edit): Promise<void> {
  const id = subscription.id;
  const fields = Object.keys(change) as EditField[];
  // With nothing on its way, the cached values are the server's.
  if (!queues.has(id))
    confirmed.set(id, { name: subscription.name, group: subscription.group, include_in_home: subscription.include_in_home });
  wanted.set(id, { ...wanted.get(id), ...change });
  patchSubscription(identity, id, change);
  const stillWanted = (field: EditField) => wanted.get(id)?.[field] === change[field];
  const pick = (from: Edit | undefined) =>
    Object.fromEntries(fields.filter(stillWanted).map((field) => [field, from?.[field]])) as Edit;

  return serially(id, async () => {
    if (!isCurrent(identity)) return; // signed out or into another account meanwhile: nothing is sent as them
    await queryClient.cancelQueries({ queryKey: listKey(identity) });
    try {
      const updated = await session.api().request<SubscriptionResponse>(`/subscriptions/${id}`, {
        method: 'PATCH',
        body: change satisfies UpdateSubscriptionRequest,
      });
      confirmed.set(id, { name: updated.name, group: updated.group, include_in_home: updated.include_in_home });
      patchSubscription(identity, id, pick(updated));
      // Folders and 全部 change the Today feeds.
      if ('group' in change || 'include_in_home' in change) void queryClient.invalidateQueries({ queryKey: [identity, 'feed'] });
      // 收藏's folder lists follow the folder of the source.
      if ('group' in change) void queryClient.invalidateQueries({ queryKey: [identity, 'saved'] });
    } catch (error) {
      if (!definite(error)) {
        void queryClient.invalidateQueries({ queryKey: listKey(identity) });
        throw new UnknownOutcome();
      }
      patchSubscription(identity, id, pick(confirmed.get(id)));
      throw error;
    }
  });
}

export function updateSubscription(
  identity: string,
  subscription: SubscriptionResponse,
  change: Pick<UpdateSubscriptionRequest, 'name' | 'group'>,
): Promise<void> {
  return editSubscription(identity, subscription, change);
}

/** Show a subscription in 全部 or not (Today's channel checkbox, the ⋮ menu's switch). */
export function setIncludeInHome(identity: string, subscription: SubscriptionResponse, include: boolean): Promise<void> {
  return editSubscription(identity, subscription, { include_in_home: include });
}

/** Opening one subscription clears its new-content count up to what it has now (old app). */
export function markViewed(identity: string, subscription: SubscriptionResponse): Promise<void> {
  if (subscription.new_count <= 0 || subscription.latest_sequence <= 0) return Promise.resolve();
  return serially(subscription.id, async () => {
    if (!isCurrent(identity)) return;
    const updated = await session.api().request<SubscriptionResponse>(`/subscriptions/${subscription.id}/viewed`, {
      method: 'POST',
      body: { sequence: subscription.latest_sequence },
    });
    // A list read still on its way began before this: its count is the old one (cancelled, it keeps the current list).
    await queryClient.cancelQueries({ queryKey: listKey(identity) });
    patchSubscription(identity, subscription.id, { new_count: updated.new_count });
  });
}

/**
 * While one subscription's feed is shown: every load of it that succeeds (the first, a retry, a
 * refresh) clears its count up to what the list knew when that load started (old app). A load that
 * fails or is cancelled (the page left) clears nothing. Returns the unsubscribe.
 */
export function clearCountOnLoad(identity: string, id: string): () => void {
  const feedKey = feedQuery(identity, { kind: 'subscription', id }).queryKey;
  const feedHash = hashKey(feedKey);
  const known = () => queryClient.getQueryData<SubscriptionResponse[]>(listKey(identity))?.find((s) => s.id === id);
  // The page's first load may have started just before this listens.
  let sequence = queryClient.getQueryState(feedKey)?.fetchStatus === 'fetching' ? (known()?.latest_sequence ?? 0) : 0;
  return queryClient.getQueryCache().subscribe((event) => {
    if (event.type !== 'updated' || event.query.queryHash !== feedHash) return;
    if (event.action.type === 'fetch') sequence = known()?.latest_sequence ?? 0;
    else if (event.action.type === 'success') {
      const subscription = known();
      if (subscription && sequence > 0) void markViewed(identity, { ...subscription, latest_sequence: sequence }).catch(() => undefined);
    }
  });
}

/**
 * Unsubscribe with an undo window (P27): the subscription disappears at once and is deleted when
 * `delayMs` has passed without undo. Deleting and following again would be a new subscription (its
 * new-content position lost), so undo sends nothing. Closing the app inside the window keeps it.
 * Returns undo. `onFailed` hears a delete the server refused or did not answer (the row comes back).
 */
export function unsubscribeLater(
  identity: string,
  subscription: SubscriptionResponse,
  delayMs: number,
  onFailed: (error: unknown) => void,
): () => void {
  const id = subscription.id;
  const list = { queryKey: listKey(identity) };
  const restore = () => {
    queryClient.setQueryData<SubscriptionResponse[]>(listKey(identity), (data) =>
      data && !data.some((s) => s.id === id) ? [...data, subscription] : data,
    );
    void queryClient.invalidateQueries(list);
  };
  const previous = leaving.get(id);
  if (previous && !previous.sent) clearTimeout(previous.timer);
  void queryClient.cancelQueries(list);
  queryClient.setQueryData<SubscriptionResponse[]>(listKey(identity), (data) => data?.filter((s) => s.id !== id));

  const entry: Leaving = { timer: setTimeout(send, delayMs), sent: false, settled: null };
  leaving.set(id, entry);

  function send() {
    entry.sent = true;
    entry.settled = serially(id, async () => {
      try {
        if (!isCurrent(identity)) return; // nothing is sent as another account; it stays followed
        await session.api().request(`/subscriptions/${id}`, { method: 'DELETE' });
        gone.add(id);
      } catch (error) {
        if (errorCode(error) === 'subscription_not_found') gone.add(id);
        else {
          leaving.delete(id);
          restore();
          onFailed(error);
          return;
        }
      } finally {
        if (leaving.get(id) === entry) leaving.delete(id);
      }
      // A list read that started before the delete is dropped; its items leave the feeds.
      await queryClient.cancelQueries(list);
      void queryClient.invalidateQueries(list);
      void queryClient.invalidateQueries({ queryKey: [identity, 'feed'] });
      void queryClient.invalidateQueries({ queryKey: [identity, 'saved'] }); // its saved items leave the folder lists
    });
  }

  return () => {
    if (entry.sent || leaving.get(id) !== entry) return; // already sent, or left again since
    clearTimeout(entry.timer);
    leaving.delete(id);
    restore();
  };
}
