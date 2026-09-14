import type { SyncStorage } from './storage';

const values = new Map<string, string>();
const memory: SyncStorage = {
  getItem: key => values.get(key) ?? null,
  setItem: (key, value) => { values.set(key, value); },
  removeItem: key => { values.delete(key); },
};

/** Web credentials last for this tab only; never use public localStorage. */
export function secretStorage(): SyncStorage {
  try {
    if (typeof window !== 'undefined' && window.sessionStorage) return window.sessionStorage;
  } catch { /* Private browsing can disable browser storage. */ }
  return memory;
}
