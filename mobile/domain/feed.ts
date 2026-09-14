export type FeedScope =
  | { kind: 'all' }
  | { folderName: string; kind: 'folder' }
  | { kind: 'source'; sourceId: string };

export const ALL_FEED_SCOPE: FeedScope = { kind: 'all' };

export function folderFeedScope(folderName: string | null): FeedScope {
  return folderName === null ? ALL_FEED_SCOPE : { folderName, kind: 'folder' };
}

export function sourceFeedScope(sourceId: string): FeedScope {
  return { kind: 'source', sourceId };
}

export function feedScopeCacheKey(scope: FeedScope): string {
  if (scope.kind === 'folder') return `folder:${scope.folderName}`;
  if (scope.kind === 'source') return `source:${scope.sourceId}`;
  return 'all';
}

export function feedScopeRequest(scope: FeedScope): {
  folderName?: string;
  sourceId?: string;
} {
  if (scope.kind === 'folder') return { folderName: scope.folderName };
  if (scope.kind === 'source') return { sourceId: scope.sourceId };
  return {};
}
