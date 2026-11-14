/**
 * Session storage on the phone: one JSON entry in the platform keystore (expo-secure-store), since
 * it holds the server token and the refresh token.
 */
import * as SecureStore from 'expo-secure-store';

import type { Persisted } from './session';
import type { SessionStorage } from './session-store';

const KEY = 'reader.session.v1';

export const sessionStorage: SessionStorage = {
  async load() {
    const raw = await SecureStore.getItemAsync(KEY);
    return raw ? (JSON.parse(raw) as Persisted) : null;
  },
  async save(persisted) {
    if (!persisted.server) await SecureStore.deleteItemAsync(KEY);
    else await SecureStore.setItemAsync(KEY, JSON.stringify(persisted));
  },
};
