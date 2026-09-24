import { expect, test } from '@playwright/test';
import { buildSync } from 'esbuild';
import { createServer } from 'node:http';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../..', import.meta.url));
const bundle = buildSync({ entryPoints: [`${root}/domain/webStorage.ts`], bundle: true, write: false, platform: 'node', format: 'esm' }).outputFiles[0].text;
const { createWebStorageScript, WEB_STORAGE_CLEANUP_INTERVAL_MS } = await import(`data:text/javascript;base64,${Buffer.from(bundle).toString('base64')}`);
const script = createWebStorageScript();
let server;
let origin;
test.beforeAll(async () => {
  server = createServer((req, res) => {
    if (req.url === '/sw.js') {
      res.writeHead(200, { 'Content-Type': 'application/javascript' });
      res.end(`let retainedCache; self.addEventListener('install', () => self.skipWaiting());
        self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
        self.addEventListener('message', e => e.waitUntil((async () => {
          const cache = retainedCache ||= await caches.open('worker-resources');
          await cache.put('/worker-resource', new Response('disposable'));
          e.ports[0].postMessage('stored');
        })()));`);
    } else {
      res.writeHead(200, { 'Content-Type': 'text/html' });
      res.end('<!doctype html><title>Storage policy fixture</title><p>Online reading</p>');
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${server.address().port}`;
});
test.afterAll(async () => { await new Promise(resolve => server.close(resolve)); });

async function seed(page, name = 'resources') {
  await page.evaluate(async name => {
    await (await caches.open(name)).put('/asset', new Response('x'.repeat(1024 * 1024)));
  }, name);
}
const entryCount = page => page.evaluate(async () => {
  const counts = await Promise.all((await caches.keys()).map(async name => (await (await caches.open(name)).keys()).length));
  return counts.reduce((total, count) => total + count, 0);
});

test('removes existing site caches and preserves persistent login stores across reload', async ({ page, context }) => {
  await page.goto(origin);
  await context.addCookies([{ name: 'login_fixture', value: 'keep', url: origin, httpOnly: true }]);
  await page.evaluate(async () => {
    localStorage.setItem('login_fixture', 'keep');
    sessionStorage.setItem('login_fixture', 'keep');
    const db = await new Promise((resolve, reject) => {
      const request = indexedDB.open('login_fixture', 1);
      request.onupgradeneeded = () => request.result.createObjectStore('session');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    await new Promise((resolve, reject) => {
      const tx = db.transaction('session', 'readwrite');
      tx.objectStore('session').put('keep', 'session');
      tx.oncomplete = resolve; tx.onerror = () => reject(tx.error);
    });
    db.close();
  });
  await seed(page, 'old-version');
  await seed(page, 'current-version');
  await page.evaluate(script);
  await expect.poll(() => entryCount(page)).toBe(0);
  await page.reload();
  expect((await context.cookies()).find(c => c.name === 'login_fixture')?.value).toBe('keep');
  expect(await page.evaluate(() => [localStorage.getItem('login_fixture'), sessionStorage.getItem('login_fixture')])).toEqual(['keep', 'keep']);
  expect(await page.evaluate(async () => {
    const db = await new Promise(resolve => { const r = indexedDB.open('login_fixture', 1); r.onsuccess = () => resolve(r.result); });
    const value = await new Promise(resolve => { const r = db.transaction('session').objectStore('session').get('session'); r.onsuccess = () => resolve(r.result); });
    db.close(); return value;
  })).toBe('keep');
});

test('cleans worker-created responses again without unregistering the worker', async ({ page }) => {
  await page.goto(origin);
  await page.evaluate(async () => { await navigator.serviceWorker.register('/sw.js'); await navigator.serviceWorker.ready; });
  await page.clock.install();
  await page.evaluate(script);
  await page.evaluate(async () => {
    const worker = (await navigator.serviceWorker.ready).active;
    await new Promise(resolve => {
      const channel = new MessageChannel(); channel.port1.onmessage = resolve;
      worker.postMessage('store', [channel.port2]);
    });
  });
  expect(await entryCount(page)).toBe(1);
  await page.clock.fastForward(WEB_STORAGE_CLEANUP_INTERVAL_MS);
  await expect.poll(() => entryCount(page)).toBe(0);
  // The worker retains its original Cache object. Subsequent writes must remain
  // visible to cleanup rather than accumulating in an orphaned deleted cache.
  await page.evaluate(async () => {
    const worker = (await navigator.serviceWorker.ready).active;
    await new Promise(resolve => {
      const channel = new MessageChannel(); channel.port1.onmessage = resolve;
      worker.postMessage('store', [channel.port2]);
    });
  });
  expect(await entryCount(page)).toBe(1);
  await page.clock.fastForward(WEB_STORAGE_CLEANUP_INTERVAL_MS);
  await expect.poll(() => entryCount(page)).toBe(0);
  expect(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length)).toBe(1);
});

test('duplicate injection coalesces work, and pagehide/pageshow resume cleanup', async ({ page }) => {
  await page.goto(origin);
  await page.clock.install();
  await page.evaluate(() => {
    const keys = caches.keys.bind(caches);
    window.keyCalls = 0;
    caches.keys = async () => { window.keyCalls++; return keys(); };
  });
  await page.evaluate(script);
  await page.evaluate(script);
  await page.evaluate(() => window.__readerWebStorage.sweep());
  const before = await page.evaluate(() => window.keyCalls);
  await page.clock.fastForward(WEB_STORAGE_CLEANUP_INTERVAL_MS);
  await expect.poll(() => page.evaluate(() => window.keyCalls)).toBe(before + 1);
  await seed(page);
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pagehide')));
  await expect.poll(() => entryCount(page)).toBe(0);
  await seed(page);
  await page.clock.fastForward(WEB_STORAGE_CLEANUP_INTERVAL_MS * 2);
  expect(await entryCount(page)).toBe(1);
  await page.evaluate(() => window.dispatchEvent(new PageTransitionEvent('pageshow')));
  await expect.poll(() => entryCount(page)).toBe(0);
});

test('storage failures do not break the page and a failed deletion is retried', async ({ page }) => {
  await page.goto(origin);
  await seed(page, 'retry');
  await seed(page, 'other');
  await page.evaluate(() => {
    const remove = Cache.prototype.delete;
    let fail = true;
    Cache.prototype.delete = function (...args) {
      if (fail) { fail = false; return Promise.reject(new Error('transient')); }
      return remove.apply(this, args);
    };
  });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.evaluate(script);
  await expect.poll(() => entryCount(page)).toBe(1);
  await page.evaluate(() => window.__readerWebStorage.sweep());
  expect(await entryCount(page)).toBe(0);
  await page.evaluate(() => { caches.keys = () => { throw new Error('blocked'); }; });
  await page.evaluate(() => window.__readerWebStorage.sweep());
  expect(errors).toEqual([]);
  await expect(page.locator('p')).toHaveText('Online reading');
});

test('opaque documents and unavailable storage are safe no-ops', async ({ page }) => {
  await page.goto('about:blank');
  await page.evaluate(script);
  expect(await page.evaluate(() => Boolean(window.__readerWebStorage))).toBe(false);
  await page.goto(origin);
  await page.evaluate(() => Object.defineProperty(window, 'caches', { get() { throw new Error('unavailable'); } }));
  await page.evaluate(script);
  expect(await page.evaluate(() => Boolean(window.__readerWebStorage))).toBe(false);
});
