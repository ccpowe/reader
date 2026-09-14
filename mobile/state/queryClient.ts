import { QueryClient } from '@tanstack/react-query';

import { getActiveRuntime } from '../lib/connection/runtime';

/** Shared cache for user-scoped Reader data. Never put a session token in a key. */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 60_000,
      gcTime: 15 * 60_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

export function readerServerNamespace(explicitServerId?: string): string {
  return explicitServerId ?? getActiveRuntime()?.identity.server_id ?? 'unconfigured';
}

export const readerQueryKeys = {
  feedPrefix: (userId: string, serverId?: string) => ['reader', readerServerNamespace(serverId), userId, 'feed'] as const,
  feed: (userId: string, scopeKey: string, translationLocale = 'zh-CN', serverId?: string) => ['reader', readerServerNamespace(serverId), userId, 'feed', scopeKey, translationLocale] as const,
  savedPrefix: (userId: string, serverId?: string) => ['reader', readerServerNamespace(serverId), userId, 'saved'] as const,
  saved: (userId: string, translationLocale = 'zh-CN', query = '', serverId?: string) => ['reader', readerServerNamespace(serverId), userId, 'saved', translationLocale, query.trim()] as const,
  sources: (userId: string, serverId?: string) => ['reader', readerServerNamespace(serverId), userId, 'sources'] as const,
  profile: (userId: string, serverId?: string) => ['reader', readerServerNamespace(serverId), userId, 'profile'] as const,
  translationPreference: (userId: string, serverId?: string) => ['reader', readerServerNamespace(serverId), userId, 'translation-preference'] as const,
  titleTranslations: (
    userId: string,
    translationLocale: string,
    engineId: string | null,
    contentIds: string[],
    serverId?: string,
  ) => ['reader', readerServerNamespace(serverId), userId, 'title-translations', translationLocale, engineId, contentIds] as const,
  rankingsPrefix: (userId: string, serverId?: string) => ['reader', readerServerNamespace(serverId), userId, 'rankings'] as const,
  rankings: (userId: string, key: string, serverId?: string) => ['reader', readerServerNamespace(serverId), userId, 'rankings', key] as const,
};
