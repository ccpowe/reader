import { localizedMessage } from '../../i18n/message';
import type { Session, ReaderAuthClient } from '../readerAuth';

import { ConnectionError, redactUrl } from './errors';
import { discoverReaderServer } from './discovery';
import {
  createNamespacedStorage,
  readServerToken,
  writeServerToken,
  writePersistedConnection,
  type SyncStorage,
} from './storage';
import { normalizeReaderBaseUrl, resolveReaderUrl } from './url';
import {
  commitRuntimeSnapshot,
  getActiveRuntimeSnapshot,
  getConnectionGenerationSnapshot,
  invalidateConnectionGenerationSnapshot,
  subscribeConnectionSnapshot,
} from './snapshot';
import type {
  ActiveReaderRuntime,
  ReaderBaseUrl,
  ReaderDiscovery,
  ReaderDiscoveryResult,
  ReaderConnectionIdentity,
  PersistedConnection,
} from './types';
import { captureRuntimeContext, type ReaderRuntimeContext } from './guard';

export type ConnectionListener = (runtime: ActiveReaderRuntime | null, generation: number) => void;

export type RuntimeActivationOptions = {
  serverToken?: string;
  confirmedRedirect?: boolean;
  /** Expected persisted identity used by cold-start restoration. */
  expectedIdentity?: ReaderConnectionIdentity;
  /** Required only when expectedIdentity differs from discovery identity. */
  confirmedIdentity?: boolean;
  signal?: AbortSignal;
  backingStorage?: SyncStorage;
  clientFactory?: (
    discovery: ReaderDiscovery,
    identity: ReaderConnectionIdentity,
    storage?: SyncStorage,
    clientOptions?: { autoRefreshToken: boolean },
  ) => ReaderAuthClient;
};

let activationSerial = 0;

export function getActiveRuntime(): ActiveReaderRuntime | null {
  return getActiveRuntimeSnapshot();
}

export function getConnectionGeneration(): number {
  return getConnectionGenerationSnapshot();
}

export function subscribeConnection(listener: ConnectionListener): () => void {
  return subscribeConnectionSnapshot(listener);
}

function runtimeIdentity(discovery: ReaderDiscovery): ReaderConnectionIdentity {
  return {
    server_id: discovery.server_id,
    api_base_url: discovery.api_base_url,
  };
}

function normalizeIdentity(identity: ReaderConnectionIdentity): ReaderConnectionIdentity {
  return {
    server_id: identity.server_id.trim(),
    api_base_url: normalizeReaderBaseUrl(identity.api_base_url),
  };
}

/** Compare only the persisted, non-secret server identity coordinates. */
export function readerIdentitiesMatch(
  left: ReaderConnectionIdentity,
  right: ReaderConnectionIdentity,
): boolean {
  const normalizedLeft = normalizeIdentity(left);
  const normalizedRight = normalizeIdentity(right);
  return normalizedLeft.server_id === normalizedRight.server_id &&
    normalizedLeft.api_base_url === normalizedRight.api_base_url;
}

export type ReaderIdentityConfirmation = {
  previous_server_id: string;
  previous_api_base_url: string;
  next_server_id: string;
  next_api_base_url: string;
};

/** Safe, non-secret metadata for the cold-start identity confirmation UI. */
export function identityConfirmationFor(
  expected: ReaderConnectionIdentity,
  discovered: ReaderConnectionIdentity,
): ReaderIdentityConfirmation {
  return {
    previous_server_id: expected.server_id.trim(),
    previous_api_base_url: redactUrl(expected.api_base_url),
    next_server_id: discovered.server_id.trim(),
    next_api_base_url: redactUrl(discovered.api_base_url),
  };
}

function assertNotAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw new ConnectionError('discovery_cancelled', localizedMessage('errors:connectionCancelled'));
}

export function stopRuntimeAutoRefresh(runtime: ActiveReaderRuntime | null): void {
  const auth = runtime?.authClient.auth as ReaderAuthClient['auth'] & { stopAutoRefresh?: () => void | Promise<void> } | undefined;
  try {
    const result = auth?.stopAutoRefresh?.();
    if (result && typeof (result as Promise<void>).catch === 'function') void (result as Promise<void>).catch(() => undefined);
  } catch { /* cleanup is best effort */ }
  try {
    const result = runtime?.lifecycle?.stopAutoRefresh?.();
    if (result && typeof (result as Promise<void>).catch === 'function') void (result as Promise<void>).catch(() => undefined);
  } catch { /* cleanup is best effort */ }
  try { runtime?.lifecycle?.unsubscribe?.(); } catch { /* cleanup is best effort */ }
}

