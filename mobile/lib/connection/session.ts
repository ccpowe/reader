import {
  commitRuntimeSnapshot,
  getActiveRuntimeSnapshot,
  getConnectionGenerationSnapshot,
} from './snapshot';
import {
  invalidateActiveSession,
  startRuntimeAutoRefresh,
} from './runtime';
import type { ActiveReaderRuntime } from './types';

export type LogoutReaderSessionOptions = {
  runtime: ActiveReaderRuntime;
  clearSession: () => void;
  stopAuthListener?: () => void;
  cancelQueries?: () => Promise<unknown>;
  clearCaches?: () => void;
};

/**
 * Invalidate the active auth namespace before sign-out, then clear all query
 * state in finally. The exact runtime is resumed only if no newer connection
 * operation has won while sign-out was pending.
 */
export async function logoutReaderSession(options: LogoutReaderSessionOptions): Promise<void> {
  options.clearSession();
  try { options.stopAuthListener?.(); } catch { /* cleanup is best effort */ }
  const invalidated = invalidateActiveSession(options.runtime);
  if (!invalidated) return;

  try {
    try {
      await options.cancelQueries?.();
    } catch {
      // Sign-out and cache cleanup must still run if cancellation rejects.
    }
    await options.runtime.authClient.auth.signOut();
  } finally {
    try { options.clearCaches?.(); } catch { /* cleanup is best effort */ }
    if (
      getActiveRuntimeSnapshot() === options.runtime &&
      getConnectionGenerationSnapshot() === invalidated.generation
    ) {
      const nextGeneration = getConnectionGenerationSnapshot() + 1;
      const resumedRuntime: ActiveReaderRuntime = {
        ...options.runtime,
        generation: nextGeneration,
      };
      commitRuntimeSnapshot(resumedRuntime, nextGeneration);
      startRuntimeAutoRefresh(resumedRuntime);
    }
  }
}
