import { queryOptions, useQuery } from '@tanstack/react-query';
import type { Session } from '../lib/readerAuth';
import { useEffect, useState } from 'react';
import { AppState } from 'react-native';

import { getSources, type SourceListItem } from '../lib/api';
import { readerQueryKeys } from '../state/queryClient';
import { useReaderRuntime } from '../lib/connection/react';
import type { ActiveReaderRuntime } from '../lib/connection';
import { sourceStatusRefreshInterval } from '../domain/source';

export function sourcesQueryOptions(session: Session, serverId?: string, runtime?: ActiveReaderRuntime | null) {
  return queryOptions<SourceListItem[], Error, SourceListItem[], ReturnType<typeof readerQueryKeys.sources>>({
    queryKey: readerQueryKeys.sources(session.user.id, serverId),
    queryFn: () => getSources(session, runtime ?? undefined),
  });
}

export function useSources(session: Session, active: boolean, refreshStatus = false) {
  const runtime = useReaderRuntime();
  const [foreground, setForeground] = useState(() => !AppState.currentState || AppState.currentState === 'active');
  useEffect(() => {
    if (!refreshStatus || !active) return;
    setForeground(!AppState.currentState || AppState.currentState === 'active');
    const subscription = AppState.addEventListener('change', (state) => setForeground(state === 'active'));
    return () => subscription.remove();
  }, [active, refreshStatus]);
  const query = useQuery({
    ...sourcesQueryOptions(session, runtime?.identity.server_id, runtime),
    enabled: active && (!refreshStatus || foreground),
    // Query owns the timer: equal responses must not stop status refreshes.
    // Only the subscriptions screen opts in; inactive screens do not poll.
    refetchInterval: active && refreshStatus && foreground && !!runtime
      ? (current) => current.state.error ? 30_000 : sourceStatusRefreshInterval(current.state.data ?? [])
      : false,
  });

  return {
    ...query,
    items: query.data ?? [],
    loading: query.isPending && query.fetchStatus !== 'idle' && !query.data,
    message: query.error ? query.error.message : '',
  };
}
