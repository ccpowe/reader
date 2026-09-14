import { i18n } from './index';
import {
  fallbackLanguage,
  normalizeLanguagePreference,
  resolveSystemLanguage,
  type AppLanguage,
  type AppLanguagePreference,
} from './registry';

export const languagePreferenceKey = 'reader.interface-language.v1';
type Storage = Pick<globalThis.Storage, 'getItem' | 'setItem'>;
type LanguageSnapshot = { language: AppLanguage; preference: AppLanguagePreference };

export function createAppLanguageStore({
  storage = () => globalThis.localStorage,
  applyLanguage = (language) => { void i18n.changeLanguage(language); },
}: {
  storage?: () => Storage | undefined;
  applyLanguage?: (language: AppLanguage) => void;
} = {}) {
  let snapshot: LanguageSnapshot = { language: fallbackLanguage, preference: 'system' };
  let readSystemLanguages: () => readonly string[] = () => [];
  let initialized = false;
  const listeners = new Set<() => void>();

  function refresh(preference = snapshot.preference) {
    let preferences: readonly string[] = [];
    try { preferences = readSystemLanguages(); } catch { /* Keep the app usable if OS lookup fails. */ }
    const language = preference === 'system'
      ? resolveSystemLanguage(preferences) as AppLanguage
      : preference;
    if (snapshot.language === language && snapshot.preference === preference) return;
    snapshot = { language, preference };
    applyLanguage(language);
    listeners.forEach((listener) => listener());
  }

  return {
    getSnapshot: () => snapshot,
    subscribe(listener: () => void) {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    initialize(reader: () => readonly string[]) {
      readSystemLanguages = reader;
      if (initialized) return;
      initialized = true;
      let preference: AppLanguagePreference = 'system';
      try { preference = normalizeLanguagePreference(storage()?.getItem(languagePreferenceKey)); } catch { /* Storage can be disabled. */ }
      refresh(preference);
    },
    refresh: () => refresh(),
    setPreference(value: AppLanguagePreference) {
      const preference = normalizeLanguagePreference(value);
      try { storage()?.setItem(languagePreferenceKey, preference); } catch { /* Retain the in-session choice. */ }
      refresh(preference);
    },
  };
}

export const appLanguageStore = createAppLanguageStore();
