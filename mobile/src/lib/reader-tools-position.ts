/**
 * Where the reader page's floating tools sit on the right edge (old app: drag them up or down; the
 * place is remembered on this device): 0 = top of the page area, 1 = bottom. Read once at start;
 * a read that arrives after a move does not undo the move.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

const KEY = 'reader.tools-position.v1';
const DEFAULT = 0.5;
let position = DEFAULT;
let moved = false;
const listeners = new Set<(position: number) => void>();

export function clampPosition(value: number): number {
  return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : DEFAULT;
}

void AsyncStorage.getItem(KEY).then(
  (stored) => {
    if (stored === null || moved) return;
    position = clampPosition(Number(stored));
    for (const listener of listeners) listener(position);
  },
  () => undefined, // unreadable: the default place
);

export const toolsPosition = {
  get: () => position,
  set(next: number) {
    moved = true;
    position = clampPosition(next);
    void AsyncStorage.setItem(KEY, String(position)).catch(() => undefined);
  },
  /** The remembered place, when it is read after the tools are already shown. */
  subscribe(listener: (position: number) => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
};
