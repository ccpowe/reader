import { type ReactNode, useEffect, useState } from 'react';
import { AppState, Platform } from 'react-native';
import { getLocales } from 'expo-localization';
import { appLanguageStore } from './store';
import { useAppLanguage } from './react';

function readDeviceLanguages() {
  return getLocales().map((locale) => locale.languageTag);
}

/** Resolve language before descendants render; refresh without remounting them. */
export function AppLanguageProvider({ children }: { children: ReactNode }) {
  useState(() => {
    appLanguageStore.initialize(readDeviceLanguages);
    return true;
  });
  const { language } = useAppLanguage();
  useEffect(() => {
    if (Platform.OS === 'web' && typeof document !== 'undefined') {
      document.documentElement.lang = language;
    }
  }, [language]);
  useEffect(() => {
    const refresh = () => appLanguageStore.refresh();
    const subscription = AppState.addEventListener('change', (state) => {
      if (state === 'active') refresh();
    });
    const browser = Platform.OS === 'web' && typeof window !== 'undefined' ? window : null;
    browser?.addEventListener('languagechange', refresh);
    // Cover a preference change between initial rendering and subscription.
    refresh();
    return () => {
      subscription.remove();
      browser?.removeEventListener('languagechange', refresh);
    };
  }, []);
  return children;
}
