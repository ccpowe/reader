import type { QueryClient } from '@tanstack/react-query';

import { isRuntimeContextCurrent, type ReaderRuntimeContext } from '../lib/connection/guard';
import { readerQueryKeys } from './queryClient';

async function invalidateCurrent(
  queryClient: QueryClient,
  filters: readonly { queryKey: readonly unknown[] }[],
  context?: ReaderRuntimeContext | null,
): Promise<void> {
  for (const filter of filters) {
    if (context && !isRuntimeContextCurrent(context)) return;
    await queryClient.invalidateQueries(filter);
  }
}

export async function invalidateAfterSourceMutation(queryClient: QueryClient, userId: string, serverId: string, context?: ReaderRuntimeContext | null): Promise<void> {
  await invalidateCurrent(queryClient, [
    { queryKey: readerQueryKeys.sources(userId, serverId) },
    { queryKey: readerQueryKeys.feedPrefix(userId, serverId) },
    { queryKey: readerQueryKeys.rankingsPrefix(userId, serverId) },
  ], context);
}

export async function invalidateAfterSavedMutation(queryClient: QueryClient, userId: string, serverId: string, context?: ReaderRuntimeContext | null): Promise<void> {
  await invalidateCurrent(queryClient, [
    { queryKey: readerQueryKeys.savedPrefix(userId, serverId) },
    { queryKey: readerQueryKeys.feedPrefix(userId, serverId) },
  ], context);
}

export async function invalidateAfterTranslationPreferenceMutation(queryClient: QueryClient, userId: string, serverId: string, context?: ReaderRuntimeContext | null): Promise<void> {
  await invalidateCurrent(queryClient, [
    { queryKey: readerQueryKeys.translationPreference(userId, serverId) },
    { queryKey: readerQueryKeys.feedPrefix(userId, serverId) },
    { queryKey: readerQueryKeys.savedPrefix(userId, serverId) },
    { queryKey: readerQueryKeys.rankingsPrefix(userId, serverId) },
  ], context);
}
