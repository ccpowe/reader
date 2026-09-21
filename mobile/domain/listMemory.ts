export type ListPosition = {
  firstVisibleId: string | null;
  offset: number;
};

export type ListPositionRegistry = Map<string, ListPosition>;

export function rememberListPosition(
  registry: ListPositionRegistry,
  key: string,
  position: ListPosition,
  limit = 12,
): void {
  registry.delete(key);
  registry.set(key, position);
  while (registry.size > limit) {
    const oldest = registry.keys().next().value as string | undefined;
    if (oldest === undefined) break;
    registry.delete(oldest);
  }
}

export function clearMissingListPosition(
  registry: ListPositionRegistry,
  key: string,
  itemIds: readonly string[],
): ListPosition | null {
  const position = registry.get(key) ?? null;
  if (!position?.firstVisibleId || itemIds.includes(position.firstVisibleId)) return position;
  registry.delete(key);
  return null;
}
