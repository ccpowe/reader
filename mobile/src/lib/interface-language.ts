/**
 * The interface language chosen on this device (我的 → 界面语言): 跟随系统, 简体中文 or English.
 * Kept in AsyncStorage and read once at start; it sets the language in force (lib/i18n), which
 * routes follow with useLanguage() so a change shows at once.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { getLocales } from 'expo-localization';
import { useSyncExternalStore } from 'react';
import { AppState } from 'react-native';

import { setLanguage, systemLanguage, type Language } from './i18n';
import { INTERFACE_LANGUAGES, type InterfaceLanguage } from './me';

const KEY = 'reader.interface-language.v1';
let value: InterfaceLanguage = 'system';
let resolved: Language = 'zh';
const listeners = new Set<() => void>();

function resolve() {
  if (value !== 'system') resolved = value;
  else {
    try {
      resolved = systemLanguage(getLocales().map((locale) => locale.languageTag));
    } catch {
      resolved = 'zh';
    }
  }
  setLanguage(resolved);
}

function apply(next: InterfaceLanguage) {
  value = next;
  resolve();
  listeners.forEach((listener) => listener());
}

resolve();
void AsyncStorage.getItem(KEY).then(
  (stored) => {
    if (stored && stored !== value && INTERFACE_LANGUAGES.some((l) => l.key === stored)) apply(stored as InterfaceLanguage);
  },
  () => undefined, // unreadable: follow the system
);

// 跟随系统: the system language may have changed while Reader was in the background.
AppState.addEventListener('change', (status) => {
  if (status !== 'active' || value !== 'system') return;
  const before = resolved;
  resolve();
  if (resolved !== before) listeners.forEach((listener) => listener());
});

const subscribe = (listener: () => void) => {
  listeners.add(listener);
  return () => void listeners.delete(listener);
};

export const interfaceLanguage = {
  get: () => value,
  subscribe,
  set(next: InterfaceLanguage) {
    apply(next);
    void AsyncStorage.setItem(KEY, next).catch(() => undefined);
  },
};

/** The device's choice (跟随系统 / 简体中文 / English). */
export function useInterfaceLanguage(): InterfaceLanguage {
  return useSyncExternalStore(subscribe, interfaceLanguage.get, interfaceLanguage.get);
}

/** The language in force; a component that calls it re-renders when it changes. */
export function useLanguage(): Language {
  return useSyncExternalStore(
    subscribe,
    () => resolved,
    () => resolved,
  );
}
