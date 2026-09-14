import type { QueryClient } from '@tanstack/react-query';

// One in-flight write per account/item, shared by Today and Saved. Repeated
// taps while a write settles are ignored, including taps from another surface.
const pending = new WeakMap<QueryClient, Set<string>>();

export function beginSavedMutation(client: QueryClient, userId: string, serverId: string, contentId: string): (() => void) | null {
  let keys = pending.get(client);
  if (!keys) {
    keys = new Set();
    pending.set(client, keys);
  }
  const key = JSON.stringify([serverId, userId, contentId]);
  if (keys.has(key)) return null;
  keys.add(key);
  return () => { keys.delete(key); };
}
