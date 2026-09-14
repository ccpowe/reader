const POSITION_KEY = 'reader:detail-tools:vertical-position:v1';
let rememberedPosition = 0.62;

export function clampToolPosition(value: number): number {
  return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 0.62;
}

export function readToolPosition(): number {
  try {
    const stored = globalThis.localStorage?.getItem(POSITION_KEY);
    if (stored !== null && stored !== undefined && stored.trim() !== '') {
      rememberedPosition = clampToolPosition(Number(stored));
    }
  } catch { /* The last in-memory position remains usable if storage is unavailable. */ }
  return rememberedPosition;
}

export function saveToolPosition(position: number): void {
  rememberedPosition = clampToolPosition(position);
  try { globalThis.localStorage?.setItem(POSITION_KEY, String(rememberedPosition)); } catch { /* Dragging remains available. */ }
}

export function draggedToolPosition(start: number, dy: number, travel: number): number {
  return travel > 0 ? clampToolPosition(start + dy / travel) : start;
}
