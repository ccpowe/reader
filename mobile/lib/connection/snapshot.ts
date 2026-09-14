import type { ActiveReaderRuntime } from './types';

export type SnapshotListener = (runtime: ActiveReaderRuntime | null, generation: number) => void;

let activeRuntime: ActiveReaderRuntime | null = null;
let generation = 0;
let sessionEpoch = 0;
let sessionUserId: string | null = null;
const sessionListeners = new Set<() => void>();

export function getReaderSessionEpochSnapshot(): number {
  return sessionEpoch;
}

export function subscribeReaderSessionEpochSnapshot(listener: () => void): () => void {
  sessionListeners.add(listener);
  return () => sessionListeners.delete(listener);
}

/** Publish the account boundary synchronously, before React renders the new user. */
export function updateReaderSessionIdentity(runtime: ActiveReaderRuntime, userId: string | null): boolean {
  if (activeRuntime !== runtime || generation !== runtime.generation || sessionUserId === userId) return false;
  sessionUserId = userId;
  sessionEpoch += 1;
  for (const listener of [...sessionListeners]) listener();
  return true;
}
const listeners = new Set<SnapshotListener>();

export function getActiveRuntimeSnapshot(): ActiveReaderRuntime | null {
  return activeRuntime;
}

export function getConnectionGenerationSnapshot(): number {
  return generation;
}

/** Invalidate in-flight work without publishing a different runtime yet. */
export function invalidateConnectionGenerationSnapshot(): number {
  generation += 1;
  return generation;
}

export function subscribeConnectionSnapshot(listener: SnapshotListener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function commitRuntimeSnapshot(runtime: ActiveReaderRuntime | null, nextGeneration: number): void {
  if (activeRuntime !== runtime || generation !== nextGeneration) {
    sessionUserId = null;
    sessionEpoch += 1;
  }
  activeRuntime = runtime;
  generation = nextGeneration;
  for (const listener of [...sessionListeners]) listener();
  for (const listener of [...listeners]) listener(activeRuntime, generation);
}
