import { WEB_STORAGE_BOOTSTRAP_SOURCE } from './translationRuntimeSources.generated';

export const WEB_STORAGE_CLEANUP_INTERVAL_MS = 60_000;

export function createWebStorageScript(): string {
  return `(${WEB_STORAGE_BOOTSTRAP_SOURCE})(${WEB_STORAGE_CLEANUP_INTERVAL_MS});true;`;
}

/** Serialized at build time, never through Hermes Function#toString. */
// eslint-disable-next-line @typescript-eslint/no-unused-vars
function webStorageBootstrap(intervalMs: number) {
  const host = window as Window & { __readerWebStorage?: { sweep: () => Promise<void> } };
  // Only the top-level HTTP(S) document owns this policy. Opaque documents and
  // embedded third-party frames are excluded; blocked storage is harmless.
  if (window.top !== window || !/^https?:$/.test(location.protocol)) return;
  if (host.__readerWebStorage) {
    void host.__readerWebStorage.sweep();
    return;
  }
  let storage: CacheStorage;
  try {
    storage = window.caches;
    if (!storage) return;
  } catch { return; }

  let pending: Promise<void> | undefined;
  const sweep = (): Promise<void> => {
    if (pending) return pending;
    // Cache API response bodies are disposable. Leave cookies, DOM storage,
    // IndexedDB and Service Worker registrations intact for login/site behavior.
    pending = (async () => {
      try {
        for (const name of await storage.keys()) {
          try {
            const cache = await storage.open(name);
            const requests = await cache.keys();
            // Preserve the container: workers may keep a Cache handle alive.
            // Deleting its name can leave an invisible, still-writable orphan.
            // Bound concurrent metadata operations without reading response bodies.
            for (let offset = 0; offset < requests.length; offset += 16) {
              await Promise.all(requests.slice(offset, offset + 16).map(async request => {
                try { await cache.delete(request, { ignoreVary: true }); } catch { /* Retry next sweep. */ }
              }));
            }
          } catch { /* Retry on the next sweep. */ }
        }
      } catch { /* Storage access can be revoked while a document is alive. */ }
    })().finally(() => { pending = undefined; });
    return pending;
  };

  let timer: ReturnType<typeof setInterval> | undefined;
  const stop = () => {
    if (timer !== undefined) clearInterval(timer);
    timer = undefined;
  };
  const resume = () => {
    stop();
    void sweep();
    if (document.visibilityState !== 'hidden') timer = setInterval(() => { void sweep(); }, intervalMs);
  };
  host.__readerWebStorage = { sweep };
  document.addEventListener('visibilitychange', resume);
  window.addEventListener('pageshow', resume);
  window.addEventListener('pagehide', () => { stop(); void sweep(); });
  resume();
}
