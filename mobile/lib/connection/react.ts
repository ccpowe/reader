import { useSyncExternalStore } from 'react';

import { getActiveRuntime, getConnectionGeneration, subscribeConnection } from './runtime';
import type { ActiveReaderRuntime } from './types';

export function useReaderRuntime(): ActiveReaderRuntime | null {
  return useSyncExternalStore(
    (listener) => subscribeConnection(() => listener()),
    getActiveRuntime,
    getActiveRuntime,
  );
}

export function useReaderRuntimeGeneration(): number {
  return useSyncExternalStore(
    (listener) => subscribeConnection(() => listener()),
    getConnectionGeneration,
    getConnectionGeneration,
  );
}
