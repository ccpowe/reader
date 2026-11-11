/**
 * Where the reader left each list (old app: domain/listMemory.ts): the scroll offset and the first
 * visible item, so a list that was unmounted (another folder, the article page) comes back to the
 * same item even if items above it changed. The most recent `limit` lists are kept.
 * Pure: runs under `node --test`.
 */
export type ListPosition = { offset: number; anchorId: string | null };

export function createListMemory(limit = 12) {
  const positions = new Map<string, ListPosition>();
  return {
    get: (key: string) => positions.get(key) ?? null,
    remember(key: string, position: ListPosition) {
      positions.delete(key); // re-insert: most recent last
      positions.set(key, position);
      while (positions.size > limit) {
        const oldest = positions.keys().next().value;
        if (oldest === undefined) break;
        positions.delete(oldest);
      }
    },
    forget: (key: string) => positions.delete(key),
  };
}

export type ListMemory = ReturnType<typeof createListMemory>;

/**
 * After restoring by offset: what to do when `visible` (ids on screen) is known.
 * - the anchor is on screen, or there is none → done
 * - the anchor is further in the list → scroll to its index
 * - the anchor is gone (the item left the feed) → give up, keep where we are
 */
export function anchorStep(anchorId: string | null, visible: readonly string[], all: readonly string[]): { kind: 'done' } | { kind: 'scroll'; index: number } {
  if (!anchorId || visible.includes(anchorId)) return { kind: 'done' };
  const index = all.indexOf(anchorId);
  return index >= 0 ? { kind: 'scroll', index } : { kind: 'done' };
}