/** Start refresh only after a runtime has been atomically published. */
export function startRuntimeAutoRefresh(runtime: ActiveReaderRuntime | null): void {
  const auth = runtime?.authClient.auth as ReaderAuthClient['auth'] & { startAutoRefresh?: () => void | Promise<void> } | undefined;
  try {
    const result = auth?.startAutoRefresh?.();
    if (result && typeof (result as Promise<void>).catch === 'function') void (result as Promise<void>).catch(() => undefined);
  } catch { /* lifecycle startup is best effort */ }
  try {
    const result = runtime?.lifecycle?.startAutoRefresh?.();
    if (result && typeof (result as Promise<void>).catch === 'function') void (result as Promise<void>).catch(() => undefined);
  } catch { /* lifecycle startup is best effort */ }
}

/** Construct an isolated client without changing the active runtime. */
export function createReaderRuntime(
  discovery: ReaderDiscovery,
  options: Pick<RuntimeActivationOptions, 'backingStorage' | 'clientFactory' | 'serverToken'> = {},
  baseUrl: ReaderBaseUrl = discovery.api_base_url as ReaderBaseUrl,
): ActiveReaderRuntime {
  const identity = runtimeIdentity(discovery);
  const storage = createNamespacedStorage(identity, options.backingStorage);
  const serverToken = options.serverToken ?? readServerToken(baseUrl);
  if (!serverToken && !options.clientFactory) throw new ConnectionError('not_configured', localizedMessage('errors:serverTokenRequired'));
  const client = options.clientFactory
    ? options.clientFactory(discovery, identity, storage, { autoRefreshToken: false })
    : (() => {
        // The platform-specific storage and transport are resolved by Metro.
        // eslint-disable-next-line @typescript-eslint/no-require-imports
        const { createReaderAuthClient } = require('../readerAuth') as typeof import('../readerAuth');
        return createReaderAuthClient(discovery.api_base_url, serverToken ?? '', storage);
      })();
  return {
    api_base_url: discovery.api_base_url,
    base_url: baseUrl,
    discovery,
    generation: getConnectionGenerationSnapshot() + 1,
    identity,
    authClient: client,
    serverToken: serverToken ?? '',
  };
}

/**
 * Restore the previously trusted public discovery coordinates immediately on
 * cold start.  The caller revalidates them in the background; this function
 * never accepts a new server identity or persists a replacement on its own.
 */
export async function restorePersistedReaderRuntime(
  persisted: PersistedConnection,
  options: Pick<RuntimeActivationOptions, 'backingStorage' | 'clientFactory' | 'serverToken' | 'signal'> = {},
): Promise<{ runtime: ActiveReaderRuntime; session: Session | null }> {
  assertNotAborted(options.signal);
  if (!persisted.discovery || !readerIdentitiesMatch(persisted.identity, runtimeIdentity(persisted.discovery))) {
    throw new ConnectionError('not_configured', localizedMessage('errors:noSavedServer'));
  }
  const serial = ++activationSerial;
  let candidate: ActiveReaderRuntime;
  try {
    candidate = createReaderRuntime(persisted.discovery, options, persisted.base_url);
  } catch (error) {
    throw new ConnectionError('activation_failed', localizedMessage('errors:restoreConnectionFailed'), { cause: error });
  }
  try {
    const result = await candidate.authClient.auth.getSession();
    assertNotAborted(options.signal);
    if (result.error) {
      throw new ConnectionError('activation_failed', localizedMessage('errors:restoreSessionFailed'), { cause: result.error });
    }
    if (serial !== activationSerial) {
      throw new ConnectionError('stale_runtime', localizedMessage('errors:serverRestoreExpired'));
    }
    const nextGeneration = getConnectionGenerationSnapshot() + 1;
    const nextRuntime: ActiveReaderRuntime = { ...candidate, generation: nextGeneration };
    stopRuntimeAutoRefresh(getActiveRuntimeSnapshot());
    commitRuntimeSnapshot(nextRuntime, nextGeneration);
    startRuntimeAutoRefresh(nextRuntime);
    return { runtime: nextRuntime, session: result.data.session };
  } catch (error) {
    stopRuntimeAutoRefresh(candidate);
    if (error instanceof ConnectionError) throw error;
    throw new ConnectionError('activation_failed', localizedMessage('errors:restoreSessionFailed'), { cause: error });
  }
}

/**
 * Atomically activate a discovered server after its candidate session has
 * been checked.  The old runtime is untouched if candidate creation or
 * session restoration fails.
 */
