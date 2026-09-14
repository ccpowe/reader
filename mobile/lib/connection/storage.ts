import { secretStorage } from './secretStorage';
import type { PersistedConnection, ReaderConnectionIdentity, ReaderDiscovery } from './types';
import { normalizeReaderBaseUrl } from './url';

type SyncStorage = {
  getItem(key: string): string | null;
  removeItem(key: string): void;
  setItem(key: string, value: string): void;
};

const PERSISTED_CONNECTION_KEY = 'reader.connection.v2';
const memoryValues = new Map<string, string>();

const memoryStorage: SyncStorage = {
  getItem: (key) => memoryValues.get(key) ?? null,
  removeItem: (key) => { memoryValues.delete(key); },
  setItem: (key, value) => { memoryValues.set(key, value); },
};

function defaultStorage(): SyncStorage {
  try {
    const candidate = globalThis.localStorage;
    if (
      candidate &&
      typeof candidate.getItem === 'function' &&
      typeof candidate.setItem === 'function' &&
      typeof candidate.removeItem === 'function'
    ) {
      return candidate;
    }
  } catch {
    // Browser privacy mode or native storage bootstrap may throw. The in
    // memory fallback keeps discovery usable for the current process.
  }
  return memoryStorage;
}

function encodeNamespacePart(value: string): string {
  // Length-prefix the original value instead of replacing escaped characters.
  // The latter is lossy (`%` and `_25` can collapse to the same key), while a
  // length-prefixed pair remains unambiguous even when values contain the
  // separators used by the surrounding storage key.
  return `${value.length}:${value}`;
}

function readCachedDiscovery(value: unknown, identity: ReaderConnectionIdentity): ReaderDiscovery | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const discovery = value as Record<string, unknown>;
  try {
    if (discovery.protocol_version !== 2 || discovery.server_id !== identity.server_id ||
        typeof discovery.api_base_url !== 'string' || normalizeReaderBaseUrl(discovery.api_base_url) !== identity.api_base_url) return undefined;
    return { protocol_version: 2, server_id: identity.server_id, api_base_url: identity.api_base_url };
  } catch { return undefined; }
}

/** Private credentials are isolated by server identity and the API base URL. */
export function createNamespacedStorage(
  identity: ReaderConnectionIdentity,
  backingStorage: SyncStorage = secretStorage(),
): SyncStorage {
  const serverId = identity.server_id.trim();
  const apiBaseUrl = normalizeReaderBaseUrl(identity.api_base_url);
  const prefix = `reader.session.v3.${encodeNamespacePart(serverId)}.${encodeNamespacePart(apiBaseUrl)}.`;
  return {
    getItem: (key) => backingStorage.getItem(`${prefix}${key}`),
    removeItem: (key) => backingStorage.removeItem(`${prefix}${key}`),
    setItem: (key, value) => backingStorage.setItem(`${prefix}${key}`, value),
  };
}

export function readPersistedConnection(
  backingStorage: SyncStorage = defaultStorage(),
): PersistedConnection | null {
  let raw: string | null;
  try {
    raw = backingStorage.getItem(PERSISTED_CONNECTION_KEY);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (!parsed || typeof parsed !== 'object') return null;
    if (!parsed.base_url || typeof parsed.base_url !== 'string') return null;
    if (!parsed.identity || typeof parsed.identity !== 'object') return null;
    const identity = parsed.identity as Record<string, unknown>;
    if (
      typeof identity.server_id !== 'string' ||
      !identity.server_id.trim() ||
      identity.server_id.length > 128 ||
      typeof identity.api_base_url !== 'string'
    ) return null;
    const normalizedIdentity = {
      server_id: identity.server_id.trim(),
      api_base_url: normalizeReaderBaseUrl(identity.api_base_url),
    };
    return {
      base_url: normalizeReaderBaseUrl(parsed.base_url),
      identity: normalizedIdentity,
      discovery: readCachedDiscovery(parsed.discovery, normalizedIdentity),
    };
  } catch {
    return null;
  }
}

/** Persist only public reconnect coordinates, never credentials. */
export function writePersistedConnection(
  connection: PersistedConnection,
  backingStorage: SyncStorage = defaultStorage(),
): void {
  if (!connection.identity.server_id.trim() || connection.identity.server_id.length > 128) {
    throw new Error('Invalid Reader server identity.');
  }
  const baseUrl = normalizeReaderBaseUrl(connection.base_url);
  const apiBaseUrl = normalizeReaderBaseUrl(connection.identity.api_base_url);
  backingStorage.setItem(PERSISTED_CONNECTION_KEY, JSON.stringify({
    base_url: baseUrl,
    discovery: connection.discovery ? {
      api_base_url: normalizeReaderBaseUrl(connection.discovery.api_base_url),
      protocol_version: 2,
      server_id: connection.identity.server_id.trim(),
    } : undefined,
    identity: {
      server_id: connection.identity.server_id.trim(),
      api_base_url: apiBaseUrl,
    },
  }));
}

export function clearPersistedConnection(backingStorage: SyncStorage = defaultStorage()): void {
  backingStorage.removeItem(PERSISTED_CONNECTION_KEY);
}

export function persistedConnectionStorageKey(): string {
  return PERSISTED_CONNECTION_KEY;
}

export type { SyncStorage };

export function readServerToken(baseUrl: string): string | null {
  return secretStorage().getItem(`reader.server-token.${normalizeReaderBaseUrl(baseUrl)}`);
}
export function writeServerToken(baseUrl: string, token: string): void {
  secretStorage().setItem(`reader.server-token.${normalizeReaderBaseUrl(baseUrl)}`, token);
}
/** Only recover the old public address; Supabase credentials are never migrated. */
export function readLegacyServerAddress(): string | null {
  try {
    const raw = defaultStorage().getItem('reader.connection.v1');
    const value = raw ? JSON.parse(raw) : null;
    return typeof value?.base_url === 'string' ? normalizeReaderBaseUrl(value.base_url) : null;
  } catch { return null; }
}
