/**
 * Session storage on the web (Storybook and web preview only; the product is the Android app):
 * the browser has no keystore, so this is plain localStorage.
 */
import type { Persisted } from './session';
import type { SessionStorage } from './session-store';

const KEY = 'reader.session.v1';

export const sessionStorage: SessionStorage = {
  async load() {
    const raw = globalThis.localStorage?.getItem(KEY);
    return raw ? (JSON.parse(raw) as Persisted) : null;
  },
  async save(persisted) {
    if (!persisted.server) globalThis.localStorage?.removeItem(KEY);
    else globalThis.localStorage?.setItem(KEY, JSON.stringify(persisted));
  },
};
