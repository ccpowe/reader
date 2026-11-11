/**
 * The app's single session store and query client, and the hooks pages use to read them.
 * Created once per app process; Storybook stories that need data make their own.
 */
import { focusManager, onlineManager } from '@tanstack/react-query';
import { useSyncExternalStore } from 'react';
import * as Network from 'expo-network';
import { AppState } from 'react-native';

import { sessionStorage } from './connection/storage';
import { identityOf, type SessionState } from './connection/session';
import { createSessionStore } from './connection/session-store';
import { createQueryClient } from './query';

export const queryClient = createQueryClient();

export const session = createSessionStore({
  storage: sessionStorage,
  // An ended identity's cache goes with it (react-guidelines §4.7): cancel first, then remove.
  onIdentityEnded(identity) {
    void queryClient.cancelQueries({ queryKey: [identity] }).then(() => queryClient.removeQueries({ queryKey: [identity] }));
  },
});

export function useSession(): SessionState {
  return useSyncExternalStore(session.subscribe, session.getSnapshot, session.getSnapshot);
}

/** The signed-in identity for query keys; routes under (tabs) are only mounted when signed in. */
export function useIdentity(): string {
  const identity = identityOf(useSession());
  if (!identity) throw new Error('useIdentity outside a signed-in route');
  return identity;
}

/**
 * Connect the app's lifecycle to Query once (react-guidelines §4.9): refetch on return to the
 * foreground, pause while the phone has no network and refetch when it comes back. "Online" means
 * connected to a network, not that the internet is reachable: a Reader server on the LAN counts.
 * Returns the cleanup for useMountEffect.
 */
export function connectAppLifecycle(): () => void {
  const appState = AppState.addEventListener('change', (status) => focusManager.setFocused(status === 'active'));
  const online = (state: Network.NetworkState) => onlineManager.setOnline(state.isConnected !== false);
  const network = Network.addNetworkStateListener(online);
  void Network.getNetworkStateAsync().then(online, () => undefined);
  return () => {
    appState.remove();
    network.remove();
  };
}
