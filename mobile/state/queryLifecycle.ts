import type { InfiniteData, QueryClient, QueryKey } from '@tanstack/react-query';

type InfiniteItemsPage<T> = {
  items: T[];
  next_cursor: string | null;
};

function sameQueryKey(left: QueryKey, right: QueryKey): boolean {
  return JSON.stringify(left) === JSON.stringify(right);
}

export function trimInactiveInfiniteQuery<T>({
  anchorId,
  getItemId,
  queryClient,
  queryKey,
}: {
  anchorId: string | null;
  getItemId: (item: T) => string;
  queryClient: QueryClient;
  queryKey: QueryKey;
}): boolean {
  if (!anchorId) return false;
  const query = queryClient.getQueryCache().find({ exact: true, queryKey });
  if (!query || query.getObserversCount() > 0 || query.state.fetchStatus !== 'idle') return false;
  const data = query.state.data as InfiniteData<InfiniteItemsPage<T>> | undefined;
  if (!data || data.pages.length < 3) return false;
  const anchorPage = data.pages.findIndex((page) => page.items.some((item) => getItemId(item) === anchorId));
  if (anchorPage < 0) return false;
  const retainedPageCount = Math.min(data.pages.length, anchorPage + 2);
  if (retainedPageCount === data.pages.length) return false;
  queryClient.setQueryData<InfiniteData<InfiniteItemsPage<T>>>(queryKey, {
    pages: data.pages.slice(0, retainedPageCount),
    pageParams: data.pageParams.slice(0, retainedPageCount),
  }, { updatedAt: query.state.dataUpdatedAt });
  return true;
}

export function trimInactiveInfiniteQueryWhenIdle<T>(options: {
  anchorId: string | null;
  getItemId: (item: T) => string;
  queryClient: QueryClient;
  queryKey: QueryKey;
}): void {
  if (trimInactiveInfiniteQuery(options)) return;
  const initial = options.queryClient.getQueryCache().find({ exact: true, queryKey: options.queryKey });
  if (!initial || initial.getObserversCount() > 0 || initial.state.fetchStatus === 'idle') return;
  const unsubscribe = options.queryClient.getQueryCache().subscribe(() => {
    const current = options.queryClient.getQueryCache().find({ exact: true, queryKey: options.queryKey });
    if (!current) {
      unsubscribe();
      return;
    }
    if (current.getObserversCount() > 0) {
      unsubscribe();
      return;
    }
    if (current.state.fetchStatus !== 'idle') return;
    trimInactiveInfiniteQuery(options);
    unsubscribe();
  });
}

export async function removeObsoleteQueries({
  isProtected,
  prefix,
  queryClient,
}: {
  isProtected: (queryKey: QueryKey) => boolean;
  prefix: QueryKey;
  queryClient: QueryClient;
}): Promise<void> {
  const candidates = queryClient.getQueryCache().findAll({ queryKey: prefix })
    .filter((query) => query.getObserversCount() === 0 && !isProtected(query.queryKey));
  await Promise.all(candidates
    .filter((query) => query.state.fetchStatus !== 'idle')
    .map((query) => queryClient.cancelQueries({ exact: true, queryKey: query.queryKey })));
  for (const candidate of candidates) {
    const current = queryClient.getQueryCache().find({ exact: true, queryKey: candidate.queryKey });
    if (
      !current ||
      current.getObserversCount() > 0 ||
      current.state.fetchStatus !== 'idle' ||
      isProtected(current.queryKey)
    ) continue;
    queryClient.removeQueries({ exact: true, queryKey: current.queryKey });
  }
}

export function protectsQueryKeys(keys: readonly QueryKey[]): (queryKey: QueryKey) => boolean {
  return (queryKey) => keys.some((key) => sameQueryKey(key, queryKey));
}
