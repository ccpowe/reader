import * as SecureStore from 'expo-secure-store';
import type { SyncStorage } from './storage';

// SecureStore accepts only alphanumeric, dot, dash and underscore keys.
function nativeKey(key: string): string {
  return Array.from(key, char => char.codePointAt(0)!.toString(16)).join('-');
}

const storage: SyncStorage = {
  getItem: key => SecureStore.getItem(nativeKey(key)) || null,
  setItem: (key, value) => SecureStore.setItem(nativeKey(key), value),
  // Synchronous tombstone prevents a delayed async deletion erasing a new login.
  removeItem: key => SecureStore.setItem(nativeKey(key), ''),
};

export function secretStorage(): SyncStorage { return storage; }