export async function activateReaderServer(
  discovery: ReaderDiscoveryResult,
  options: RuntimeActivationOptions = {},
): Promise<{ runtime: ActiveReaderRuntime; session: Session | null }> {
  assertNotAborted(options.signal);
  if (discovery.requires_confirmation && !options.confirmedRedirect) {
    throw new ConnectionError(
      'redirect_confirmation_required',
      localizedMessage('errors:redirectConfirmationRequired'),
      { responseUrl: discovery.response_url },
    );
  }
  const discoveredIdentity = runtimeIdentity(discovery);
  if (
    options.expectedIdentity &&
    !readerIdentitiesMatch(options.expectedIdentity, discoveredIdentity) &&
    !options.confirmedIdentity
  ) {
    throw new ConnectionError(
      'identity_confirmation_required',
      localizedMessage('errors:identityConfirmationRequired'),
    );
  }
  const serial = ++activationSerial;
  let candidate: ActiveReaderRuntime;
  try {
    candidate = createReaderRuntime(discovery, options, discovery.base_url);
  } catch (error) {
    throw new ConnectionError('activation_failed', localizedMessage('errors:createConnectionFailed'), { cause: error });
  }
  let session: Session | null = null;
  try {
    const result = await candidate.authClient.auth.getSession();
    assertNotAborted(options.signal);
    if (result.error) {
      throw new ConnectionError('activation_failed', localizedMessage('errors:restoreSessionFailed'), { cause: result.error });
    }
    session = result.data.session;
  } catch (error) {
    stopRuntimeAutoRefresh(candidate);
    if (error instanceof ConnectionError) throw error;
    throw new ConnectionError('activation_failed', localizedMessage('errors:connectThisServerFailed'), { cause: error });
  }
  if (serial !== activationSerial) {
    stopRuntimeAutoRefresh(candidate);
    throw new ConnectionError('stale_runtime', localizedMessage('errors:serverSwitchExpired'));
  }
  const nextGeneration = getConnectionGenerationSnapshot() + 1;
  const nextRuntime: ActiveReaderRuntime = { ...candidate, generation: nextGeneration };
  try {
    // Persist the reconnect coordinates before publishing the new runtime.
    // If storage is unavailable, the old runtime remains active and callers
    // can retry without having exposed an un-restorable candidate globally.
    writeServerToken(discovery.base_url, nextRuntime.serverToken);
    writePersistedConnection({
      base_url: discovery.base_url,
      discovery: nextRuntime.discovery,
      identity: nextRuntime.identity,
    }, options.backingStorage);
  } catch (error) {
    stopRuntimeAutoRefresh(candidate);
    throw new ConnectionError('activation_failed', localizedMessage('errors:saveConnectionFailed'), { cause: error });
  }
  stopRuntimeAutoRefresh(getActiveRuntimeSnapshot());
  commitRuntimeSnapshot(nextRuntime, nextGeneration);
  startRuntimeAutoRefresh(nextRuntime);
  return { runtime: nextRuntime, session };
}

/** Discover and activate in one operation for callers without a form UI. */
export async function connectReaderServer(
  input: string,
  options: RuntimeActivationOptions & { discover?: typeof discoverReaderServer } = {},
): Promise<{ runtime: ActiveReaderRuntime; session: Session | null; discovery: ReaderDiscoveryResult }> {
  const discovery = await (options.discover ?? discoverReaderServer)(input, { signal: options.signal, serverToken: options.serverToken });
  const activated = await activateReaderServer(discovery, options);
  return { ...activated, discovery };
}

export function disconnectReaderServer(): void {
  const currentRuntime = getActiveRuntimeSnapshot();
  if (!currentRuntime) return;
  activationSerial += 1;
  stopRuntimeAutoRefresh(currentRuntime);
  const nextGeneration = invalidateConnectionGenerationSnapshot();
  commitRuntimeSnapshot(null, nextGeneration);
}

/** Invalidate auth and request callbacks for the exact active runtime. */
export function invalidateActiveSession(
  runtime: ActiveReaderRuntime,
): ReaderRuntimeContext | null {
  const context = captureRuntimeContext(runtime);
  if (!context) return null;
  activationSerial += 1;
  stopRuntimeAutoRefresh(runtime);
  const generation = invalidateConnectionGenerationSnapshot();
  return { ...context, generation };
}

export function readerApiUrl(route: string, runtime = getActiveRuntimeSnapshot()): string {
  if (!runtime) throw new ConnectionError('not_configured', localizedMessage('errors:connectServerFirst'));
  return resolveReaderUrl(runtime.api_base_url, route);
}
