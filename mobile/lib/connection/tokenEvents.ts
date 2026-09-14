import type { ActiveReaderRuntime } from './types';

const listeners = new Set<(runtime: ActiveReaderRuntime) => void>();
const rejectedRuntimes = new WeakSet<ActiveReaderRuntime>();
export function subscribeServerTokenInvalid(listener: (runtime: ActiveReaderRuntime) => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function notifyServerTokenInvalid(runtime: ActiveReaderRuntime): void {
  if (rejectedRuntimes.has(runtime)) return;
  rejectedRuntimes.add(runtime);
  runtime.authClient?.auth.stopAutoRefresh();
  for (const listener of listeners) listener(runtime);
}
export function isServerTokenInvalid(runtime: ActiveReaderRuntime): boolean {
  return rejectedRuntimes.has(runtime);
}
