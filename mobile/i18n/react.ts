import { useSyncExternalStore } from 'react';
import { appLanguageStore } from './store';

export function useAppLanguage() {
  const snapshot = useSyncExternalStore(
    appLanguageStore.subscribe,
    appLanguageStore.getSnapshot,
    appLanguageStore.getSnapshot,
  );
  return { ...snapshot, setPreference: appLanguageStore.setPreference };
}
