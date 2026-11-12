/**
 * The interface language chosen on this device (我的 → 界面语言): 跟随系统, 简体中文 or English.
 * Kept in AsyncStorage; read once at start. Until the interface texts are translated (block 7b),
 * choosing English is remembered but the interface stays Chinese.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { useSyncExternalStore } from 'react';

import { INTERFACE_LANGUAGES, type InterfaceLanguage } from './me';

const KEY = 'reader.interface-language.v1';
let value: InterfaceLanguage = 'system';
const listeners = new Set<() => void>();
const notify = () => listeners.forEach((listener) => listener());

void AsyncStorage.getItem(KEY).then(
  (stored) => {
    if (stored && INTERFACE_LANGUAGES.some((l) => l.key === stored)) {
      value = stored as InterfaceLanguage;
      notify();
    }
  },
  () => undefined, // unreadable: follow the system
);

export const interfaceLanguage = {
  get: () => value,
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => void listeners.delete(listener);
  },
  set(next: InterfaceLanguage) {
    value = next;
    notify();
    void AsyncStorage.setItem(KEY, next).catch(() => undefined);
  },
};

export function useInterfaceLanguage(): InterfaceLanguage {
  return useSyncExternalStore(interfaceLanguage.subscribe, interfaceLanguage.get, interfaceLanguage.get);
}
